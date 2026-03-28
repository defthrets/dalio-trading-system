"""
Market data ingestion from Alpha Vantage, iTick, and yfinance.
Handles both real-time and deep historical data for ASX equities and commodities.
"""

import pandas as pd
import numpy as np
import yfinance as yf
import requests
from datetime import datetime, timedelta
from loguru import logger
from typing import Optional

from config.settings import get_settings


class MarketDataFetcher:
    """Fetches and manages market data from multiple sources."""

    def __init__(self):
        self.settings = get_settings()
        self._cache: dict[str, pd.DataFrame] = {}

    def get_historical_data(
        self,
        ticker: str,
        period: str = "max",
        interval: str = "1d",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV data. Uses yfinance as primary, Alpha Vantage as fallback.

        Args:
            ticker: Asset ticker symbol (e.g., 'BHP.AX', 'GC=F')
            period: Data period ('max' for all available history)
            interval: Candle interval ('1d', '1h', '5m')
            start: Start date string 'YYYY-MM-DD'
            end: End date string 'YYYY-MM-DD'
        """
        cache_key = f"{ticker}_{interval}_{start}_{end}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            df = self._fetch_yfinance(ticker, period, interval, start, end)
            if df.empty:
                logger.warning(f"yfinance returned empty for {ticker}, trying Alpha Vantage")
                df = self._fetch_alpha_vantage(ticker, interval)
        except Exception as e:
            logger.error(f"yfinance failed for {ticker}: {e}")
            df = self._fetch_alpha_vantage(ticker, interval)

        if not df.empty:
            df = self._clean_data(df)
            self._cache[cache_key] = df
            logger.info(f"Fetched {len(df)} bars for {ticker} ({df.index[0]} to {df.index[-1]})")
        else:
            logger.error(f"All data sources failed for {ticker}")

        return df

    def get_intraday_data(self, ticker: str, interval: str = "5m") -> pd.DataFrame:
        """Fetch intraday data for day trading signals."""
        try:
            stock = yf.Ticker(ticker)
            df = stock.history(period="5d", interval=interval)
            return self._clean_data(df)
        except Exception as e:
            logger.error(f"Intraday fetch failed for {ticker}: {e}")
            return pd.DataFrame()

    def get_realtime_price(self, ticker: str) -> dict:
        """Get latest price and volume for a ticker."""
        try:
            stock = yf.Ticker(ticker)
            info = stock.fast_info
            return {
                "ticker": ticker,
                "price": info.get("lastPrice", 0),
                "volume": info.get("lastVolume", 0),
                "market_cap": info.get("marketCap", 0),
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Realtime price fetch failed for {ticker}: {e}")
            return {"ticker": ticker, "price": 0, "error": str(e)}

    def get_bulk_historical(
        self, tickers: list[str], period: str = "max", interval: str = "1d"
    ) -> dict[str, pd.DataFrame]:
        """Fetch historical data for multiple tickers."""
        results = {}
        for ticker in tickers:
            df = self.get_historical_data(ticker, period=period, interval=interval)
            if not df.empty:
                results[ticker] = df
        return results

    def compute_returns(self, df: pd.DataFrame, column: str = "Close") -> pd.Series:
        """Compute log returns from price data."""
        return np.log(df[column] / df[column].shift(1)).dropna()

    def compute_volatility(
        self, df: pd.DataFrame, window: int = 21, column: str = "Close"
    ) -> pd.Series:
        """Compute rolling annualized volatility."""
        returns = self.compute_returns(df, column)
        return returns.rolling(window=window).std() * np.sqrt(252)

    # --- Private Methods ---

    def _fetch_yfinance(
        self,
        ticker: str,
        period: str,
        interval: str,
        start: Optional[str],
        end: Optional[str],
    ) -> pd.DataFrame:
        stock = yf.Ticker(ticker)
        if start and end:
            return stock.history(start=start, end=end, interval=interval)
        return stock.history(period=period, interval=interval)

    def _fetch_alpha_vantage(self, ticker: str, interval: str = "1d") -> pd.DataFrame:
        """Fallback to Alpha Vantage API."""
        if not self.settings.alpha_vantage_api_key:
            logger.warning("No Alpha Vantage API key configured")
            return pd.DataFrame()

        base_url = "https://www.alphavantage.co/query"
        if interval == "1d":
            params = {
                "function": "TIME_SERIES_DAILY",
                "symbol": ticker.replace(".AX", ".AU"),
                "outputsize": "full",
                "apikey": self.settings.alpha_vantage_api_key,
            }
        else:
            params = {
                "function": "TIME_SERIES_INTRADAY",
                "symbol": ticker.replace(".AX", ".AU"),
                "interval": interval,
                "outputsize": "full",
                "apikey": self.settings.alpha_vantage_api_key,
            }

        try:
            response = requests.get(base_url, params=params, timeout=30)
            data = response.json()

            time_series_key = [k for k in data.keys() if "Time Series" in k]
            if not time_series_key:
                return pd.DataFrame()

            ts = data[time_series_key[0]]
            df = pd.DataFrame.from_dict(ts, orient="index").astype(float)
            df.columns = ["Open", "High", "Low", "Close", "Volume"]
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            return df
        except Exception as e:
            logger.error(f"Alpha Vantage failed for {ticker}: {e}")
            return pd.DataFrame()

    def _fetch_itick(self, ticker: str) -> pd.DataFrame:
        """Fetch from iTick API for ASX real-time data."""
        if not self.settings.itick_api_key:
            return pd.DataFrame()

        try:
            url = f"https://api.itick.com/v1/stock/history"
            params = {
                "symbol": ticker,
                "apikey": self.settings.itick_api_key,
            }
            response = requests.get(url, params=params, timeout=30)
            data = response.json()
            if "data" in data:
                df = pd.DataFrame(data["data"])
                df.index = pd.to_datetime(df["date"])
                return df
        except Exception as e:
            logger.error(f"iTick failed for {ticker}: {e}")
        return pd.DataFrame()

    def _clean_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean and standardize OHLCV data."""
        if df.empty:
            return df

        expected_cols = ["Open", "High", "Low", "Close", "Volume"]
        for col in expected_cols:
            if col not in df.columns:
                df[col] = np.nan

        df = df[expected_cols].copy()
        df = df.dropna(subset=["Close"])
        df = df[df["Close"] > 0]
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="last")]

        return df
