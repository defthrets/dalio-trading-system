"""
Circuit Breaker — Hard-coded risk limits.

Enforces:
  - Daily stop-loss: 2% of total equity (configurable via settings)
  - Maximum drawdown: 10% of peak equity
  - Per-trade position limits
  - Kill-switch: halts all new trades when limits are hit
"""

from datetime import date, datetime
from loguru import logger
from dataclasses import dataclass, field
from typing import Optional

from config.settings import get_settings


@dataclass
class EquityState:
    """Tracks portfolio equity for circuit breaker calculations."""
    starting_equity: float          # Value at system start / day open
    peak_equity: float              # All-time / rolling peak
    current_equity: float           # Current mark-to-market
    daily_open_equity: float        # Equity at start of current trading day
    last_reset_date: date = field(default_factory=date.today)

    @property
    def daily_pnl_pct(self) -> float:
        if self.daily_open_equity == 0:
            return 0.0
        return (self.current_equity - self.daily_open_equity) / self.daily_open_equity * 100

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity == 0:
            return 0.0
        return (self.peak_equity - self.current_equity) / self.peak_equity * 100


class CircuitBreaker:
    """
    Monitors portfolio equity and halts trading when risk limits are breached.
    All limits are hard-coded safety rails per Dalio's risk discipline.
    """

    def __init__(self, starting_equity: float = 100_000.0):
        self.settings = get_settings()
        self.state = EquityState(
            starting_equity=starting_equity,
            peak_equity=starting_equity,
            current_equity=starting_equity,
            daily_open_equity=starting_equity,
        )
        self._trading_halted: bool = False
        self._halt_reason: str = ""
        self._halt_timestamp: Optional[str] = None
        self._daily_trade_count: int = 0
        self._daily_loss_trades: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_equity(self, new_equity: float):
        """Call after each trade or mark-to-market update."""
        today = date.today()

        # Daily reset
        if self.state.last_reset_date < today:
            self.state.daily_open_equity = new_equity
            self.state.last_reset_date = today
            self._daily_trade_count = 0
            self._daily_loss_trades = 0
            logger.info(f"Circuit breaker: daily reset at equity ${new_equity:,.2f}")

        self.state.current_equity = new_equity
        self.state.peak_equity = max(self.state.peak_equity, new_equity)

        self._check_limits()

    def can_trade(self, trade_value: Optional[float] = None) -> tuple[bool, str]:
        """
        Call before executing any trade.
        Returns (True, "") if trading is allowed, or (False, reason) if blocked.
        """
        if self._trading_halted:
            return False, f"CIRCUIT BREAKER TRIPPED: {self._halt_reason}"

        # Check daily loss limit
        daily_loss = self.state.daily_pnl_pct
        if daily_loss <= -self.settings.max_daily_loss_pct:
            reason = (
                f"Daily loss limit reached: {daily_loss:.2f}% "
                f"(limit: -{self.settings.max_daily_loss_pct}%)"
            )
            self._trip(reason)
            return False, reason

        # Check max drawdown
        drawdown = self.state.drawdown_pct
        if drawdown >= self.settings.max_drawdown_pct:
            reason = (
                f"Maximum drawdown breached: {drawdown:.2f}% "
                f"(limit: {self.settings.max_drawdown_pct}%)"
            )
            self._trip(reason)
            return False, reason

        # Position size check
        if trade_value and self.state.current_equity > 0:
            pct = trade_value / self.state.current_equity * 100
            if pct > 25.0:
                return False, f"Single trade > 25% of portfolio ({pct:.1f}%) — rejected."

        return True, ""

    def record_trade(self, pnl: float):
        """Record the outcome of a completed trade."""
        self._daily_trade_count += 1
        if pnl < 0:
            self._daily_loss_trades += 1
        new_equity = self.state.current_equity + pnl
        self.update_equity(new_equity)

    def reset_halt(self, authorised_by: str = "manual"):
        """Manually reset the circuit breaker (requires explicit authorisation)."""
        logger.warning(f"Circuit breaker RESET by {authorised_by}")
        self._trading_halted = False
        self._halt_reason = ""
        self._halt_timestamp = None

    def get_status(self) -> dict:
        """Return full circuit breaker status for reporting."""
        return {
            "trading_halted": self._trading_halted,
            "halt_reason": self._halt_reason,
            "halt_timestamp": self._halt_timestamp,
            "current_equity": round(self.state.current_equity, 2),
            "peak_equity": round(self.state.peak_equity, 2),
            "daily_open_equity": round(self.state.daily_open_equity, 2),
            "daily_pnl_pct": round(self.state.daily_pnl_pct, 4),
            "drawdown_pct": round(self.state.drawdown_pct, 4),
            "daily_loss_limit_pct": self.settings.max_daily_loss_pct,
            "max_drawdown_limit_pct": self.settings.max_drawdown_pct,
            "daily_trades_today": self._daily_trade_count,
            "headroom_daily_loss": round(
                self.settings.max_daily_loss_pct + self.state.daily_pnl_pct, 4
            ),
            "headroom_drawdown": round(
                self.settings.max_drawdown_pct - self.state.drawdown_pct, 4
            ),
        }

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _check_limits(self):
        """Automatically check and trip breaker when limits are hit."""
        if self._trading_halted:
            return

        daily = self.state.daily_pnl_pct
        dd = self.state.drawdown_pct

        if daily <= -self.settings.max_daily_loss_pct:
            self._trip(
                f"Daily loss limit: {daily:.2f}% ≤ -{self.settings.max_daily_loss_pct}%"
            )
        elif dd >= self.settings.max_drawdown_pct:
            self._trip(
                f"Max drawdown: {dd:.2f}% ≥ {self.settings.max_drawdown_pct}%"
            )

    def _trip(self, reason: str):
        self._trading_halted = True
        self._halt_reason = reason
        self._halt_timestamp = datetime.utcnow().isoformat()
        logger.critical(
            f"🚨 CIRCUIT BREAKER TRIPPED 🚨\n"
            f"Reason: {reason}\n"
            f"Equity: ${self.state.current_equity:,.2f} | "
            f"Daily P&L: {self.state.daily_pnl_pct:.2f}% | "
            f"Drawdown: {self.state.drawdown_pct:.2f}%\n"
            f"ALL TRADING HALTED."
        )
