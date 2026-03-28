"""
Economic Quadrant Engine — Dalio's "Economic Machine".

Combines macro data (GDP, CPI) + market sentiment to classify the
current global economic environment into one of 4 quadrants and
determine which assets to favour / avoid.
"""

from loguru import logger
from typing import Optional

from data.ingestion.macro_data import MacroDataFetcher
from engines.sentiment_engine import SentimentEngine
from config.assets import QUADRANTS, get_assets_by_quadrant


class QuadrantEngine:
    """
    Classifies the economic environment and provides asset recommendations
    based on Dalio's All Weather / Economic Machine framework.
    """

    # Quadrant label to human description
    QUADRANT_DESCRIPTIONS = {
        "rising_growth": (
            "RISING GROWTH: Economy expanding. Favour equities, commodities, "
            "corporate bonds. Reduce nominal bonds."
        ),
        "falling_growth": (
            "FALLING GROWTH: Recessionary pressure. Favour long-duration bonds, "
            "defensive equities. Reduce cyclicals & commodities."
        ),
        "rising_inflation": (
            "RISING INFLATION / STAGFLATION: Prices rising faster than growth. "
            "Favour gold, inflation-linked bonds, energy, real assets. "
            "Reduce nominal bonds & growth equities."
        ),
        "falling_inflation": (
            "FALLING INFLATION: Disinflation / deflation. Favour equities, "
            "nominal bonds, consumer staples. Reduce commodities & gold."
        ),
        "unknown": "QUADRANT UNCLEAR: Insufficient macro data. Use neutral positioning.",
    }

    def __init__(self):
        self.macro = MacroDataFetcher()
        self.sentiment = SentimentEngine()
        self._current_quadrant: Optional[str] = None
        self._macro_snapshot: Optional[dict] = None
        self._sentiment_summary: Optional[dict] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, country: str = "AUS") -> dict:
        """
        Full quadrant classification pipeline:
          1. Fetch macro snapshot (GDP + CPI trends).
          2. Get news sentiment summary.
          3. Reconcile into a single quadrant verdict.

        Returns full context dict used in Systematic Justification.
        """
        logger.info(f"Classifying economic quadrant for {country}...")

        # Step 1: Macro
        self._macro_snapshot = self.macro.get_all_macro_snapshot(country)
        macro_quadrant = self.macro.classify_quadrant(self._macro_snapshot)

        # Step 2: Sentiment
        try:
            self._sentiment_summary = self.sentiment.get_market_sentiment_summary()
            sentiment_quadrant = self._sentiment_summary.get("dominant_quadrant", "unknown")
        except Exception as e:
            logger.warning(f"Sentiment classification failed: {e}")
            sentiment_quadrant = "unknown"
            self._sentiment_summary = {}

        # Step 3: Reconcile — macro is primary, sentiment is a tie-breaker
        if macro_quadrant != "unknown":
            final_quadrant = macro_quadrant
        elif sentiment_quadrant != "unknown":
            final_quadrant = sentiment_quadrant
        else:
            final_quadrant = "unknown"

        self._current_quadrant = final_quadrant

        conflict_elevated = self._sentiment_summary.get("conflict_risk_elevated", False)
        if conflict_elevated:
            logger.warning(
                "CONFLICT RISK ELEVATED — bias toward gold, bonds, defensive assets."
            )

        result = {
            "quadrant": final_quadrant,
            "description": self.QUADRANT_DESCRIPTIONS.get(final_quadrant, ""),
            "macro_quadrant": macro_quadrant,
            "sentiment_quadrant": sentiment_quadrant,
            "macro_snapshot": self._macro_snapshot,
            "conflict_risk_elevated": conflict_elevated,
            "favoured_assets": QUADRANTS.get(final_quadrant, {}).get("favored", []),
            "avoid_assets": QUADRANTS.get(final_quadrant, {}).get("avoid", []),
            "recommended_tickers": list(get_assets_by_quadrant(final_quadrant).keys()),
        }

        logger.info(
            f"Quadrant classified: {final_quadrant} "
            f"(macro={macro_quadrant}, sentiment={sentiment_quadrant}, "
            f"conflict={conflict_elevated})"
        )
        return result

    def get_asset_quadrant_fit(self, ticker: str, asset_info: dict) -> dict:
        """
        Score how well a single asset fits the current quadrant.

        Returns:
            {
              "fit": "strong|moderate|weak|avoid",
              "score": 0-100,
              "reason": str
            }
        """
        if self._current_quadrant is None:
            return {"fit": "unknown", "score": 50, "reason": "Quadrant not yet classified."}

        asset_bias = asset_info.get("quadrant_bias", "unknown")
        current = self._current_quadrant
        quadrant_data = QUADRANTS.get(current, {})
        favored_types = quadrant_data.get("favored", [])
        avoid_types = quadrant_data.get("avoid", [])
        asset_type = asset_info.get("type", asset_info.get("sector", "")).lower().replace(" ", "_")

        if asset_bias == current:
            fit, score = "strong", 90
            reason = f"{ticker} is directly aligned with {current} quadrant."
        elif any(ft in asset_type for ft in favored_types):
            fit, score = "moderate", 65
            reason = f"{ticker} asset class ({asset_type}) is favoured in {current}."
        elif any(av in asset_type for av in avoid_types):
            fit, score = "avoid", 15
            reason = f"{ticker} asset class ({asset_type}) is unfavoured in {current}."
        else:
            fit, score = "weak", 40
            reason = f"{ticker} has neutral quadrant alignment for {current}."

        return {"fit": fit, "score": score, "reason": reason}

    def get_current_quadrant(self) -> Optional[str]:
        return self._current_quadrant

    def get_narrative(self) -> str:
        """Human-readable description of the current economic environment."""
        if not self._current_quadrant:
            return "Economic quadrant not yet determined. Run classify() first."

        macro = self._macro_snapshot or {}
        gdp = macro.get("gdp", {})
        cpi = macro.get("cpi", {})
        sentiment = self._sentiment_summary or {}

        lines = [
            f"=== DALIO ECONOMIC MACHINE — CURRENT STATE ===",
            f"Quadrant: {self._current_quadrant.upper().replace('_', ' ')}",
            f"",
            f"GDP: {gdp.get('value', 'N/A')}% ({gdp.get('trend', 'N/A')})",
            f"CPI: {cpi.get('value', 'N/A')}% ({cpi.get('trend', 'N/A')})",
            f"",
            f"News Sentiment (dominant): {sentiment.get('dominant_quadrant', 'N/A')}",
            f"Conflict Risk Elevated: {sentiment.get('conflict_risk_elevated', False)}",
            f"",
            self.QUADRANT_DESCRIPTIONS.get(self._current_quadrant, ""),
        ]
        return "\n".join(lines)
