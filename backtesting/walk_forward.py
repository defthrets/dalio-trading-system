"""
Walk-Forward Optimizer — Anti-overfitting backtester.

Methodology (Dalio discipline — no look-ahead bias):
  - Train window : 12 months (configurable)
  - Test  window : 3  months (configurable)
  - Roll  step   : 3  months (anchored walk-forward)

Each iteration:
  1. Optimise signal parameters on the IN-SAMPLE window.
  2. Run the strategy on the OUT-OF-SAMPLE window.
  3. Record performance metrics.
  4. Advance both windows by one step.

Metrics: Sharpe, Sortino, Max Drawdown, Win Rate, Profit Factor, CAGR.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from loguru import logger
from typing import Optional
from dataclasses import dataclass, field

from data.ingestion.market_data import MarketDataFetcher
from config.settings import get_settings
from config.assets import get_all_assets


@dataclass
class WindowResult:
    """Results from a single walk-forward window."""
    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str

    # Performance
    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    cagr_pct: float = 0.0
    total_trades: int = 0
    avg_trade_pct: float = 0.0

    trades: list[dict] = field(default_factory=list)


@dataclass
class WalkForwardReport:
    """Aggregated results across all walk-forward windows."""
    windows: list[WindowResult] = field(default_factory=list)

    @property
    def avg_sharpe(self) -> float:
        ratios = [w.sharpe_ratio for w in self.windows if w.sharpe_ratio != 0]
        return round(np.mean(ratios), 4) if ratios else 0.0

    @property
    def avg_max_drawdown(self) -> float:
        dds = [w.max_drawdown_pct for w in self.windows]
        return round(np.mean(dds), 4) if dds else 0.0

    @property
    def total_return_pct(self) -> float:
        returns = [w.total_return_pct / 100 for w in self.windows]
        compound = 1.0
        for r in returns:
            compound *= (1 + r)
        return round((compound - 1) * 100, 4)

    @property
    def consistency_score(self) -> float:
        """% of windows with positive returns — measures robustness."""
        if not self.windows:
            return 0.0
        positive = sum(1 for w in self.windows if w.total_return_pct > 0)
        return round(positive / len(self.windows) * 100, 1)

    def summary(self) -> dict:
        return {
            "windows_tested": len(self.windows),
            "avg_sharpe_ratio": self.avg_sharpe,
            "avg_max_drawdown_pct": self.avg_max_drawdown,
            "total_compound_return_pct": self.total_return_pct,
            "consistency_score_pct": self.consistency_score,
            "all_windows": [
                {
                    "window": w.window_id,
                    "test_period": f"{w.test_start} to {w.test_end}",
                    "return_pct": w.total_return_pct,
                    "sharpe": w.sharpe_ratio,
                    "max_dd_pct": w.max_drawdown_pct,
                    "win_rate_pct": w.win_rate_pct,
                    "trades": w.total_trades,
                }
                for w in self.windows
            ],
        }


class WalkForwardOptimizer:
    """
    Runs walk-forward backtests on the Dalio signal strategy.
    Prevents overfitting by only evaluating on unseen out-of-sample data.
    """

    def __init__(self):
        self.settings = get_settings()
        self.fetcher = MarketDataFetcher()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        tickers: Optional[list[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        initial_equity: float = 100_000.0,
        train_months: Optional[int] = None,
        test_months: Optional[int] = None,
    ) -> WalkForwardReport:
        """
        Run the full walk-forward backtest.

        Args:
            tickers   : Asset list (defaults to full universe)
            start_date: Backtest start (defaults to 5 years ago)
            end_date  : Backtest end   (defaults to today)
            initial_equity: Starting portfolio value
            train_months  : Training window length
            test_months   : Test (OOS) window length
        """
        if tickers is None:
            tickers = list(get_all_assets().keys())[:20]  # Top 20 for speed

        train_m = train_months or self.settings.walk_forward_train_months
        test_m = test_months or self.settings.walk_forward_test_months

        end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.now()
        start = (
            datetime.strptime(start_date, "%Y-%m-%d")
            if start_date
            else end - relativedelta(years=5)
        )

        logger.info(
            f"Walk-Forward Backtest | {start.date()} → {end.date()} | "
            f"Train={train_m}m Test={test_m}m | {len(tickers)} assets"
        )

        # Fetch all historical data upfront
        all_data = self._fetch_all_data(tickers, start, end)
        if not all_data:
            logger.error("No data available for backtesting.")
            return WalkForwardReport()

        # Generate windows
        windows = self._generate_windows(start, end, train_m, test_m)
        logger.info(f"Generated {len(windows)} walk-forward windows.")

        report = WalkForwardReport()
        for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
            logger.info(
                f"Window {i+1}/{len(windows)}: "
                f"Train [{train_start.date()}→{train_end.date()}] "
                f"Test [{test_start.date()}→{test_end.date()}]"
            )
            result = self._run_window(
                window_id=i + 1,
                all_data=all_data,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                initial_equity=initial_equity,
            )
            report.windows.append(result)

        summary = report.summary()
        logger.info(
            f"\n{'='*50}\n"
            f"WALK-FORWARD COMPLETE\n"
            f"Windows       : {summary['windows_tested']}\n"
            f"Avg Sharpe    : {summary['avg_sharpe_ratio']:.4f}\n"
            f"Avg MaxDD     : {summary['avg_max_drawdown_pct']:.2f}%\n"
            f"Total Return  : {summary['total_compound_return_pct']:.2f}%\n"
            f"Consistency   : {summary['consistency_score_pct']:.1f}% windows profitable\n"
            f"{'='*50}"
        )
        return report

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _generate_windows(
        self,
        start: datetime,
        end: datetime,
        train_months: int,
        test_months: int,
    ) -> list[tuple]:
        """Generate (train_start, train_end, test_start, test_end) tuples."""
        windows = []
        cursor = start
        while True:
            train_start = cursor
            train_end = cursor + relativedelta(months=train_months)
            test_start = train_end
            test_end = test_start + relativedelta(months=test_months)
            if test_end > end:
                break
            windows.append((train_start, train_end, test_start, test_end))
            cursor += relativedelta(months=test_months)  # Roll forward by test period
        return windows

    def _fetch_all_data(
        self, tickers: list[str], start: datetime, end: datetime
    ) -> dict[str, pd.DataFrame]:
        """Fetch full price history for all tickers once."""
        all_data = {}
        for ticker in tickers:
            df = self.fetcher.get_historical_data(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
            )
            if not df.empty:
                all_data[ticker] = df
        logger.info(f"Fetched data for {len(all_data)}/{len(tickers)} tickers.")
        return all_data

    def _run_window(
        self,
        window_id: int,
        all_data: dict[str, pd.DataFrame],
        train_start: datetime,
        train_end: datetime,
        test_start: datetime,
        test_end: datetime,
        initial_equity: float,
    ) -> WindowResult:
        """Run strategy simulation on a single OOS test window."""

        result = WindowResult(
            window_id=window_id,
            train_start=train_start.strftime("%Y-%m-%d"),
            train_end=train_end.strftime("%Y-%m-%d"),
            test_start=test_start.strftime("%Y-%m-%d"),
            test_end=test_end.strftime("%Y-%m-%d"),
        )

        # Step 1: Optimise thresholds on IN-SAMPLE data
        opt_params = self._optimize_on_train(all_data, train_start, train_end)

        # Step 2: Run strategy on OUT-OF-SAMPLE data
        equity_curve, trades = self._simulate_oos(
            all_data, test_start, test_end, initial_equity, opt_params
        )

        if equity_curve.empty or len(equity_curve) < 2:
            return result

        # Step 3: Calculate metrics
        returns = equity_curve.pct_change().dropna()
        result.total_return_pct = round(
            (equity_curve.iloc[-1] / equity_curve.iloc[0] - 1) * 100, 4
        )
        result.sharpe_ratio = self._sharpe(returns)
        result.sortino_ratio = self._sortino(returns)
        result.max_drawdown_pct = self._max_drawdown(equity_curve)
        result.total_trades = len(trades)
        result.trades = trades

        if trades:
            pnls = [t.get("pnl_pct", 0) for t in trades]
            wins = [p for p in pnls if p > 0]
            losses = [abs(p) for p in pnls if p < 0]
            result.win_rate_pct = round(len(wins) / len(pnls) * 100, 2)
            result.profit_factor = (
                round(sum(wins) / sum(losses), 4) if losses else float("inf")
            )
            result.avg_trade_pct = round(np.mean(pnls), 4)

        # CAGR
        days = (test_end - test_start).days
        if days > 0:
            result.cagr_pct = round(
                ((equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (365 / days) - 1) * 100,
                4,
            )

        return result

    def _optimize_on_train(
        self,
        all_data: dict,
        train_start: datetime,
        train_end: datetime,
    ) -> dict:
        """
        Simple parameter search over RSI thresholds and MA periods on training data.
        Returns the best-performing parameter set.
        """
        best_params = {"rsi_oversold": 30, "rsi_overbought": 70, "ma_fast": 20, "ma_slow": 50}
        best_sharpe = -np.inf

        param_grid = [
            {"rsi_oversold": 25, "rsi_overbought": 75, "ma_fast": 10, "ma_slow": 30},
            {"rsi_oversold": 30, "rsi_overbought": 70, "ma_fast": 20, "ma_slow": 50},
            {"rsi_oversold": 35, "rsi_overbought": 65, "ma_fast": 20, "ma_slow": 100},
        ]

        for params in param_grid:
            _, trades = self._simulate_oos(all_data, train_start, train_end, 100_000, params)
            if not trades:
                continue
            pnls = pd.Series([t.get("pnl_pct", 0) for t in trades]) / 100
            if len(pnls) < 5:
                continue
            sharpe = self._sharpe(pnls)
            if sharpe > best_sharpe:
                best_sharpe = sharpe
                best_params = params

        return best_params

    def _simulate_oos(
        self,
        all_data: dict,
        start: datetime,
        end: datetime,
        initial_equity: float,
        params: dict,
    ) -> tuple[pd.Series, list[dict]]:
        """
        Simple rule-based simulation using RSI + MA crossover signals.
        Returns equity curve and trade list.
        """
        rsi_os = params.get("rsi_oversold", 30)
        rsi_ob = params.get("rsi_overbought", 70)
        ma_fast = params.get("ma_fast", 20)
        ma_slow = params.get("ma_slow", 50)

        equity = initial_equity
        equity_index = []
        equity_values = []
        trades = []

        # Use first valid asset for demo curve; real impl runs all assets
        for ticker, df in all_data.items():
            window_df = df.loc[
                (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
            ]
            if len(window_df) < ma_slow + 5:
                continue

            close = window_df["Close"]
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rsi = 100 - (100 / (1 + gain / (loss + 1e-9)))
            fast_ma = close.rolling(ma_fast).mean()
            slow_ma = close.rolling(ma_slow).mean()

            position = None
            entry_price = 0.0

            for i in range(ma_slow, len(window_df)):
                date = window_df.index[i]
                price = float(close.iloc[i])
                r = float(rsi.iloc[i])
                uptrend = fast_ma.iloc[i] > slow_ma.iloc[i]

                # Entry
                if position is None:
                    if r < rsi_os and uptrend:
                        position = "long"
                        entry_price = price
                    elif r > rsi_ob and not uptrend:
                        position = "short"
                        entry_price = price

                # Exit
                elif position == "long" and (r > 60 or not uptrend):
                    pnl_pct = (price - entry_price) / entry_price * 100
                    pnl_abs = equity * pnl_pct / 100 * 0.1  # 10% position
                    equity += pnl_abs
                    trades.append({
                        "ticker": ticker, "action": "BUY→SELL",
                        "entry": entry_price, "exit": price,
                        "pnl_pct": round(pnl_pct, 4),
                        "date": str(date.date()),
                    })
                    position = None

                elif position == "short" and (r < 40 or uptrend):
                    pnl_pct = (entry_price - price) / entry_price * 100
                    pnl_abs = equity * pnl_pct / 100 * 0.1
                    equity += pnl_abs
                    trades.append({
                        "ticker": ticker, "action": "SHORT→COVER",
                        "entry": entry_price, "exit": price,
                        "pnl_pct": round(pnl_pct, 4),
                        "date": str(date.date()),
                    })
                    position = None

                equity_index.append(date)
                equity_values.append(equity)
            break  # Single asset for walk-forward demo; extend to portfolio

        if not equity_index:
            return pd.Series(dtype=float), trades

        equity_curve = pd.Series(equity_values, index=equity_index).resample("D").last().ffill()
        return equity_curve, trades

    # ------------------------------------------------------------------
    # Metric helpers
    # ------------------------------------------------------------------

    def _sharpe(self, returns: pd.Series, rf: float = 0.04 / 252) -> float:
        if returns.empty or returns.std() == 0:
            return 0.0
        excess = returns - rf
        return round(float(excess.mean() / excess.std() * np.sqrt(252)), 4)

    def _sortino(self, returns: pd.Series, rf: float = 0.04 / 252) -> float:
        if returns.empty:
            return 0.0
        downside = returns[returns < rf]
        if downside.empty or downside.std() == 0:
            return 0.0
        excess = returns.mean() - rf
        return round(float(excess / downside.std() * np.sqrt(252)), 4)

    def _max_drawdown(self, equity_curve: pd.Series) -> float:
        if equity_curve.empty:
            return 0.0
        rolling_max = equity_curve.cummax()
        drawdown = (equity_curve - rolling_max) / rolling_max * 100
        return round(float(drawdown.min()), 4)
