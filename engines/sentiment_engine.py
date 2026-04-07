"""
Sentiment Engine — FinBERT-powered news analysis.

Classifies each news article into a sentiment score AND maps it
to Dalio's 4 economic quadrants based on entity & keyword context.
"""

import torch
import numpy as np
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from loguru import logger
from typing import Optional

from config.settings import get_settings
from data.ingestion.news_data import NewsDataFetcher


QUADRANT_KEYWORDS = {
    "rising_growth": [
        "gdp growth", "expansion", "bull market", "strong earnings",
        "hiring surge", "consumer spending", "business investment",
        "trade deal", "stimulus", "infrastructure spending",
    ],
    "falling_growth": [
        "recession", "contraction", "layoffs", "bankruptcy", "default",
        "bear market", "earnings miss", "slowdown", "gdp decline",
        "unemployment rise", "credit crunch",
    ],
    "rising_inflation": [
        "inflation", "cpi surge", "price hike", "oil price", "commodity surge",
        "supply chain", "shortage", "war", "sanctions", "rate hike",
        "energy crisis", "wage growth",
    ],
    "falling_inflation": [
        "deflation", "disinflation", "price drop", "rate cut", "oil crash",
        "commodity selloff", "demand destruction", "currency strength",
    ],
}

CONFLICT_RISK_KEYWORDS = [
    "war", "invasion", "military strike", "nuclear", "sanctions",
    "conflict", "troops", "missile", "ceasefire", "coup",
]


class SentimentEngine:
    """
    Runs FinBERT sentiment analysis on news articles and maps
    results to Dalio's economic quadrants.
    """

    def __init__(self):
        self.settings = get_settings()
        self.news_fetcher = NewsDataFetcher()
        self._model = None
        self._tokenizer = None
        self._load_failed = False
        self._device = "cuda" if torch.cuda.is_available() else "cpu"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_model(self):
        """Lazy-load FinBERT (downloads once, ~400 MB). Won't retry on failure."""
        if self._model is not None:
            return
        if self._load_failed:
            return  # Already failed — use keyword fallback instead of retrying

        model_name = self.settings.finbert_model_name
        logger.info(f"Loading FinBERT model: {model_name} on {self._device}")
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(model_name)
            self._model.to(self._device)
            self._model.eval()
            logger.info("FinBERT loaded successfully.")
        except Exception as e:
            logger.error(f"FinBERT load failed (will use keyword fallback): {e}")
            self._model = None
            self._load_failed = True

    def analyze_article(self, title: str, summary: str = "") -> dict:
        """
        Run FinBERT on a single article.

        Returns:
            {
              "sentiment": "positive|negative|neutral",
              "score": float (−1 to +1),
              "quadrant": str,
              "conflict_risk": bool,
              "raw_probs": {"positive": float, "negative": float, "neutral": float}
            }
        """
        self.load_model()
        text = f"{title}. {summary}"[:512]

        sentiment, probs = self._run_finbert(text)
        score = probs.get("positive", 0) - probs.get("negative", 0)

        quadrant = self._classify_quadrant(text)
        conflict_risk = self._detect_conflict(text)

        return {
            "sentiment": sentiment,
            "score": round(score, 4),
            "quadrant": quadrant,
            "conflict_risk": conflict_risk,
            "raw_probs": {k: round(v, 4) for k, v in probs.items()},
        }

    def analyze_batch(self, articles: list[dict]) -> list[dict]:
        """Analyze a list of article dicts (must have 'title' and 'summary' keys)."""
        self.load_model()
        results = []
        batch_size = self.settings.sentiment_batch_size

        for i in range(0, len(articles), batch_size):
            batch = articles[i:i + batch_size]
            texts = [
                f"{a.get('title', '')}. {a.get('summary', '')}"[:512]
                for a in batch
            ]
            sentiments, probs_list = self._run_finbert_batch(texts)

            for article, sentiment, probs in zip(batch, sentiments, probs_list):
                text = f"{article.get('title', '')} {article.get('summary', '')}"
                score = probs.get("positive", 0) - probs.get("negative", 0)
                result = dict(article)
                result.update({
                    "sentiment": sentiment,
                    "score": round(score, 4),
                    "quadrant": self._classify_quadrant(text),
                    "conflict_risk": self._detect_conflict(text),
                    "raw_probs": {k: round(v, 4) for k, v in probs.items()},
                })
                results.append(result)

        return results

    def get_market_sentiment_summary(self) -> dict:
        """
        Full pipeline: fetch news → run FinBERT → aggregate into a
        Dalio quadrant-aware sentiment report.
        """
        logger.info("Running full market sentiment scan...")
        news = self.news_fetcher.get_full_news_scan()

        all_articles = (
            news.get("market", [])
            + news.get("geopolitical", [])
            + news.get("economic", [])
            + news.get("business", [])
        )

        if not all_articles:
            logger.warning("No news articles retrieved.")
            return {}

        analyzed = self.analyze_batch(all_articles)

        # Aggregate by quadrant
        quadrant_scores: dict[str, list[float]] = {
            "rising_growth": [],
            "falling_growth": [],
            "rising_inflation": [],
            "falling_inflation": [],
            "unknown": [],
        }
        conflict_count = 0

        for a in analyzed:
            q = a.get("quadrant", "unknown")
            quadrant_scores.setdefault(q, []).append(a.get("score", 0))
            if a.get("conflict_risk"):
                conflict_count += 1

        summary = {
            "total_articles": len(analyzed),
            "conflict_risk_articles": conflict_count,
            "conflict_risk_elevated": conflict_count > 5,
            "quadrant_sentiment": {},
        }

        for q, scores in quadrant_scores.items():
            if scores:
                summary["quadrant_sentiment"][q] = {
                    "avg_score": round(np.mean(scores), 4),
                    "article_count": len(scores),
                    "bullish_pct": round(
                        sum(1 for s in scores if s > 0.1) / len(scores) * 100, 1
                    ),
                }

        # Dominant quadrant by article count
        if summary["quadrant_sentiment"]:
            dominant = max(
                summary["quadrant_sentiment"],
                key=lambda q: summary["quadrant_sentiment"][q]["article_count"],
            )
            summary["dominant_quadrant"] = dominant
        else:
            summary["dominant_quadrant"] = "unknown"

        logger.info(
            f"Sentiment scan complete: {len(analyzed)} articles, "
            f"dominant quadrant: {summary['dominant_quadrant']}, "
            f"conflict alerts: {conflict_count}"
        )
        return summary

    def get_ticker_sentiment(self, ticker: str) -> dict:
        """Fetch and analyse news for a specific ticker."""
        articles = self.news_fetcher.get_stock_news(ticker, days_back=7)
        if not articles:
            return {"ticker": ticker, "sentiment": "neutral", "score": 0, "articles": 0}

        analyzed = self.analyze_batch(articles)
        scores = [a["score"] for a in analyzed]
        avg_score = np.mean(scores) if scores else 0

        return {
            "ticker": ticker,
            "sentiment": "positive" if avg_score > 0.05 else "negative" if avg_score < -0.05 else "neutral",
            "score": round(avg_score, 4),
            "articles": len(analyzed),
            "conflict_risk": any(a.get("conflict_risk") for a in analyzed),
        }

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _run_finbert(self, text: str) -> tuple[str, dict]:
        if self._model is None:
            return "neutral", {"positive": 0.33, "negative": 0.33, "neutral": 0.34}

        try:
            inputs = self._tokenizer(
                text, return_tensors="pt", truncation=True, max_length=512
            ).to(self._device)
            with torch.no_grad():
                outputs = self._model(**inputs)

            probs = torch.softmax(outputs.logits, dim=1).squeeze().cpu().numpy()
            labels = ["positive", "negative", "neutral"]
            prob_dict = dict(zip(labels, probs.tolist()))
            sentiment = labels[int(np.argmax(probs))]
            return sentiment, prob_dict
        except Exception as e:
            logger.error(f"FinBERT inference failed: {e}")
            return "neutral", {"positive": 0.33, "negative": 0.33, "neutral": 0.34}

    def _run_finbert_batch(self, texts: list[str]) -> tuple[list[str], list[dict]]:
        if self._model is None:
            neutral = {"positive": 0.33, "negative": 0.33, "neutral": 0.34}
            return ["neutral"] * len(texts), [neutral] * len(texts)

        try:
            inputs = self._tokenizer(
                texts,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True,
            ).to(self._device)

            with torch.no_grad():
                outputs = self._model(**inputs)

            probs = torch.softmax(outputs.logits, dim=1).cpu().numpy()
            labels = ["positive", "negative", "neutral"]
            sentiments = [labels[int(np.argmax(p))] for p in probs]
            prob_dicts = [dict(zip(labels, p.tolist())) for p in probs]
            return sentiments, prob_dicts
        except Exception as e:
            logger.error(f"FinBERT batch inference failed: {e}")
            neutral = {"positive": 0.33, "negative": 0.33, "neutral": 0.34}
            return ["neutral"] * len(texts), [neutral] * len(texts)

    def _classify_quadrant(self, text: str) -> str:
        """Map text keywords to the most relevant Dalio quadrant."""
        text_lower = text.lower()
        scores = {}
        for quadrant, keywords in QUADRANT_KEYWORDS.items():
            scores[quadrant] = sum(1 for kw in keywords if kw in text_lower)
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "unknown"

    def _detect_conflict(self, text: str) -> bool:
        """Flag articles with military/geopolitical conflict signals."""
        text_lower = text.lower()
        return any(kw in text_lower for kw in CONFLICT_RISK_KEYWORDS)
