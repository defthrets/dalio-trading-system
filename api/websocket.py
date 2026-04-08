"""
Dalios -- WebSocket Manager & CLI Command Parser
ConnectionManager for real-time broadcasts, _run_cmd for CLI commands.
"""

import re as _re
import asyncio
import time as _time
from datetime import datetime
from typing import Optional

from fastapi import WebSocket
from loguru import logger

from api.state import (
    STATE, WATCHLIST, _save_watchlist, _WATCHLIST_LOCK,
    _PAPER_LOCK, TRADING_MODE,
    _db_save_trade, _db_save_equity_snapshot,
)
from api.portfolio import (
    PAPER, PAPER_STARTING_CASH, _save_paper_state, _save_paper_config,
    _calculate_position_size, _RISK_MAX_POS_SIZE_PCT,
)
from api.scanners import (
    ASX_TICKERS, COMMODITY_TICKERS,
    _ASSET_META, _scanner_cache, _CACHE_TTL,
    _live_price, _prices_for_positions,
    _scan_yfinance,
)
from api.signals import (
    QUADRANT_PLAYBOOK, ASSET_CLASS_MAP,
    _gen_signals, _gen_opportunities, _gen_quadrant_data,
    dalio_analyse_trade,
)


# ─────────────────────────────────────────────
# WebSocket Manager
# ─────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info(f"WebSocket connected. Active connections: {len(self.active)}")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        logger.info(f"WebSocket disconnected. Active connections: {len(self.active)}")

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


WS_MANAGER = ConnectionManager()


# ─────────────────────────────────────────────
# CLI Command Parser
# ─────────────────────────────────────────────

async def _run_cmd(message: str) -> dict:
    """Core command dispatcher -- shared by /api/ai/chat and /api/cmd."""
    global PAPER_STARTING_CASH
    msg_lower = message.strip().lower()

    # ── help ──────────────────────────────────────────────────────────────
    if msg_lower in ("help", "?", "commands"):
        return {"type":"help","message":(
            "Dalios CLI Commands\n"
            "-------------------\n"
            "  buy <qty> <ticker>              -- Paper buy  (e.g. buy 10 BHP.AX)\n"
            "  sell <qty> <ticker>             -- Paper sell (e.g. sell 5 CBA.AX)\n"
            "  close <ticker>                  -- Close open position\n"
            "  portfolio                       -- Full portfolio summary\n"
            "  positions                       -- Open positions detail\n"
            "  history [n]                     -- Last N trades (default 10)\n"
            "  quote <ticker>                  -- Live price lookup\n"
            "  watchlist                       -- Show watchlist\n"
            "  watchlist add <ticker>          -- Add to watchlist\n"
            "  watchlist remove <ticker>       -- Remove from watchlist\n"
            "  scanner asx                     -- ASX scanner data\n"
            "  scanner commodities             -- Commodities scanner data\n"
            "  suggest [n]                     -- Top N trade opportunities\n"
            "  signals                         -- Top 5 active signals\n"
            "  analyse <ticker>                -- Dalio All Weather analysis\n"
            "  risk                            -- Portfolio risk assessment\n"
            "  quadrant                        -- Current economic regime\n"
            "  reset [<cash>]                  -- Reset paper portfolio\n"
            "  set cash <amount>               -- Update starting cash\n"
            "  help                            -- This list")}

    # ── portfolio / positions ──────────────────────────────────────────────
    if msg_lower in ("portfolio","portfolio summary","show portfolio"):
        tickers = list(PAPER.positions.keys())
        prices  = await _prices_for_positions(tickers) if tickers else {}
        total   = PAPER.total_value(prices)
        pnl     = total - PAPER_STARTING_CASH
        pnl_pct = (pnl / PAPER_STARTING_CASH) * 100 if PAPER_STARTING_CASH else 0
        pos_lines = [
            f"  {t}: {p['side']} {p['qty']} @ ${p['entry_price']:.4f}  |  "
            f"now ${prices.get(t, p['entry_price']):.4f}  |  "
            f"P&L {((prices.get(t,p['entry_price'])-p['entry_price'])/p['entry_price']*100):+.2f}%"
            for t,p in PAPER.positions.items()
        ]
        return {"type":"portfolio","message":(
            f"Paper Portfolio\n"
            f"  Cash:        ${PAPER.cash:,.2f}\n"
            f"  Total NAV:   ${total:,.2f}\n"
            f"  P&L:         ${pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
            f"  Positions ({len(PAPER.positions)}):\n" +
            ("\n".join(pos_lines) if pos_lines else "  None")),
            "data":{"cash":round(PAPER.cash,2),"total_value":round(total,2),"pnl":round(pnl,2),
                    "pnl_pct":round(pnl_pct,2),"positions":[
                        {"ticker":t,"side":p["side"],"qty":p["qty"],
                         "entry_price":p["entry_price"],"current_price":prices.get(t,p["entry_price"])}
                        for t,p in PAPER.positions.items()]}}

    if msg_lower == "positions":
        tickers = list(PAPER.positions.keys())
        if not tickers:
            return {"type":"positions","message":"No open positions.","data":{"positions":[]}}
        prices  = await _prices_for_positions(tickers)
        rows = []
        for t,p in PAPER.positions.items():
            cur = prices.get(t, p["entry_price"])
            pnl = (cur - p["entry_price"]) * p["qty"] * (1 if p["side"] in ("BUY","LONG") else -1)
            rows.append({"ticker":t,"side":p["side"],"qty":p["qty"],
                         "entry_price":p["entry_price"],"current_price":cur,"pnl":round(pnl,2)})
        lines = [f"  {r['ticker']}: {r['side']} {r['qty']} entry ${r['entry_price']:.4f} cur ${r['current_price']:.4f} P&L ${r['pnl']:+,.2f}" for r in rows]
        return {"type":"positions","message":"Open Positions:\n"+"\n".join(lines),"data":{"positions":rows}}

    # ── history [n] ───────────────────────────────────────────────────────
    hist_m = _re.match(r"^history(?:\s+(\d+))?$", msg_lower)
    if hist_m:
        n    = int(hist_m.group(1) or 10)
        recent = PAPER.history[-n:][::-1]
        if not recent:
            return {"type":"history","message":"No trade history yet.","data":{"trades":[]}}
        lines = [f"  #{t.get('id','?')} {t['side']} {t['qty']} {t['ticker']} @ ${t.get('entry_price', t.get('exit_price',0)):.4f}" for t in recent]
        return {"type":"history","message":f"Last {len(recent)} trades:\n"+"\n".join(lines),"data":{"trades":recent}}

    # ── quote <ticker> ────────────────────────────────────────────────────
    quote_m = _re.match(r"^quote\s+(\S+)$", msg_lower)
    if quote_m:
        tkr   = quote_m.group(1).upper()
        price = await _live_price(tkr)
        if price is None:
            return {"type":"error","message":f"Cannot fetch price for {tkr}. Check the ticker symbol."}
        return {"type":"quote","message":f"{tkr}: ${float(price):,.4f}","data":{"ticker":tkr,"price":float(price)}}

    # ── close <ticker> ────────────────────────────────────────────────────
    close_m = _re.match(r"^close\s+(\S+)$", msg_lower)
    if close_m:
        tkr = close_m.group(1).upper()
        if tkr not in PAPER.positions:
            return {"type":"error","message":f"No open position for {tkr}."}
        price = await _live_price(tkr)
        if price is None:
            return {"type":"error","message":f"Cannot fetch price for {tkr} to close position."}
        async with _PAPER_LOCK:
            qty = PAPER.positions[tkr]["qty"]
            result = PAPER.place_order(tkr, "SELL", qty, float(price))
            _save_paper_state()
            # DB: persist trade
            if PAPER.history:
                _db_save_trade(PAPER.history[0])
        await WS_MANAGER.broadcast({"type":"PAPER_CLOSE","data":result})
        pnl = result.get("pnl", PAPER.history[0]["pnl"] if PAPER.history else 0)
        return {"type":"close","message":(
            f"Closed {tkr}  {qty} @ ${float(price):.4f}\n  Cash: ${PAPER.cash:,.2f}"),
            "data":result}

    # ── watchlist ─────────────────────────────────────────────────────────
    if msg_lower == "watchlist":
        if not WATCHLIST:
            return {"type":"watchlist","message":"Watchlist is empty.","data":{"tickers":[]}}
        return {"type":"watchlist","message":"Watchlist:\n  "+"\n  ".join(WATCHLIST),"data":{"tickers":list(WATCHLIST)}}

    wl_add_m = _re.match(r"^watchlist add\s+(\S+)$", msg_lower)
    if wl_add_m:
        tkr = wl_add_m.group(1).upper()
        async with _WATCHLIST_LOCK:
            if tkr not in WATCHLIST:
                WATCHLIST.append(tkr)
                _save_watchlist(WATCHLIST)
        return {"type":"watchlist","message":f"Added {tkr} to watchlist.","data":{"tickers":list(WATCHLIST)}}

    wl_rem_m = _re.match(r"^watchlist remove\s+(\S+)$", msg_lower)
    if wl_rem_m:
        tkr = wl_rem_m.group(1).upper()
        async with _WATCHLIST_LOCK:
            if tkr in WATCHLIST:
                WATCHLIST.remove(tkr)
                _save_watchlist(WATCHLIST)
                return {"type":"watchlist","message":f"Removed {tkr} from watchlist.","data":{"tickers":list(WATCHLIST)}}
        return {"type":"watchlist","message":f"{tkr} not in watchlist.","data":{"tickers":list(WATCHLIST)}}

    # ── scanner <market> ──────────────────────────────────────────────────
    scanner_m = _re.match(r"^scanner\s+(asx|commodities)$", msg_lower)
    if scanner_m:
        market = scanner_m.group(1)
        ticker_map = {"asx": ASX_TICKERS, "commodities": COMMODITY_TICKERS}
        cached = _scanner_cache.get(market)
        if cached and (_time.time() - cached["ts"]) < _CACHE_TTL:
            all_rows = cached["rows"]
        else:
            tickers = ticker_map[market]
            all_rows = await _scan_yfinance(tickers, market)
            good = [r for r in all_rows if r["price"] > 0]
            if good: all_rows = good
            _scanner_cache[market] = {"ts": _time.time(), "rows": all_rows}
        top    = all_rows[:10]
        gainers = sorted(top, key=lambda r: r.get("change_pct",0), reverse=True)[:5]
        losers  = sorted(top, key=lambda r: r.get("change_pct",0))[:3]
        def _fmt_row(r):
            chg = r.get("change_pct",0)
            sign = "+" if chg >= 0 else ""
            return f"  {r['ticker']:<12} ${r.get('price',0):>10,.4f}  {sign}{chg:.2f}%"
        lines = (["Top Gainers:"] + [_fmt_row(r) for r in gainers] +
                 ["\nTop Losers:"]  + [_fmt_row(r) for r in losers])
        return {"type":"scanner","market":market,"message":f"{market.upper()} Scanner (top 10):\n"+"\n".join(lines),"data":{"rows":top}}

    # ── set cash <amount> ─────────────────────────────────────────────────
    set_cash_m = _re.match(r"^set cash\s+([\d,]+(?:\.\d+)?)$", msg_lower)
    if set_cash_m:
        import api.portfolio as _portfolio_mod
        amount = float(set_cash_m.group(1).replace(",",""))
        if amount < 1:
            return {"type":"error","message":"Starting cash must be at least $1."}
        _portfolio_mod.PAPER_STARTING_CASH = amount
        _save_paper_config()
        return {"type":"config","message":f"Starting cash set to ${amount:,.2f}. Reset portfolio to apply.",
                "data":{"starting_cash":amount}}

    # ── reset [<cash>] ────────────────────────────────────────────────────
    reset_m = _re.match(r"^reset(?:\s+([\d,]+(?:\.\d+)?))?$", msg_lower)
    if reset_m:
        import api.portfolio as _portfolio_mod
        if reset_m.group(1):
            _portfolio_mod.PAPER_STARTING_CASH = float(reset_m.group(1).replace(",",""))
            _save_paper_config()
        starting = _portfolio_mod.PAPER_STARTING_CASH
        async with _PAPER_LOCK:
            PAPER.cash       = starting
            PAPER.positions  = {}
            PAPER.history    = []
            PAPER.equity_history = []
            PAPER.order_id   = 0
            _save_paper_state()
        STATE.add_alert("PAPER", f"Portfolio reset to ${starting:,.2f} via CLI", "INFO")
        await WS_MANAGER.broadcast({"type":"PAPER_RESET","data":{"starting_cash":starting}})
        return {"type":"reset","message":f"Portfolio reset to ${starting:,.2f} starting cash.",
                "data":{"starting_cash":starting}}

    # ── quadrant ──────────────────────────────────────────────────────────
    if msg_lower in ("quadrant","regime","macro","current quadrant"):
        qdata    = STATE.last_quadrant or _gen_quadrant_data()
        quadrant = qdata.get("quadrant","rising_growth")
        pb       = QUADRANT_PLAYBOOK.get(quadrant, QUADRANT_PLAYBOOK["rising_growth"])
        return {"type":"quadrant","message":(
            f"Quadrant: {qdata.get('label','').upper()}\n\n{pb['narrative']}\n\n"
            f"  Favour: {', '.join((pb['strong_buy']+pb['buy'])[:4]).replace('_',' ')}\n"
            f"  Avoid:  {', '.join(pb['avoid']).replace('_',' ')}"),
            "data": qdata}

    # ── suggest / opportunities ───────────────────────────────────────────
    suggest_m = _re.match(r"^(suggest|opportunities?|opps?)(?:\s+(\d+))?$", msg_lower)
    if suggest_m:
        n    = int(suggest_m.group(2) or 8)
        opps = await _gen_opportunities(n)
        if not opps:
            return {"type":"suggest","message":"No opportunities found. Try loading the scanner tabs first to populate the data cache.","data":{"opportunities":[]}}
        lines = []
        for i, o in enumerate(opps, 1):
            sign = "+" if o["change_pct"] >= 0 else ""
            lines.append(
                f"{i:>2}. [{o['action']:<5}] {o['ticker']:<14} ${o['price']:>12,.4f}  "
                f"{sign}{o['change_pct']:.2f}%  RSI:{o['rsi']:.0f}  "
                f"Score:{o['score']:.0f}  Fit:{o['quadrant_fit'].upper()}\n"
                f"      {o['reasoning'][0]}\n"
                f"      SL ${o['stop_loss']:,.4f}  TP ${o['take_profit']:,.4f}  R:R {o['rr_ratio']:.1f}x"
            )
        header = (f"Top {len(opps)} Opportunities -- Regime: {opps[0].get('regime_label','').upper()}\n"
                  f"{'---'*24}\n")
        return {"type":"suggest","message": header + "\n\n".join(lines),
                "data":{"opportunities": opps, "quadrant": opps[0].get("quadrant","")}}

    # ── signals ───────────────────────────────────────────────────────────
    if msg_lower in ("signals","top signals","best signals"):
        sigs = await _gen_signals(12)
        top5 = sigs[:5]
        lines = [f"  {s['ticker']}: {s['action']} | conf {s['confidence']:.0f}% | RSI {s['rsi']}" for s in top5]
        return {"type":"signals","message":"Top 5 Signals:\n"+"\n".join(lines),"data":top5}

    # ── risk ──────────────────────────────────────────────────────────────
    if msg_lower in ("risk","risk assessment","portfolio risk","how am i doing"):
        tickers = list(PAPER.positions.keys())
        prices  = await _prices_for_positions(tickers) if tickers else {}
        total   = PAPER.total_value(prices)
        exc     = [ASSET_CLASS_MAP.get(t,"equities") for t in PAPER.positions]
        n_pos   = len(PAPER.positions)
        cash_pct = (PAPER.cash / total * 100) if total > 0 else 100.0
        _AW = {"equities":0.30,"long_bonds":0.40,"gold":0.15,"commodities":0.075,"tips":0.075}
        cc = {c: exc.count(c) for c in set(exc)}; tot = n_pos or 1
        dev = sum(abs(cc.get(c,0)/tot - v) for c,v in _AW.items())
        aw  = max(0, min(100, int(100 - dev*50)))
        return {"type":"risk","message":(
            f"Dalio Risk Assessment\n"
            f"  Positions:         {n_pos}/15 (Holy Grail target)\n"
            f"  Asset classes:     {len(set(exc))} ({', '.join(set(exc)).replace('_',' ')})\n"
            f"  Cash reserve:      {cash_pct:.1f}%\n"
            f"  All Weather Score: {aw}/100\n"
            f"  Holy Grail met:    {'YES' if n_pos>=12 else 'NO -- add uncorrelated assets'}\n\n"
            f"Rule: 15 uncorrelated streams reduce risk without reducing return."),
            "data":{"n_positions":n_pos,"all_weather_score":aw,"cash_pct":round(cash_pct,1)}}

    # ── analyse <ticker> ──────────────────────────────────────────────────
    analyse_m = _re.match(r"^(analyse|analyze|analysis)\s+(\S+)$", msg_lower)
    if analyse_m:
        tkr   = analyse_m.group(2).upper()
        qdata = STATE.last_quadrant or _gen_quadrant_data()
        res   = dalio_analyse_trade(tkr,"BUY",qdata.get("quadrant","rising_growth"),PAPER.cash,PAPER.positions,await _gen_signals(12))
        return {"type":"analyse","message":(
            f"Dalio Analysis: {tkr}\n  Fit: {res['fit_score']}/100 -- {res['fit_label']}\n"
            f"  Asset Class: {res['asset_class'].replace('_',' ').title()}\n"
            f"  All Weather: {res['all_weather_score']}/100\n"
            f"  {res['recommendation']}\n"
            f"  Risks: {', '.join(res['risk_flags']) if res['risk_flags'] else 'None'}\n"
            f"\n" + "\n".join(f"  * {r}" for r in res["reasoning"])),"data":res}

    # ── buy / sell ────────────────────────────────────────────────────────
    order_m = _re.match(r"^(buy|sell)\s+([\d.]+)\s+(\S+)", msg_lower)
    if order_m:
        side  = order_m.group(1).upper()
        qty   = float(order_m.group(2))
        tkr   = order_m.group(3).upper()
        try:
            price = await _live_price(tkr)
            if price is None: raise ValueError(f"Cannot determine price for {tkr}")
            # Auto-compute SL/TP for BUY orders (ATR-based defaults)
            _sl_val = None
            _tp_val = None
            if side == "BUY":
                _sl_offset = float(price) * 0.025  # 2.5% default stop
                _tp_offset = float(price) * 0.05   # 5% default target
                _sl_val = round(float(price) - _sl_offset, 4)
                _tp_val = round(float(price) + _tp_offset, 4)
            async with _PAPER_LOCK:
                # -- Position sizing enforcement --
                _tks_pre = list(set(list(PAPER.positions.keys()) + [tkr]))
                _prc_pre = await _prices_for_positions(_tks_pre) if _tks_pre else {}
                _prc_pre[tkr] = float(price)
                sizing = _calculate_position_size(tkr, float(price), side, PAPER, _prc_pre)
                max_allowed = sizing["max_allowed_qty"]
                original_qty = qty
                cap_msg = ""
                if side == "BUY" and qty > max_allowed:
                    if max_allowed <= 0:
                        raise ValueError(
                            f"Position sizing blocked: max allowed qty is 0 "
                            f"(max {_RISK_MAX_POS_SIZE_PCT}% per position)")
                    qty = max_allowed
                    cap_msg = f"\n  [RISK] Qty capped from {original_qty} to {qty:.8g} (max {_RISK_MAX_POS_SIZE_PCT}% per position)"

                result = PAPER.place_order(tkr, side, qty, float(price),
                                           stop_loss=_sl_val, take_profit=_tp_val)
                _tks = list(PAPER.positions.keys())
                _prc = await _prices_for_positions(_tks) if _tks else {}
                _eq_val_cli = PAPER.total_value(_prc)
                PAPER.equity_history.append({"t":datetime.utcnow().isoformat(),"v":_eq_val_cli})
                PAPER.equity_history = PAPER.equity_history[-2000:]
                _save_paper_state()
                # DB: persist trade (SELL) + equity snapshot
                if side == "SELL" and PAPER.history:
                    _db_save_trade(PAPER.history[0])
                _db_save_equity_snapshot(_eq_val_cli)
            await WS_MANAGER.broadcast({"type":"PAPER_ORDER","data":result})
            return {"type":"order","message":(
                f"Order placed: {side} {qty} {tkr} @ ${price:.4f}\n"
                f"  ID: #{result['order_id']} | Cost: ${qty*price:,.2f} | Cash left: ${PAPER.cash:,.2f}"
                f"{cap_msg}"),
                "data":result}
        except ValueError as exc:
            return {"type":"error","message":f"Order failed: {exc}"}

    # ── free-form fallback ─────────────────────────────────────────────────
    qdata = STATE.last_quadrant or _gen_quadrant_data()
    pb    = QUADRANT_PLAYBOOK.get(qdata.get("quadrant","rising_growth"), QUADRANT_PLAYBOOK["rising_growth"])
    tks   = list(PAPER.positions.keys())
    prc   = await _prices_for_positions(tks) if tks else {}
    total = PAPER.total_value(prc)
    return {"type":"freeform","message":(
        f"Dalios AI (type 'help' for commands)\n\n"
        f"You said: \"{message.strip()}\"\n\n"
        f"Current regime: {qdata.get('label','').upper()}\n"
        f"Portfolio: ${total:,.2f} | Cash: ${PAPER.cash:,.2f} | Positions: {len(PAPER.positions)}\n\n"
        f"Dalio says: {pb['narrative'][:160]}...")}
