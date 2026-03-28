"""
News data ingestion from Finnhub and NewsAPI.
Fetches market news, geopolitical events, military conflicts,
business deals/collapses, and stock-specific news for sentiment analysis.
"""

import requests
from datetime import datetime, timedelta
from loguru import logger
from typing import Optional

from config.settings import get_settings


class NewsDataFetcher:
    """Fetches news from Finnhub and NewsAPI for the sentiment pipeline."""

    FINNHUB_BASE = "https://finnhub.io/api/v1"
    NEWSAPI_BASE = "https://newsapi.org/v2"

    # Keywords for Dalio-relevant global scanning
    GEOPOLITICAL_KEYWORDS = [
        "war", "military", "conflict", "sanctions", "invasion",
        "nato", "nuclear", "missile", "troops", "ceasefire",
    ]
    ECONOMIC_KEYWORDS = [
        "gdp", "inflation", "interest rate", "federal reserve", "rba",
        "recession", "unemployment", "stimulus", "trade war", "tariff",
    ]
    BUSINESS_KEYWORDS = [
        "acquisition", "merger", "bankruptcy", "ipo", "earnings",
        "collapse", "default", "restructuring", "layoffs", "profit warning",
    ]

    def __init__(self):
        self.settings = get_settings()

    def get_market_news(self, category: str = "general") -> list[dict]:
        """Fetch general market news from Finnhub."""
        if not self.settings.finnhub_api_key:
            logger.warning("No Finnhub API key configured")
            return []

        try:
            url = f"{self.FINNHUB_BASE}/news"
            params = {
                "category": category,
                "token": self.settings.finnhub_api_key,
            }
            response = requests.get(url, params=params, timeout=30)
            articles = response.json()

            return [
                {
                    "title": a.get("headline", ""),
                    "summary": a.get("summary", ""),
                    "source": a.get("source", ""),
                    "url": a.get("url", ""),
                    "timestamp": datetime.fromtimestamp(a.get("datetime", 0)).isoformat(),
                    "category": category,
                    "related": a.get("related", ""),
                }
                for a in articles
                if a.get("headline")
            ]
        except Exception as e:
            logger.error(f"Finnhub market news fetch failed: {e}")
            return []

    def get_stock_news(self, ticker: str, days_back: int = 7) -> list[dict]:
        """Fetch news for a specific stock ticker from Finnhub."""
        if not self.settings.finnhub_api_key:
            return []

        try:
            end = datetime.now()
            start = end - timedelta(days=days_back)
            url = f"{self.FINNHUB_BASE}/company-news"
            params = {
                "symbol": ticker.replace(".AX", ""),
                "from": start.strftime("%Y-%m-%d"),
                "to": end.strftime("%Y-%m-%d"),
                "token": self.settings.finnhub_api_key,
            }
            response = requests.get(url, params=params, timeout=30)
            articles = response.json()

            return [
                {
                    "title": a.get("headline", ""),
                    "summary": a.get("summary", ""),
                    "source": a.get("source", ""),
                    "url": a.get("url", ""),
                    "timestamp": datetime.fromtimestamp(a.get("datetime", 0)).isoformat(),
                    "ticker": ticker,
                }
                for a in articles
                if a.get("headline")
            ]
        except Exception as e:
            logger.error(f"Finnhub stock news failed for {ticker}: {e}")
            return []

    def get_global_news(
        self,
        query: Optional[str] = None,
        category: str = "business",
        days_back: int = 3,
    ) -> list[dict]:
        """Fetch global news from NewsAPI for geopolitical/macro scanning."""
        if not self.settings.newsapi_api_key:
            logger.warning("No NewsAPI key configured")
            return []

        try:
            end = datetime.now()
            start = end - timedelta(days=days_back)

            if query:
                url = f"{self.NEWSAPI_BASE}/everything"
                params = {
                    "q": query,
                    "from": start.strftime("%Y-%m-%d"),
                    "to": end.strftime("%Y-%m-%d"),
                    "sortBy": "relevancy",
                    "language": "en",
                    "pageSize": 50,
                    "apiKey": self.settings.newsapi_api_key,
                }
            else:
                url = f"{self.NEWSAPI_BASE}/top-headlines"
                params = {
                    "category": category,
                    "language": "en",
                    "pageSize": 50,
                    "apiKey": self.settings.newsapi_api_key,
                }

            response = requests.get(url, params=params, timeout=30)
            data = response.json()

            articles = data.get("articles", [])
            return [
                {
                    "title": a.get("title", ""),
                    "summary": a.get("description", ""),
                    "content": a.get("content", ""),
                    "source": a.get("source", {}).get("name", ""),
                    "url": a.get("url", ""),
                    "timestamp": a.get("publishedAt", ""),
                    "query": query or category,
                }
                for a in articles
                if a.get("title") and a["title"] != "[Removed]"
            ]
        except Exception as e:
            logger.error(f"NewsAPI fetch failed: {e}")
            return []

    def scan_geopolitical_events(self) -> list[dict]:
        """Scan for military conflicts, sanctions, and geopolitical tensions."""
        all_articles = []
        for keyword in self.GEOPOLITICAL_KEYWORDS[:5]:  # Top 5 to avoid rate limits
            articles = self.get_global_news(query=keyword, days_back=3)
            for a in articles:
                a["event_type"] = "geopolitical"
            all_articles.extend(articles)
        return all_articles

    def scan_economic_events(self) -> list[dict]:
        """Scan for macro economic news (GDP, rates, inflation)."""
        all_articles = []
        for keyword in self.ECONOMIC_KEYWORDS[:5]:
            articles = self.get_global_news(query=keyword, days_back=3)
            for a in articles:
                a["event_type"] = "economic"
            all_articles.extend(articles)
        return all_articles

    def scan_business_events(self) -> list[dict]:
        """Scan for major business deals, collapses, and M&A activity."""
        all_articles = []
        for keyword in self.BUSINESS_KEYWORDS[:5]:
            articles = self.get_global_news(query=keyword, days_back=3)
            for a in articles:
                a["event_type"] = "business"
            all_articles.extend(articles)
        return all_articles

    def get_full_news_scan(self) -> dict:
        """
        Comprehensive news scan across all categories.
        Returns categorized articles for the sentiment pipeline.
        """
        return {
            "market": self.get_market_news(),
            "geopolitical": self.scan_geopolitical_events(),
            "economic": self.scan_economic_events(),
            "business": self.scan_business_events(),
            "timestamp": datetime.utcnow().isoformat(),
        }
