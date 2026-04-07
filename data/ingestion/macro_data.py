"""
Macro economic data ingestion for Dalio's Economic Machine.
Tracks GDP growth, CPI inflation, interest rates, and unemployment.
Used to classify the current economic quadrant.
"""

import pandas as pd
import requests
import yfinance as yf
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
        Tries EODHD first, falls back to yfinance market proxies.
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

        # If EODHD returned all unknowns, use yfinance market proxies
        all_unknown = all(
            snapshot.get(k, {}).get("trend") == "unknown"
            for k in ["gdp", "cpi", "interest_rate", "unemployment"]
        )
        if all_unknown:
            logger.info("EODHD data unavailable — using yfinance market proxy fallback")
            snapshot = self._yfinance_macro_fallback()

        return snapshot

    def _yfinance_macro_fallback(self) -> dict:
        """
        Estimate macro trends from freely available market data:
          - Growth proxy: ASX200 (^AXJO) 3-month trend
          - Inflation proxy: Gold (GC=F) vs Bond yield (^TNX) spread trend
          - Interest rate proxy: AU 10Y bond yield (^GSPC stand-in via ^TNX)
          - Unemployment proxy: consumer discretionary vs staples ratio
        """
        snapshot = {
            "gdp": {"value": None, "trend": "unknown"},
            "cpi": {"value": None, "trend": "unknown"},
            "interest_rate": {"value": None, "trend": "unknown"},
            "unemployment": {"value": None, "trend": "unknown"},
        }

        try:
            # Growth proxy: ASX200 3-month trend
            asx = yf.download("^AXJO", period="6mo", interval="1wk", progress=False)
            if len(asx) >= 12:
                recent = asx["Close"].iloc[-1]
                past = asx["Close"].iloc[-12]
                pct = float((recent - past) / past * 100)
                snapshot["gdp"] = {
                    "value": round(pct, 2),
                    "previous": 0,
                    "trend": "rising" if pct > 0 else "falling",
                    "date": str(asx.index[-1].date()),
                    "source": "yfinance_proxy (ASX200 3mo return)",
                }
        except Exception as e:
            logger.debug(f"Growth proxy failed: {e}")

        try:
            # Inflation proxy: Gold price trend (rising gold = rising inflation expectations)
            gold = yf.download("GC=F", period="6mo", interval="1wk", progress=False)
            if len(gold) >= 12:
                recent = gold["Close"].iloc[-1]
                past = gold["Close"].iloc[-12]
                pct = float((recent - past) / past * 100)
                snapshot["cpi"] = {
                    "value": round(pct, 2),
                    "previous": 0,
                    "trend": "rising" if pct > 2 else "falling",
                    "date": str(gold.index[-1].date()),
                    "source": "yfinance_proxy (Gold 3mo change)",
                }
        except Exception as e:
            logger.debug(f"Inflation proxy failed: {e}")

        try:
            # Interest rate proxy: US 10Y Treasury yield trend
            tnx = yf.download("^TNX", period="6mo", interval="1wk", progress=False)
            if len(tnx) >= 12:
                recent = float(tnx["Close"].iloc[-1])
                past = float(tnx["Close"].iloc[-12])
                snapshot["interest_rate"] = {
                    "value": round(recent, 2),
                    "previous": round(past, 2),
                    "trend": "rising" if recent > past else "falling",
                    "date": str(tnx.index[-1].date()),
                    "source": "yfinance_proxy (US 10Y yield)",
                }
        except Exception as e:
            logger.debug(f"Rate proxy failed: {e}")

        try:
            # Unemployment proxy: consumer discretionary (XLY) vs staples (XLP)
            # Rising ratio = confidence/low unemployment, falling = rising unemployment
            xly = yf.download("XLY", period="6mo", interval="1wk", progress=False)
            xlp = yf.download("XLP", period="6mo", interval="1wk", progress=False)
            if len(xly) >= 12 and len(xlp) >= 12:
                ratio_now = float(xly["Close"].iloc[-1] / xlp["Close"].iloc[-1])
                ratio_past = float(xly["Close"].iloc[-12] / xlp["Close"].iloc[-12])
                # Falling ratio = rising unemployment
                snapshot["unemployment"] = {
                    "value": round(ratio_now, 4),
                    "previous": round(ratio_past, 4),
                    "trend": "falling" if ratio_now > ratio_past else "rising",
                    "date": str(xly.index[-1].date()),
                    "source": "yfinance_proxy (XLY/XLP ratio)",
                }
        except Exception as e:
            logger.debug(f"Unemployment proxy failed: {e}")

        proxied = sum(1 for v in snapshot.values() if v.get("trend") != "unknown")
        logger.info(f"yfinance macro fallback: {proxied}/4 indicators estimated")
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
