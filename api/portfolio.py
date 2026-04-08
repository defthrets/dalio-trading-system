"""
Dalios -- Paper Trading Portfolio
PaperPortfolio class, fee model, position sizing, persistence.
"""

import json
from datetime import datetime
from typing import Optional

from loguru import logger

from api.state import (
    STATE, PAPER_STATE_FILE, PAPER_CONFIG_FILE,
    _db_sync_positions, _PAPER_LOCK,
    SETTINGS_AVAILABLE,
)
from api.scanners import COMMODITY_TICKERS


# ── Trading Fee Schedule (percentage of trade value) ─────────────────
# Covers brokerage, spread, exchange fees. Conservative estimates.
TRADING_FEES = {
    "asx":         0.10,   # 0.10% -- typical ASX online broker (CommSec, SelfWealth)
    "us_equity":   0.05,   # 0.05% -- typical US broker (incl. SEC/FINRA micro-fees)
    "commodities": 0.10,   # 0.10% -- commodity ETF brokerage
    "forex":       0.03,   # 0.03% -- forex spread cost estimate
    "default":     0.10,   # 0.10% -- fallback for unknown asset types
}


def _get_fee_pct(ticker: str) -> float:
    """Return the estimated round-trip fee percentage for a ticker."""
    if ticker.endswith(".AX"):
        return TRADING_FEES["asx"]
    elif ticker in COMMODITY_TICKERS:
        return TRADING_FEES["commodities"]
    else:
        return TRADING_FEES["default"]


def _calc_fee(ticker: str, qty: float, price: float) -> float:
    """Calculate fee in dollars for a single leg of a trade."""
    return round(qty * price * _get_fee_pct(ticker) / 100, 4)


# ── Paper config (persisted) ────────────────────────────
def _load_paper_config() -> dict:
    if PAPER_CONFIG_FILE.exists():
        try:
            return json.loads(PAPER_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"starting_cash": 1_000.0}


def _save_paper_config() -> None:
    try:
        PAPER_CONFIG_FILE.write_text(json.dumps({"starting_cash": PAPER_STARTING_CASH}, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to save paper config: {exc}")


_paper_cfg = _load_paper_config()
PAPER_STARTING_CASH: float = float(_paper_cfg.get("starting_cash", 1_000.0))
# Persist config file on first startup if it doesn't exist
if not PAPER_CONFIG_FILE.exists():
    try:
        PAPER_CONFIG_FILE.write_text(json.dumps({"starting_cash": PAPER_STARTING_CASH}, indent=2), encoding="utf-8")
    except Exception:
        pass


class PaperPortfolio:
    def __init__(self):
        self.cash = PAPER_STARTING_CASH
        self.positions: dict = {}
        self.history: list = []
        self.equity_history: list = []  # [{t: ISO, v: float}]
        self.order_id = 0

    def _next_id(self) -> int:
        self.order_id += 1
        return self.order_id

    def total_value(self, prices: dict) -> float:
        """Portfolio total = cash + market value of all positions."""
        invested = sum(
            pos["qty"] * prices.get(t, pos["entry_price"])
            for t, pos in self.positions.items()
        )
        return round(self.cash + invested, 2)

    def unrealised_pnl(self, prices: dict) -> float:
        total = 0.0
        for t, pos in self.positions.items():
            cur = prices.get(t, pos["entry_price"])
            if pos["side"] == "LONG":
                total += (cur - pos["entry_price"]) * pos["qty"]
            else:
                total += (pos["entry_price"] - cur) * pos["qty"]
        return round(total, 2)

    def place_order(self, ticker: str, side: str, qty: float, price: float,
                    stop_loss: float = None, take_profit: float = None) -> dict:
        cost = qty * price
        fee = _calc_fee(ticker, qty, price)
        oid = self._next_id()
        ts = datetime.utcnow().isoformat()

        if side == "BUY":
            total_cost = cost + fee
            if total_cost > self.cash:
                raise ValueError(f"Insufficient cash -- need ${total_cost:,.2f} (incl ${fee:.2f} fee), have ${self.cash:,.2f}")
            self.cash -= total_cost
            if ticker in self.positions and self.positions[ticker]["side"] == "LONG":
                # Add to existing long
                pos = self.positions[ticker]
                total_qty = pos["qty"] + qty
                total_cost = pos["entry_price"] * pos["qty"] + price * qty
                pos["entry_price"] = round(total_cost / total_qty, 4)
                pos["qty"] = total_qty
                # Update SL/TP if provided (new values take precedence)
                if stop_loss is not None:
                    pos["stop_loss"] = stop_loss
                if take_profit is not None:
                    pos["take_profit"] = take_profit
            else:
                self.positions[ticker] = {
                    "qty": qty, "entry_price": round(price, 4),
                    "entry_time": ts, "side": "LONG", "cost_basis": round(cost, 2),
                    "stop_loss": stop_loss, "take_profit": take_profit,
                }
        else:  # SELL / close
            if ticker not in self.positions:
                raise ValueError(f"No open position in {ticker}")
            pos = self.positions[ticker]
            close_qty = min(qty, pos["qty"])
            sell_fee = _calc_fee(ticker, close_qty, price)
            proceeds = close_qty * price - sell_fee
            # Also account for the buy-side fee already embedded in cost_basis
            buy_fee = _calc_fee(ticker, close_qty, pos["entry_price"])
            if pos["side"] == "LONG":
                pnl = (price - pos["entry_price"]) * close_qty - buy_fee - sell_fee
            else:
                pnl = (pos["entry_price"] - price) * close_qty - buy_fee - sell_fee
            self.cash += proceeds
            self.history.insert(0, {
                "id": oid, "ticker": ticker, "side": "SELL",
                "qty": close_qty, "entry_price": pos["entry_price"],
                "exit_price": round(price, 4), "pnl": round(pnl, 2),
                "pnl_pct": round(pnl / (pos["entry_price"] * close_qty) * 100, 2),
                "fees": round(buy_fee + sell_fee, 2),
                "entry_time": pos.get("entry_time", ts),
                "timestamp": ts,
            })
            pos["qty"] -= close_qty
            if pos["qty"] <= 0:
                del self.positions[ticker]
        STATE.add_alert("PAPER", f"Order #{oid}: {side} {qty:.4g} {ticker} @ ${price:.4f} (fee ${fee:.2f})", "INFO")
        return {"order_id": oid, "ticker": ticker, "side": side, "qty": qty, "price": price, "fee": fee, "timestamp": ts}

    def reset(self):
        self.cash = PAPER_STARTING_CASH
        self.positions = {}
        self.history = []
        self.equity_history = []
        self.order_id = 0
        STATE.add_alert("PAPER", f"Portfolio reset to ${PAPER_STARTING_CASH:,.2f} starting cash", "INFO")


PAPER = PaperPortfolio()


def _save_paper_state() -> None:
    try:
        payload = {
            "cash":           PAPER.cash,
            "positions":      PAPER.positions,
            "history":        PAPER.history,
            "equity_history": PAPER.equity_history,
            "order_id":       PAPER.order_id,
        }
        PAPER_STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to save paper state: {exc}")
    # Also sync open positions to DB
    _db_sync_positions(PAPER.positions)


def _load_paper_state() -> None:
    if not PAPER_STATE_FILE.exists():
        return
    try:
        payload = json.loads(PAPER_STATE_FILE.read_text(encoding="utf-8"))
        PAPER.cash           = float(payload.get("cash", PAPER_STARTING_CASH))
        PAPER.positions      = payload.get("positions", {})
        PAPER.history        = payload.get("history", [])
        PAPER.equity_history = payload.get("equity_history", [])
        PAPER.order_id       = int(payload.get("order_id", 0))
        logger.info(f"Paper portfolio loaded -- cash=${PAPER.cash:,.2f}, "
                    f"{len(PAPER.positions)} positions, {len(PAPER.equity_history)} equity pts")
    except Exception as exc:
        logger.warning(f"Failed to load paper state (starting fresh): {exc}")


# ── Position sizing / risk parameters ─────────────────────────────
_RISK_MAX_POS_SIZE_PCT = 10.0   # max % of portfolio in a single position
_RISK_MAX_OPEN = 20     # max number of concurrent open positions
_RISK_MAX_DAILY_LOSS = 2.0    # max daily loss % before blocking new buys

if SETTINGS_AVAILABLE:
    try:
        from config.settings import get_settings as _get_settings
        _s = _get_settings()
        _RISK_MAX_POS_SIZE_PCT = getattr(_s, "max_pos_size_pct", 10.0)
        _RISK_MAX_OPEN = getattr(_s, "max_open_positions", 20)
        _RISK_MAX_DAILY_LOSS = getattr(_s, "max_daily_loss_pct", 2.0)
    except Exception:
        pass  # keep defaults


def _calculate_position_size(ticker: str, price: float, side: str,
                             portfolio: PaperPortfolio,
                             prices: dict) -> dict:
    """Calculate the maximum allowed quantity for a trade based on risk limits.

    Returns dict with keys: max_allowed_qty, position_pct, reason.
    Raises ValueError if the trade is completely blocked.
    """
    total_value = portfolio.total_value(prices)
    if total_value <= 0:
        raise ValueError("Portfolio value is zero or negative -- cannot size position")

    # --- SELL orders: no position sizing restrictions ---
    if side != "BUY":
        existing = portfolio.positions.get(ticker)
        return {
            "max_allowed_qty": existing["qty"] if existing else 0,
            "position_pct": 0.0,
            "reason": "sell_no_limit",
        }

    # --- Check max open positions ---
    if ticker not in portfolio.positions and len(portfolio.positions) >= _RISK_MAX_OPEN:
        raise ValueError(
            f"Max open positions reached ({_RISK_MAX_OPEN}). "
            f"Close an existing position before opening a new one."
        )

    # --- Check daily loss limit ---
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    today_pnl = sum(
        t["pnl"] for t in portfolio.history
        if t.get("timestamp", "").startswith(today_str)
    )
    if total_value > 0:
        daily_loss_pct = abs(min(0, today_pnl)) / total_value * 100
        if daily_loss_pct >= _RISK_MAX_DAILY_LOSS:
            raise ValueError(
                f"Daily loss limit reached ({daily_loss_pct:.1f}% >= {_RISK_MAX_DAILY_LOSS}%). "
                f"No new BUY orders allowed today."
            )

    # --- Calculate max position value ---
    max_position_value = total_value * (_RISK_MAX_POS_SIZE_PCT / 100.0)

    # Account for existing position in same ticker
    existing = portfolio.positions.get(ticker)
    current_position_value = 0.0
    if existing and existing["side"] == "LONG":
        current_position_value = existing["qty"] * prices.get(ticker, existing["entry_price"])

    remaining_allowance = max(0, max_position_value - current_position_value)

    if remaining_allowance <= 0:
        raise ValueError(
            f"Position in {ticker} already at max size "
            f"(${current_position_value:,.2f} >= {_RISK_MAX_POS_SIZE_PCT}% of portfolio ${total_value:,.2f})"
        )

    # Also cap by available cash
    max_by_cash = portfolio.cash / price if price > 0 else 0
    max_by_risk = remaining_allowance / price if price > 0 else 0
    max_allowed_qty = min(max_by_cash, max_by_risk)

    position_pct = 0.0
    if total_value > 0:
        position_pct = round((current_position_value + max_allowed_qty * price) / total_value * 100, 2)

    return {
        "max_allowed_qty": round(max_allowed_qty, 8),
        "position_pct": min(position_pct, _RISK_MAX_POS_SIZE_PCT),
        "reason": "ok",
    }
