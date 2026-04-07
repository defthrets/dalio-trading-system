"""
Correlation Engine — Dalio "Holy Grail" Diversification.

Every 24 hours:
  1. Build a rolling Pearson correlation matrix for all tracked assets.
  2. Enforce the 15-asset rule: prefer assets with pairwise correlation < 0.3.
  3. Reject any trade that pushes portfolio-wide average correlation above threshold.
"""

import pandas as pd
import numpy as np
from datetime import datetime
from loguru import logger
from typing import Optional

from config.settings import get_settings
from config.assets import get_core_assets
from data.ingestion.market_data import MarketDataFetcher


class CorrelationEngine:
    """Builds and enforces Dalio's Holy Grail correlation constraints."""

    def __init__(self):
        self.settings = get_settings()
        self.fetcher = MarketDataFetcher()
        self.corr_matrix: Optional[pd.DataFrame] = None
        self.last_updated: Optional[datetime] = None
        self.returns_df: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self, tickers: Optional[list[str]] = None) -> pd.DataFrame:
        """
        Rebuild the correlation matrix from fresh market data.
        Called once every 24 hours by the scheduler.
        Preserves the last good matrix if the refresh fails.
        """
        if tickers is None:
            tickers = list(get_core_assets().keys())

        logger.info(f"Refreshing correlation matrix for {len(tickers)} assets...")

        try:
            new_returns = self._build_returns_matrix(tickers)
        except Exception as e:
            logger.error(f"Correlation refresh crashed: {e}")
            if self.corr_matrix is not None:
                logger.info("Keeping previous correlation matrix.")
            return self.corr_matrix if self.corr_matrix is not None else pd.DataFrame()

        if new_returns.empty:
            logger.warning("No return data available — keeping previous correlation matrix.")
            if self.corr_matrix is not None:
                return self.corr_matrix
            return pd.DataFrame()

        self.returns_df = new_returns
        lookback = self.settings.correlation_lookback_days
        recent = self.returns_df.tail(lookback)
        self.corr_matrix = recent.corr(method="pearson")
        self.last_updated = datetime.utcnow()

        logger.info(
            f"Correlation matrix updated: {self.corr_matrix.shape} "
            f"at {self.last_updated.isoformat()}"
        )
        return self.corr_matrix

    def get_low_correlation_assets(
        self,
        target_count: int = 15,
        max_corr: float = 0.3,
        seed_ticker: Optional[str] = None,
    ) -> list[str]:
        """
        Select up to `target_count` assets where pairwise |correlation| < max_corr.
        Uses a greedy selection: starts from the lowest-volatility seed and
        adds assets that stay below the threshold with ALL already-selected assets.

        Returns the selected ticker list — must contain >= 15 to satisfy Dalio's rule.
        """
        if self.corr_matrix is None:
            logger.warning("Correlation matrix not built — call refresh() first.")
            return []

        available = list(self.corr_matrix.columns)
        if not available:
            return []

        # Seed: lowest average absolute correlation asset (most independent)
        if seed_ticker and seed_ticker in available:
            selected = [seed_ticker]
        else:
            avg_abs = self.corr_matrix.abs().mean()
            selected = [avg_abs.idxmin()]

        for candidate in available:
            if candidate in selected:
                continue
            if len(selected) >= target_count:
                break

            # Check correlation with all already-selected assets
            correlations = [
                abs(self.corr_matrix.loc[candidate, s])
                for s in selected
                if candidate in self.corr_matrix.index and s in self.corr_matrix.columns
            ]
            if correlations and max(correlations) < max_corr:
                selected.append(candidate)

        if len(selected) < target_count:
            logger.warning(
                f"Only {len(selected)}/{target_count} assets meet correlation < {max_corr}. "
                f"Consider relaxing threshold or expanding asset universe."
            )
        else:
            logger.info(f"Selected {len(selected)} low-correlation assets (threshold={max_corr})")

        return selected

    def would_breach_threshold(
        self,
        current_portfolio: list[str],
        new_ticker: str,
        threshold: Optional[float] = None,
    ) -> bool:
        """
        Returns True if adding `new_ticker` would push the average portfolio
        correlation above the configured threshold. Used as a trade gate.
        """
        if self.corr_matrix is None:
            return False  # Can't enforce without data — allow trade

        threshold = threshold or self.settings.max_portfolio_correlation
        test_portfolio = current_portfolio + [new_ticker]
        valid = [t for t in test_portfolio if t in self.corr_matrix.index]

        if len(valid) < 2:
            return False

        sub = self.corr_matrix.loc[valid, valid]
        n = len(valid)
        # Average of upper triangle (excluding diagonal)
        upper = sub.where(np.triu(np.ones(sub.shape), k=1).astype(bool))
        avg_corr = upper.stack().abs().mean()

        breaches = avg_corr > threshold
        if breaches:
            logger.warning(
                f"Adding {new_ticker} would raise avg portfolio correlation "
                f"to {avg_corr:.3f} > threshold {threshold:.3f} — REJECTED"
            )
        return breaches

    def get_portfolio_correlation_stats(self, portfolio: list[str]) -> dict:
        """Return correlation statistics for a given portfolio."""
        if self.corr_matrix is None:
            return {}

        valid = [t for t in portfolio if t in self.corr_matrix.index]
        if len(valid) < 2:
            return {}

        sub = self.corr_matrix.loc[valid, valid]
        upper = sub.where(np.triu(np.ones(sub.shape), k=1).astype(bool))
        values = upper.stack().abs()

        return {
            "assets": valid,
            "avg_correlation": round(values.mean(), 4),
            "max_correlation": round(values.max(), 4),
            "min_correlation": round(values.min(), 4),
            "meets_dalio_rule": values.max() < self.settings.max_portfolio_correlation,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }

    def get_most_correlated_pairs(self, top_n: int = 10) -> list[dict]:
        """Return the top-N most correlated asset pairs (for risk reporting)."""
        if self.corr_matrix is None:
            return []

        pairs = []
        cols = list(self.corr_matrix.columns)
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                pairs.append({
                    "asset_a": cols[i],
                    "asset_b": cols[j],
                    "correlation": round(self.corr_matrix.iloc[i, j], 4),
                })

        pairs.sort(key=lambda x: abs(x["correlation"]), reverse=True)
        return pairs[:top_n]

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _build_returns_matrix(self, tickers: list[str]) -> pd.DataFrame:
        """Fetch historical data for all tickers and compute log returns."""
        returns = {}
        for ticker in tickers:
            df = self.fetcher.get_historical_data(ticker, period="2y", interval="1d")
            if df.empty or len(df) < 30:
                logger.debug(f"Skipping {ticker}: insufficient history")
                continue
            ret = self.fetcher.compute_returns(df)
            returns[ticker] = ret

        if not returns:
            return pd.DataFrame()

        combined = pd.DataFrame(returns)
        combined = combined.dropna(thresh=int(len(combined) * 0.7), axis=1)
        combined = combined.fillna(0)
        return combined
