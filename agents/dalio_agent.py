"""
Dalio AI Agent — The Autonomous Orchestrator.

This is the brain of the system. It coordinates all engines and
produces actionable, justified trade decisions based on:
  - All Weather / Economic Machine principles
  - Real-time macro + news data
  - Correlation & risk parity constraints
  - Technical signals

The agent runs on a schedule (see main.py) and pushes results
to Discord/Telegram via the notifications module.

Dalio Principles Encoded:
  "The greatest mistake of the individual investor is to think
   that a market that did well is a good market." 

  1. Diversify across uncorrelated return streams (Holy Grail).
  2. Balance risk, not dollars, across all economic environments.
  3. Know where you are in the economic machine cycle.
  4. Be systematic — never emotional.
"""

import asyncio
from datetime import datetime
from loguru import logger
from typing import Optional

from engines.correlation_engine import CorrelationEngine
from engines.risk_parity_engine import RiskParityEngine
from engines.sentiment_engine import SentimentEngine
from engines.quadrant_engine import QuadrantEngine
from trading.signal_generator import SignalGenerator, TradeSignal
from trading.execution import ExecutionEngine
from trading.circuit_breaker import CircuitBreaker
from config.assets import get_core_assets
from config.settings import get_settings


class DalioAgent:
    """
    Autonomous trading agent that embodies DALIOS principles.

    Lifecycle:
      boot()       → initialise all engines
      run_cycle()  → one full decision cycle (called by scheduler)
      shutdown()   → clean teardown
    """

    def __init__(self, initial_equity: float = 100_000.0):
        self.settings = get_settings()
        self.initial_equity = initial_equity

        # Engines
        self.correlation_engine = CorrelationEngine()
        self.risk_parity_engine = RiskParityEngine()
        self.sentiment_engine = SentimentEngine()
        self.quadrant_engine = QuadrantEngine()

        # Trading
        self.circuit_breaker = CircuitBreaker(initial_equity)
        self.execution_engine = ExecutionEngine(self.circuit_breaker, initial_equity)
        self.signal_generator = SignalGenerator(
            quadrant_engine=self.quadrant_engine,
            sentiment_engine=self.sentiment_engine,
            correlation_engine=self.correlation_engine,
        )

        # State
        self._booted = False
        self._current_quadrant_context: Optional[dict] = None
        self._current_weights: dict[str, float] = {}
        self._selected_portfolio: list[str] = []
        self._notifier = None   # Injected after init to avoid circular imports

        self._cycle_count = 0
        self._last_correlation_update: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def boot(self):
        """Initialise all engines. Each step is isolated so one failure
        doesn't prevent the rest of the system from starting."""
        logger.info("=" * 60)
        logger.info("  DALIO AUTONOMOUS TRADING SYSTEM — BOOTING")
        logger.info("=" * 60)

        # Step 1: FinBERT (optional — keyword fallback exists)
        logger.info("Step 1/4: Loading FinBERT sentiment model...")
        try:
            self.sentiment_engine.load_model()
        except Exception as e:
            logger.error(f"FinBERT load failed, using keyword fallback: {e}")

        # Step 2: Quadrant classification
        logger.info("Step 2/4: Classifying economic quadrant...")
        try:
            self._current_quadrant_context = self.quadrant_engine.classify()
            logger.info(self.quadrant_engine.get_narrative())
        except Exception as e:
            logger.error(f"Quadrant classification failed, defaulting to unknown: {e}")
            self._current_quadrant_context = {"quadrant": "unknown", "description": "Boot failed — neutral positioning"}

        # Step 3: Correlation matrix
        logger.info("Step 3/4: Building correlation matrix...")
        try:
            self._refresh_correlations()
        except Exception as e:
            logger.error(f"Correlation matrix build failed: {e}")

        # Step 4: Risk-parity weights
        logger.info("Step 4/4: Computing risk-parity weights...")
        try:
            self._refresh_weights()
        except Exception as e:
            logger.error(f"Risk-parity weight computation failed: {e}")

        self._booted = True
        logger.info("DALIO AGENT READY. Entering autonomous mode.")

    def attach_notifier(self, notifier):
        """Inject the notification manager after construction."""
        self._notifier = notifier

    # ------------------------------------------------------------------
    # Main Cycle
    # ------------------------------------------------------------------

    def run_cycle(self) -> dict:
        """
        One full decision cycle. Called by the APScheduler job.

        Returns a summary dict for the notification system.
        """
        if not self._booted:
            self.boot()

        self._cycle_count += 1
        cycle_start = datetime.utcnow()
        logger.info(f"\n{'='*60}")
        logger.info(f"  DALIO CYCLE #{self._cycle_count} — {cycle_start.isoformat()}")
        logger.info(f"{'='*60}")

        # 1. Circuit Breaker Check
        can_trade, halt_reason = self.circuit_breaker.can_trade()
        if not can_trade:
            alert = {
                "type": "CIRCUIT_BREAKER",
                "message": f"Trading HALTED: {halt_reason}",
                "timestamp": cycle_start.isoformat(),
            }
            self._send_notification(alert)
            return alert

        # 2. Refresh macro / quadrant (every cycle — data may be stale)
        logger.info("Refreshing economic quadrant classification...")
        self._current_quadrant_context = self.quadrant_engine.classify()

        # 3. Refresh correlation matrix (every 24h)
        hours_since_update = self._hours_since_correlation_update()
        if hours_since_update >= self.settings.correlation_update_hours:
            logger.info("Refreshing correlation matrix (24h interval)...")
            self._refresh_correlations()
            self._refresh_weights()

        # 4. Scan asset universe for signals
        logger.info("Scanning universe for trade signals...")
        current_tickers = list(self.execution_engine.get_open_positions().keys())
        signals = self.signal_generator.scan_universe(
            current_portfolio=current_tickers,
            top_n=10,
        )

        # 5. Also look for NEW opportunities not in portfolio
        logger.info("Scanning for new opportunities...")
        new_opportunities = self.signal_generator.suggest_new_opportunities(
            exclude=current_tickers
        )

        # 6. Execute signals (paper or live)
        executed_orders = []
        for signal in signals:
            order = self.execution_engine.execute_signal(signal)
            if order and order.status == "filled":
                executed_orders.append(order)

        # 7. Portfolio health report
        portfolio_health = self._generate_portfolio_health()

        # 8. Compile cycle summary
        cycle_summary = {
            "type": "CYCLE_COMPLETE",
            "cycle": self._cycle_count,
            "timestamp": cycle_start.isoformat(),
            "quadrant": self._current_quadrant_context.get("quadrant", "unknown"),
            "quadrant_description": self._current_quadrant_context.get("description", ""),
            "conflict_risk": self._current_quadrant_context.get("conflict_risk_elevated", False),
            "signals_found": len(signals),
            "new_opportunities": len(new_opportunities),
            "orders_executed": len(executed_orders),
            "portfolio_health": portfolio_health,
            "top_signals": [self._signal_to_dict(s) for s in signals[:5]],
            "new_opportunities_detail": [self._signal_to_dict(s) for s in new_opportunities[:3]],
            "circuit_breaker": self.circuit_breaker.get_status(),
        }

        # 9. Send notifications
        self._send_notification(cycle_summary)

        logger.info(
            f"Cycle #{self._cycle_count} complete | "
            f"Signals: {len(signals)} | Executed: {len(executed_orders)} | "
            f"Quadrant: {self._current_quadrant_context.get('quadrant', '?')}"
        )
        return cycle_summary

    # ------------------------------------------------------------------
    # Specialised Reports
    # ------------------------------------------------------------------

    def run_portfolio_health_report(self) -> dict:
        """Standalone portfolio health report for scheduled alerts."""
        health = self._generate_portfolio_health()
        self._send_notification({"type": "HEALTH_REPORT", **health})
        return health

    def run_sentiment_alert(self) -> dict:
        """Run a sentiment-only scan and alert on elevated conflict risk."""
        logger.info("Running sentiment alert scan...")
        summary = self.sentiment_engine.get_market_sentiment_summary()
        if summary.get("conflict_risk_elevated") or summary.get("conflict_risk_articles", 0) > 3:
            alert = {
                "type": "SENTIMENT_ALERT",
                "message": (
                    f"⚠ Elevated geopolitical risk detected: "
                    f"{summary.get('conflict_risk_articles', 0)} conflict-related articles. "
                    f"Consider reducing commodity/equity exposure and increasing gold/bonds."
                ),
                "dominant_quadrant": summary.get("dominant_quadrant", "unknown"),
                "sentiment_summary": summary,
                "timestamp": datetime.utcnow().isoformat(),
            }
            self._send_notification(alert)
            return alert
        return {"type": "SENTIMENT_SCAN", "status": "normal", **summary}

    def run_walk_forward_backtest(self, tickers: Optional[list[str]] = None) -> dict:
        """Run and report walk-forward backtest results."""
        from backtesting.walk_forward import WalkForwardOptimizer
        logger.info("Starting walk-forward backtest...")
        wf = WalkForwardOptimizer()
        report = wf.run(tickers=tickers, initial_equity=self.initial_equity)
        summary = report.summary()
        notification = {
            "type": "BACKTEST_REPORT",
            "timestamp": datetime.utcnow().isoformat(),
            **summary,
        }
        self._send_notification(notification)
        return summary

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _refresh_correlations(self):
        tickers = list(get_core_assets().keys())
        self.correlation_engine.refresh(tickers)
        self._last_correlation_update = datetime.utcnow()

        # Select Dalio-compliant low-correlation portfolio
        self._selected_portfolio = self.correlation_engine.get_low_correlation_assets(
            target_count=self.settings.min_diversification_assets,
            max_corr=self.settings.max_portfolio_correlation,
        )
        logger.info(
            f"Holy Grail portfolio selected: {len(self._selected_portfolio)} assets "
            f"with correlation < {self.settings.max_portfolio_correlation}"
        )

    def _refresh_weights(self):
        if not self._selected_portfolio:
            return
        self._current_weights = self.risk_parity_engine.compute_weights(
            self._selected_portfolio
        )
        logger.info(
            f"Risk-parity weights computed for {len(self._current_weights)} assets."
        )

    def _generate_portfolio_health(self) -> dict:
        cb_status = self.circuit_breaker.get_status()
        positions = self.execution_engine.get_open_positions()
        port_tickers = list(positions.keys())

        corr_stats = {}
        if port_tickers and len(port_tickers) >= 2:
            corr_stats = self.correlation_engine.get_portfolio_correlation_stats(port_tickers)

        sharpe_info = {}
        if port_tickers and self._current_weights:
            sharpe_info = self.risk_parity_engine.compute_sharpe_contribution(
                self._current_weights, port_tickers
            )

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "equity": cb_status["current_equity"],
            "daily_pnl_pct": cb_status["daily_pnl_pct"],
            "drawdown_pct": cb_status["drawdown_pct"],
            "open_positions": len(positions),
            "portfolio_assets": port_tickers,
            "correlation_stats": corr_stats,
            "sharpe_info": sharpe_info,
            "risk_weights": self._current_weights,
            "selected_portfolio_size": len(self._selected_portfolio),
            "dalio_diversification_met": len(self._selected_portfolio) >= 15,
            "circuit_breaker_active": cb_status["trading_halted"],
        }

    def _hours_since_correlation_update(self) -> float:
        if self._last_correlation_update is None:
            return float("inf")
        delta = datetime.utcnow() - self._last_correlation_update
        return delta.total_seconds() / 3600

    def _send_notification(self, data: dict):
        if self._notifier:
            try:
                self._notifier.send(data)
            except Exception as e:
                logger.error(f"Notification failed: {e}")

    @staticmethod
    def _signal_to_dict(signal: TradeSignal) -> dict:
        return {
            "ticker": signal.ticker,
            "action": signal.action,
            "confidence": signal.confidence,
            "price": signal.price,
            "quadrant_fit": signal.quadrant_fit,
            "sentiment": signal.sentiment_label,
            "rsi": signal.rsi,
            "trend": signal.trend,
            "stop_loss": signal.suggested_stop_loss,
            "take_profit": signal.suggested_take_profit,
            "rr_ratio": signal.risk_reward_ratio,
            "position_size_pct": signal.position_size_pct,
            "options_strategy": signal.options_strategy,
            "reasons": signal.reasons,
        }
