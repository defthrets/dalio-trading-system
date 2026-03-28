"""
Macro economic data ingestion for Dalio's Economic Machine.
Tracks GDP growth, CPI inflation, interest rates, and unemployment.
Used to classify the current economic quadrant.
"""

import pandas as pd
import requests
from datetime import datetime
from loguru import logger

from config.settings import get_settings


class MacroDataFetcher:
    """Fetches macro indicators: GDP, CPI, interest rates, unemployment."""

    EODHD_BASE = "https://eodhd.com/api"

    def __init__(self):
        self.settings = get_settings()

    def get_gdp_data(self, country: str = "AUS") -> pd.DataFrame:
        """Fetch GDP growth rate time series."""
        return self._fetch_eodhd_macro(country, "gdp_growth_annual")

    def get_cpi_data(self, country: str = "AUS") -> pd.DataFrame:
        """Fetch Consumer Price Index (inflation) time series."""
        return self._fetch_eodhd_macro(country, "inflation_consumer_prices_annual")

    def get_interest_rate(self, country: str = "AUS") -> pd.DataFrame:
        """Fetch central bank interest rate."""
        return self._fetch_eodhd_macro(country, "real_interest_rate")

    def get_unemployment(self, country: str = "AUS") -> pd.DataFrame:
        """Fetch unemployment rate."""
        return self._fetch_eodhd_macro(country, "unemployment_total")

    def get_all_macro_snapshot(self, country: str = "AUS") -> dict:
        """
        Get a current snapshot of all macro indicators for quadrant classification.
        Returns latest values and their trends (rising/falling).
        """
        gdp = self.get_gdp_data(country)
        cpi = self.get_cpi_data(country)
        rates = self.get_interest_rate(country)
        unemployment = self.get_unemployment(country)

        snapshot = {}

        for name, df in [("gdp", gdp), ("cpi", cpi), ("interest_rate", rates), ("unemployment", unemployment)]:
            if df.empty or len(df) < 2:
                snapshot[name] = {"value": None, "trend": "unknown"}
                continue

            latest = df.iloc[-1]["value"]
            previous = df.iloc[-2]["value"]
            trend = "rising" if latest > previous else "falling"
            snapshot[name] = {
                "value": latest,
                "previous": previous,
                "trend": trend,
                "date": str(df.index[-1].date()),
            }

        return snapshot

    def classify_quadrant(self, snapshot: dict) -> str:
        """
        Classify the current economic environment into one of Dalio's 4 quadrants
        based on growth (GDP) and inflation (CPI) trends.

        Quadrants:
          - rising_growth + rising_inflation   -> "rising_growth"  (growth dominant)
          - rising_growth + falling_inflation   -> "rising_growth"
          - falling_growth + rising_inflation   -> "rising_inflation" (stagflation)
          - falling_growth + falling_inflation  -> "falling_growth"  (deflation/recession)
        """
        gdp_trend = snapshot.get("gdp", {}).get("trend", "unknown")
        cpi_trend = snapshot.get("cpi", {}).get("trend", "unknown")

        if gdp_trend == "rising" and cpi_trend == "rising":
            return "rising_growth"
        elif gdp_trend == "rising" and cpi_trend == "falling":
            return "falling_inflation"
        elif gdp_trend == "falling" and cpi_trend == "rising":
            return "rising_inflation"
        elif gdp_trend == "falling" and cpi_trend == "falling":
            return "falling_growth"
        else:
            return "unknown"

    # --- Private ---

    def _fetch_eodhd_macro(self, country: str, indicator: str) -> pd.DataFrame:
        """Fetch a macro indicator from EODHD."""
        if not self.settings.eodhd_api_key:
            logger.warning("No EODHD API key configured, returning empty macro data")
            return pd.DataFrame()

        try:
            url = f"{self.EODHD_BASE}/macro-indicator/{country}"
            params = {
                "api_token": self.settings.eodhd_api_key,
                "indicator": indicator,
                "fmt": "json",
            }
            response = requests.get(url, params=params, timeout=30)
            data = response.json()

            if not data or isinstance(data, dict):
                logger.warning(f"No data for {indicator} in {country}")
                return pd.DataFrame()

            df = pd.DataFrame(data)
            if "Date" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.set_index("Date").sort_index()
            if "Value" in df.columns:
                df = df.rename(columns={"Value": "value"})
            df["value"] = pd.to_numeric(df["value"], errors="coerce")

            return df
        except Exception as e:
            logger.error(f"EODHD macro fetch failed ({indicator}, {country}): {e}")
            return pd.DataFrame()
