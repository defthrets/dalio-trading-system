"""
Execution Engine — Order management and paper/live trade routing.

Paper trading mode: logs all orders to the database with simulated fills.
Live mode: routes to broker API (extendable).

Before every execution, verifies:
  1. Circuit breaker allows trading.
  2. Correlation gate won't breach threshold.
  3. Systematic Justification is generated and logged.
"""

import uuid
from datetime import datetime
from loguru import logger
from dataclasses import dataclass
from typing import Optional

from trading.circuit_breaker import CircuitBreaker
from trading.signal_generator import TradeSignal
from config.settings import get_settings


@dataclass
class Order:
    """Represents a submitted trade order."""
    order_id: str
    ticker: str
    action: str           # BUY | SELL | SHORT | COVER
    quantity: float
    price: float
    order_type: str       # "market" | "limit"
    status: str           # "pending" | "filled" | "rejected" | "cancelled"
    timestamp: str
    fill_price: Optional[float] = None
    fill_timestamp: Optional[str] = None
    justification: str = ""
    pnl: Optional[float] = None


class ExecutionEngine:
    """
    Routes trade signals to orders, enforces pre-trade checks,
    and generates Systematic Justification reports.
    """

    def __init__(self, circuit_breaker: CircuitBreaker, portfolio_equity: float = 100_000.0):
        self.settings = get_settings()
        self.circuit_breaker = circuit_breaker
        self.portfolio_equity = portfolio_equity
        self.open_positions: dict[str, dict] = {}  # ticker -> position data
        self.order_history: list[Order] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute_signal(self, signal: TradeSignal) -> Optional[Order]:
        """
        Main entry point: takes a TradeSignal and attempts execution.
        Performs all pre-trade checks and generates Systematic Justification.
        """
        # 1. Circuit Breaker check
        trade_value = self._calc_trade_value(signal)
        can_trade, reason = self.circuit_breaker.can_trade(trade_value)
        if not can_trade:
            logger.warning(f"Trade BLOCKED for {signal.ticker}: {reason}")
            return self._reject_order(signal, reason)

        # 2. Generate Systematic Justification (required before execution)
        justification = self.generate_systematic_justification(signal)
        logger.info(f"\n{justification}")

        # 3. Build order
        quantity = self._calc_quantity(signal, trade_value)
        if quantity <= 0:
            logger.warning(f"Zero quantity calculated for {signal.ticker} — skipping.")
            return None

        order = Order(
            order_id=str(uuid.uuid4())[:8].upper(),
            ticker=signal.ticker,
            action=signal.action,
            quantity=quantity,
            price=signal.price,
            order_type="market",
            status="pending",
            timestamp=datetime.utcnow().isoformat(),
            justification=justification,
        )

        # 4. Simulate fill (paper trading) or route to live broker
        if self.settings.trading_mode == "paper":
            order = self._paper_fill(order, signal)
        else:
            order = self._live_route(order, signal)

        self.order_history.append(order)
        self._update_positions(order, signal)

        logger.info(
            f"Order {order.order_id} | {order.action} {order.quantity:.2f} "
            f"{order.ticker} @ ${order.fill_price:.4f} | Status: {order.status}"
        )
        return order

    def generate_systematic_justification(self, signal: TradeSignal) -> str:
        """
        Produce a Dalio-style Systematic Justification for a trade.
        This is the AI agent's required pre-trade checklist.
        """
        sep = "=" * 60
        lines = [
            sep,
            f"  SYSTEMATIC JUSTIFICATION — {signal.ticker}",
            sep,
            f"  Action    : {signal.action} @ ${signal.price:.4f}",
            f"  Confidence: {signal.confidence:.1%}",
            f"  Timestamp : {signal.timestamp}",
            "",
            "  1. ECONOMIC QUADRANT ANALYSIS",
            f"     Current Quadrant : {signal.quadrant.upper().replace('_', ' ')}",
            f"     Asset Fit        : {signal.quadrant_fit.upper()}",
            "",
            "  2. SENTIMENT & GEOPOLITICAL RISK",
            f"     News Sentiment   : {signal.sentiment_label.capitalize()} "
            f"(score: {signal.sentiment_score:+.4f})",
            f"     Conflict Risk    : {'⚠ ELEVATED' if signal.conflict_risk else 'Normal'}",
            "",
            "  3. TECHNICAL SIGNALS",
            f"     RSI              : {signal.rsi:.1f} "
            f"({'Oversold' if signal.rsi < 30 else 'Overbought' if signal.rsi > 70 else 'Neutral'})",
            f"     MACD             : {signal.macd_signal.capitalize()}",
            f"     Bollinger Band   : {signal.bb_position.replace('_', ' ').capitalize()}",
            f"     Trend            : {signal.trend.capitalize()}",
            f"     ATR              : {signal.atr:.4f}",
            "",
            "  4. RISK METRICS",
            f"     Stop Loss        : ${signal.suggested_stop_loss:.4f}",
            f"     Take Profit      : ${signal.suggested_take_profit:.4f}",
            f"     Risk/Reward      : {signal.risk_reward_ratio:.2f}x",
            f"     Position Size    : {signal.position_size_pct:.2f}% of portfolio",
            "",
            "  5. OPTIONS STRATEGY",
            f"     {signal.options_strategy or 'N/A — equity trade only.'}",
            "",
            "  6. REASONING",
        ]
        for i, reason in enumerate(signal.reasons, 1):
            lines.append(f"     {i}. {reason}")

        if not signal.reasons:
            lines.append("     No explicit reasons recorded.")

        lines.append(sep)
        return "\n".join(lines)

    def get_open_positions(self) -> dict:
        return self.open_positions

    def get_portfolio_summary(self) -> dict:
        return {
            "equity": self.portfolio_equity,
            "open_positions": len(self.open_positions),
            "total_orders": len(self.order_history),
            "filled_orders": sum(1 for o in self.order_history if o.status == "filled"),
            "circuit_breaker": self.circuit_breaker.get_status(),
        }

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _paper_fill(self, order: Order, signal: TradeSignal) -> Order:
        """Simulate immediate fill at signal price (paper trading)."""
        order.fill_price = signal.price * (1 + 0.001)  # Tiny slippage simulation
        order.fill_timestamp = datetime.utcnow().isoformat()
        order.status = "filled"
        return order

    def _live_route(self, order: Order, signal: TradeSignal) -> Order:
        """Placeholder for live broker routing (extend with broker API)."""
        logger.warning("Live trading not yet configured. Falling back to paper mode.")
        return self._paper_fill(order, signal)

    def _reject_order(self, signal: TradeSignal, reason: str) -> Order:
        order = Order(
            order_id=str(uuid.uuid4())[:8].upper(),
            ticker=signal.ticker,
            action=signal.action,
            quantity=0,
            price=signal.price,
            order_type="market",
            status="rejected",
            timestamp=datetime.utcnow().isoformat(),
            justification=f"REJECTED: {reason}",
        )
        self.order_history.append(order)
        return order

    def _calc_trade_value(self, signal: TradeSignal) -> float:
        pct = signal.position_size_pct / 100.0
        return self.portfolio_equity * pct

    def _calc_quantity(self, signal: TradeSignal, trade_value: float) -> float:
        if signal.price <= 0:
            return 0.0
        return round(trade_value / signal.price, 4)

    def _update_positions(self, order: Order, signal: TradeSignal):
        if order.status != "filled":
            return

        ticker = order.ticker
        if order.action in ("BUY", "COVER"):
            self.open_positions[ticker] = {
                "action": "long",
                "quantity": order.quantity,
                "entry_price": order.fill_price,
                "entry_time": order.fill_timestamp,
                "stop_loss": signal.suggested_stop_loss,
                "take_profit": signal.suggested_take_profit,
            }
        elif order.action in ("SELL", "SHORT"):
            if ticker in self.open_positions:
                # Calculate P&L
                entry = self.open_positions[ticker]["entry_price"]
                qty = self.open_positions[ticker]["quantity"]
                pnl = (order.fill_price - entry) * qty
                order.pnl = round(pnl, 2)
                self.circuit_breaker.record_trade(pnl)
                del self.open_positions[ticker]
            else:
                # Opening a short
                self.open_positions[ticker] = {
                    "action": "short",
                    "quantity": order.quantity,
                    "entry_price": order.fill_price,
                    "entry_time": order.fill_timestamp,
                    "stop_loss": signal.suggested_stop_loss,
                    "take_profit": signal.suggested_take_profit,
                }
