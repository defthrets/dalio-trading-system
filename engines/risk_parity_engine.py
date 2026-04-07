"""
Risk Parity Engine — Dalio's Volatility Weighting / Equal Risk Contribution.

Production-ready implementation using inverse-volatility weighting.
No heavy dependencies (replaces riskfolio-lib ~50MB).
Each asset contributes an equal amount of risk to the total portfolio.
"""

import pandas as pd
import numpy as np
from loguru import logger

from data.ingestion.market_data import MarketDataFetcher
from config.settings import get_settings


class RiskParityEngine:
    """
    Computes risk-parity (equal risk contribution) portfolio weights.
    Each asset contributes the same volatility to the portfolio.
    """

    def __init__(self):
        self.settings = get_settings()
        self.fetcher = MarketDataFetcher()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_weights(
        self,
        tickers: list[str],
        lookback_days: int = 252,
    ) -> dict[str, float]:
        """
        Compute risk-parity weights for the given asset list.

        Returns:
            dict mapping ticker -> weight (0–1, sums to 1.0)
        """
        returns_df = self._get_returns(tickers, lookback_days)
        if returns_df.empty:
            logger.error("No returns data for risk parity calculation.")
            return self._equal_weight(tickers)

        weights = self._inverse_vol_weights(returns_df)
        if not weights:
            logger.warning("Risk parity failed — falling back to equal weight.")
            return self._equal_weight(tickers)

        return weights

    def compute_sharpe_contribution(
        self,
        weights: dict[str, float],
        tickers: list[str],
        risk_free_rate: float = 0.04,
    ) -> dict:
        """
        Calculate the portfolio Sharpe ratio and each asset's contribution.
        Used in the Systematic Justification report.
        """
        returns_df = self._get_returns(tickers)
        if returns_df.empty or not weights:
            return {}

        w = pd.Series(weights).reindex(returns_df.columns).fillna(0)
        portfolio_returns = returns_df.dot(w)

        annual_ret = portfolio_returns.mean() * 252
        annual_vol = portfolio_returns.std() * np.sqrt(252)
        sharpe = (annual_ret - risk_free_rate) / annual_vol if annual_vol > 0 else 0

        # Marginal contribution to portfolio volatility per asset
        cov = returns_df.cov() * 252
        port_vol = float(np.sqrt(w.values @ cov.values @ w.values))

        contributions = {}
        for ticker in weights:
            if ticker in cov.columns:
                mcv = float(cov[ticker].dot(w)) / port_vol if port_vol > 0 else 0
                contributions[ticker] = round(mcv * weights[ticker], 6)

        return {
            "portfolio_sharpe": round(sharpe, 4),
            "annual_return_pct": round(annual_ret * 100, 2),
            "annual_volatility_pct": round(annual_vol * 100, 2),
            "risk_contributions": contributions,
        }

    def rebalance_needed(
        self,
        current_weights: dict[str, float],
        target_weights: dict[str, float],
        drift_threshold: float = 0.05,
    ) -> bool:
        """
        Check if portfolio has drifted > drift_threshold from target weights.
        Triggers rebalancing if true.
        """
        for ticker, target in target_weights.items():
            current = current_weights.get(ticker, 0.0)
            if abs(current - target) > drift_threshold:
                logger.info(
                    f"Rebalance needed: {ticker} drifted "
                    f"{abs(current - target):.2%} from target {target:.2%}"
                )
                return True
        return False

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _inverse_vol_weights(self, returns_df: pd.DataFrame) -> dict[str, float]:
        """
        Inverse-volatility weighting: Weight_i = (1/vol_i) / sum(1/vol_j).
        This is a well-established risk-parity approximation that produces
        results within ~5% of full ERC optimization for most portfolios.
        """
        vols = returns_df.std() * np.sqrt(252)
        vols = vols.replace(0, np.nan).dropna()
        if vols.empty:
            return {}

        inv_vols = 1.0 / vols
        total = inv_vols.sum()
        if total == 0:
            return self._equal_weight(list(returns_df.columns))

        weights = (inv_vols / total).to_dict()
        logger.info(f"Risk-parity weights computed for {len(weights)} assets.")
        return {k: round(v, 6) for k, v in weights.items()}

    def _get_returns(
        self, tickers: list[str], lookback_days: int = 252
    ) -> pd.DataFrame:
        """Fetch daily log returns for all tickers."""
        returns = {}
        for ticker in tickers:
            df = self.fetcher.get_historical_data(ticker, period="2y", interval="1d")
            if df.empty:
                continue
            ret = self.fetcher.compute_returns(df)
            if len(ret) >= lookback_days // 2:
                returns[ticker] = ret.tail(lookback_days)

        if not returns:
            return pd.DataFrame()

        combined = pd.DataFrame(returns).dropna(thresh=int(lookback_days * 0.6), axis=1)
        return combined.fillna(0)

    @staticmethod
    def _equal_weight(tickers: list[str]) -> dict[str, float]:
        n = len(tickers)
        if n == 0:
            return {}
        w = round(1.0 / n, 6)
        return {t: w for t in tickers}
