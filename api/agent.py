"""
Dalios -- Autonomous Agent
Agent config persistence, autonomous cycle loop, SL/TP monitoring.
"""

import asyncio
import json
from datetime import datetime
from typing import Optional

from loguru import logger

from api.state import (
    STATE, AGENT_CONFIG_FILE,
    _PAPER_LOCK, SETTINGS_AVAILABLE,
    _db_save_trade, _db_save_equity_snapshot,
)
from api.portfolio import (
    PAPER, _save_paper_state, _calculate_position_size,
    PAPER_STARTING_CASH,
)
from api.scanners import _live_price, _prices_for_positions


# ── Agent auto-trading config persistence ──────────────────────────

_AGENT_CONFIG_DEFAULTS = {
    "enabled": False,
    "interval_seconds": 300,
    "min_confidence": 60,
}


def _load_agent_config() -> dict:
    """Load agent auto-trading config from disk, or return defaults."""
    try:
        if AGENT_CONFIG_FILE.exists():
            with open(AGENT_CONFIG_FILE) as f:
                cfg = json.load(f)
            # Merge with defaults so new keys are always present
            merged = {**_AGENT_CONFIG_DEFAULTS, **cfg}
            return merged
    except Exception as exc:
        logger.warning(f"Failed to load agent config: {exc}")
    return dict(_AGENT_CONFIG_DEFAULTS)


def _save_agent_config(cfg: dict) -> None:
    """Persist agent auto-trading config to disk."""
    try:
        with open(AGENT_CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as exc:
        logger.warning(f"Failed to save agent config: {exc}")


AGENT_CONFIG = _load_agent_config()


# ── SL/TP Monitoring ──────────────────────────────────────────────

async def _check_stop_loss_take_profit():
    """Iterate open paper positions and auto-close any that hit SL or TP."""
    # Deferred import to avoid circular dependency
    from api.websocket import WS_MANAGER

    if not PAPER.positions:
        return
    # Snapshot tickers to avoid dict-changed-during-iteration
    tickers = list(PAPER.positions.keys())
    for ticker in tickers:
        pos = PAPER.positions.get(ticker)
        if pos is None:
            continue
        sl = pos.get("stop_loss")
        tp = pos.get("take_profit")
        if sl is None and tp is None:
            continue
        price = await _live_price(ticker)
        if price is None:
            continue
        price = float(price)
        triggered = None
        if sl is not None and price <= sl:
            triggered = "STOP-LOSS"
        elif tp is not None and price >= tp:
            triggered = "TAKE-PROFIT"
        if triggered is None:
            continue
        # Auto-close the position
        async with _PAPER_LOCK:
            # Re-check after acquiring lock (may have been closed already)
            if ticker not in PAPER.positions:
                continue
            qty = PAPER.positions[ticker]["qty"]
            try:
                result = PAPER.place_order(ticker, "SELL", qty, price)
            except ValueError as exc:
                logger.warning(f"SL/TP auto-close failed for {ticker}: {exc}")
                continue
            # Equity snapshot + persist
            _tickers = list(PAPER.positions.keys())
            _prices  = await _prices_for_positions(_tickers) if _tickers else {}
            current_equity = PAPER.total_value(_prices)
            PAPER.equity_history.append({"t": datetime.utcnow().isoformat(), "v": current_equity})
            PAPER.equity_history = PAPER.equity_history[-2000:]
            _save_paper_state()
            # DB: persist trade + equity snapshot
            if PAPER.history:
                _db_save_trade(PAPER.history[0])
            _db_save_equity_snapshot(current_equity)
        # Alert + broadcast
        pnl = PAPER.history[0]["pnl"] if PAPER.history else 0
        level_str = f"{triggered} hit"
        STATE.add_alert("SL/TP",
            f"{level_str}: auto-closed {ticker} {qty:.4g} @ ${price:.4f} "
            f"(PnL ${pnl:+,.2f})", "WARNING")
        await WS_MANAGER.broadcast({
            "type": "PAPER_SL_TP",
            "data": {**result, "trigger": triggered, "pnl": pnl},
        })
        logger.info(f"{triggered} triggered for {ticker} @ ${price:.4f} -- position auto-closed")


async def _sl_tp_monitor_loop():
    """Background loop: check SL/TP every 30 seconds."""
    while True:
        try:
            await _check_stop_loss_take_profit()
        except Exception as exc:
            logger.warning(f"SL/TP monitor error: {exc}")
        await asyncio.sleep(30)


# ── Autonomous Agent Loop ──────────────────────────────────────────

_agent_last_cycle_time: Optional[str] = None
_agent_next_cycle_time: Optional[str] = None


async def _autonomous_agent_loop():
    """Background loop: scan markets, generate signals, auto-execute paper trades.

    Runs every AGENT_CONFIG['interval_seconds'] (default 300s / 5 min).
    Auto-trading is disabled by default -- user must enable via POST /api/agent/toggle.
    Only executes trades automatically in paper mode. In live mode, signals are
    logged but require manual execution.
    """
    global _agent_last_cycle_time, _agent_next_cycle_time

    # Brief initial delay to let startup finish
    await asyncio.sleep(10)
    logger.info("Autonomous agent loop started (waiting for enable)")

    while True:
        interval = AGENT_CONFIG.get("interval_seconds", 300)
        if not AGENT_CONFIG.get("enabled", False):
            _agent_next_cycle_time = None
            await asyncio.sleep(5)  # Check enable flag every 5s
            continue

        _agent_next_cycle_time = (
            datetime.utcnow().__class__.utcnow()
            .replace(microsecond=0)
            .isoformat()
        )

        try:
            await _run_autonomous_cycle()
        except Exception as exc:
            logger.error(f"Autonomous cycle error: {exc}")
            STATE.add_alert("AGENT", f"Autonomous cycle failed: {exc}", "ERROR")

        # Calculate next cycle time
        await asyncio.sleep(interval)


async def _run_autonomous_cycle():
    """Execute one autonomous scan/signal/trade cycle."""
    global _agent_last_cycle_time, _agent_next_cycle_time

    # Deferred imports to avoid circular dependency
    from api.signals import _gen_signals, _gen_opportunities, _gen_portfolio_health, _gen_quadrant_data
    from api.websocket import WS_MANAGER
    from api.state import TRADING_MODE

    cycle_start = datetime.utcnow()
    STATE.cycle_count += 1
    cycle_num = STATE.cycle_count

    logger.info(f"Autonomous cycle #{cycle_num} starting...")

    # 1. Refresh scanner data for all markets
    # market_scanner is a route function in server.py; we call scanners directly
    from api.scanners import (
        ASX_TICKERS, COMMODITY_TICKERS,
        _scan_yfinance, _scanner_cache,
    )
    import time as _time

    markets_refreshed = 0
    ticker_map = {"asx": ASX_TICKERS, "commodities": COMMODITY_TICKERS}
    for market in ("asx", "commodities"):
        try:
            tickers = ticker_map[market]
            rows = await _scan_yfinance(tickers, market)
            good = [r for r in rows if r["price"] > 0]
            if good:
                rows = good
            _scanner_cache[market] = {"ts": _time.time(), "rows": rows}
            markets_refreshed += 1
        except Exception as exc:
            logger.warning(f"Scanner refresh failed for {market}: {exc}")

    # 2. Generate signals
    signals = await _gen_signals(12)
    min_confidence = AGENT_CONFIG.get("min_confidence", 60)

    # 3. Filter signals by minimum confidence
    strong_signals = [s for s in signals if s.get("confidence", 0) >= min_confidence]
    buy_signals = [s for s in strong_signals if s.get("action") == "BUY"]
    sell_signals = [s for s in strong_signals if s.get("action") == "SELL"]

    trades_executed = 0
    trade_details = []

    # 4. Determine trading mode
    is_paper = TRADING_MODE.upper() != "LIVE"

    # 5. Process SELL signals -- close held positions
    for sig in sell_signals:
        ticker = sig["ticker"]
        if ticker not in PAPER.positions:
            continue  # No position to close

        if not is_paper:
            logger.info(
                f"[LIVE MODE] SELL signal for {ticker} (conf {sig['confidence']}%) "
                f"-- requires manual execution"
            )
            STATE.add_alert(
                "AGENT",
                f"SELL signal: {ticker} @ ${sig['price']:.4f} "
                f"(conf {sig['confidence']}%) -- manual execution required (live mode)",
                "WARNING",
            )
            continue

        # Auto-close in paper mode
        try:
            pos = PAPER.positions[ticker]
            qty = pos["qty"]
            price = await _live_price(ticker)
            if price is None:
                price = sig["price"]
            price = float(price)

            async with _PAPER_LOCK:
                if ticker not in PAPER.positions:
                    continue
                result = PAPER.place_order(ticker, "SELL", qty, price)
                _tickers = list(PAPER.positions.keys())
                _prices = await _prices_for_positions(_tickers) if _tickers else {}
                current_equity = PAPER.total_value(_prices)
                PAPER.equity_history.append({"t": datetime.utcnow().isoformat(), "v": current_equity})
                PAPER.equity_history = PAPER.equity_history[-2000:]
                _save_paper_state()
                if PAPER.history:
                    _db_save_trade(PAPER.history[0])
                _db_save_equity_snapshot(current_equity)

            pnl = result.get("pnl", PAPER.history[0]["pnl"] if PAPER.history else 0)
            trades_executed += 1
            trade_details.append(f"SELL {ticker} x{qty:.4g} @ ${price:.4f}")
            STATE.add_alert(
                "AGENT",
                f"Auto-closed {ticker} x{qty:.4g} @ ${price:.4f} (PnL ${pnl:+,.2f})",
                "INFO",
            )
            await WS_MANAGER.broadcast({"type": "PAPER_ORDER", "data": result})
            logger.info(f"Cycle #{cycle_num}: auto-closed {ticker} @ ${price:.4f}")

        except Exception as exc:
            logger.warning(f"Cycle #{cycle_num}: failed to close {ticker}: {exc}")

    # 6. Process BUY signals -- open new positions (paper mode only)
    for sig in buy_signals:
        ticker = sig["ticker"]
        # Skip if already holding this ticker
        if ticker in PAPER.positions:
            continue

        if not is_paper:
            logger.info(
                f"[LIVE MODE] BUY signal for {ticker} (conf {sig['confidence']}%) "
                f"-- requires manual execution"
            )
            STATE.add_alert(
                "AGENT",
                f"BUY signal: {ticker} @ ${sig['price']:.4f} "
                f"(conf {sig['confidence']}%) -- manual execution required (live mode)",
                "WARNING",
            )
            continue

        # Auto-execute in paper mode with position sizing
        try:
            price = await _live_price(ticker)
            if price is None:
                price = sig["price"]
            price = float(price)

            async with _PAPER_LOCK:
                # Position sizing check
                _tickers_pre = list(set(list(PAPER.positions.keys()) + [ticker]))
                _prices_pre = await _prices_for_positions(_tickers_pre) if _tickers_pre else {}
                _prices_pre[ticker] = price

                try:
                    sizing = _calculate_position_size(ticker, price, "BUY", PAPER, _prices_pre)
                except ValueError as e:
                    logger.info(f"Cycle #{cycle_num}: position sizing blocked {ticker}: {e}")
                    continue

                max_qty = sizing["max_allowed_qty"]
                if max_qty <= 0:
                    continue

                # Use signal's suggested position size (1-5% of portfolio)
                pos_size_pct = sig.get("position_size_pct", 2.0)
                total_value = PAPER.total_value(_prices_pre)
                target_value = total_value * (pos_size_pct / 100.0)
                target_qty = target_value / price if price > 0 else 0

                # Cap to max allowed
                qty = min(target_qty, max_qty)
                if qty <= 0:
                    continue

                # Round qty sensibly
                if price > 100:
                    qty = round(qty, 2)
                elif price > 1:
                    qty = round(qty, 4)
                else:
                    qty = round(qty, 6)

                if qty <= 0:
                    continue

                # Use signal's SL/TP
                sl = sig.get("stop_loss")
                tp = sig.get("take_profit")

                result = PAPER.place_order(ticker, "BUY", qty, price,
                                           stop_loss=sl, take_profit=tp)

                # Equity snapshot + persist
                _tickers_post = list(PAPER.positions.keys())
                _prices_post = await _prices_for_positions(_tickers_post) if _tickers_post else {}
                current_equity = PAPER.total_value(_prices_post)
                PAPER.equity_history.append({"t": datetime.utcnow().isoformat(), "v": current_equity})
                PAPER.equity_history = PAPER.equity_history[-2000:]
                _save_paper_state()
                _db_save_equity_snapshot(current_equity)

            trades_executed += 1
            trade_details.append(f"BUY {ticker} x{qty:.4g} @ ${price:.4f}")
            STATE.add_alert(
                "AGENT",
                f"Auto-bought {ticker} x{qty:.4g} @ ${price:.4f} "
                f"(conf {sig['confidence']}%, SL ${sl}, TP ${tp})",
                "INFO",
            )
            await WS_MANAGER.broadcast({"type": "PAPER_ORDER", "data": result})
            logger.info(f"Cycle #{cycle_num}: auto-bought {ticker} x{qty:.4g} @ ${price:.4f}")

        except Exception as exc:
            logger.warning(f"Cycle #{cycle_num}: failed to buy {ticker}: {exc}")

    # 7. Generate opportunities
    opportunities = await _gen_opportunities(8)

    # 8. Build cycle result
    health = _gen_portfolio_health()
    quadrant = _gen_quadrant_data()
    _agent_last_cycle_time = datetime.utcnow().isoformat()
    interval = AGENT_CONFIG.get("interval_seconds", 300)
    _agent_next_cycle_time = (
        datetime.utcnow().__class__.utcnow()
        .replace(microsecond=0)
        .isoformat()
    )

    result = {
        "type": "CYCLE_COMPLETE",
        "cycle": cycle_num,
        "autonomous": True,
        "quadrant": quadrant.get("quadrant", "unknown"),
        "signals_found": len(signals),
        "strong_signals": len(strong_signals),
        "trades_executed": trades_executed,
        "trade_details": trade_details,
        "top_signals": signals[:5],
        "opportunities": len(opportunities),
        "portfolio_health": health,
        "markets_refreshed": markets_refreshed,
        "timestamp": _agent_last_cycle_time,
    }
    STATE.last_cycle = result

    # 9. Summary log + alert
    elapsed = (datetime.utcnow() - cycle_start).total_seconds()
    summary = (
        f"Cycle #{cycle_num} complete -- "
        f"{len(signals)} signals, {len(strong_signals)} above {min_confidence}% conf, "
        f"{trades_executed} trades executed ({elapsed:.1f}s)"
    )
    logger.info(summary)
    STATE.add_alert("CYCLE", summary, "INFO")

    # 10. Broadcast via WebSocket
    await WS_MANAGER.broadcast({"type": "CYCLE_UPDATE", "data": result})
