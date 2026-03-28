"""
Signal Generator — Technical + Fundamental trade signals.

Combines:
  - Price action (trend, momentum, mean reversion)
  - Volume analysis
  - Technical indicators (RSI, MACD, Bollinger Bands, ATR)
  - Dalio quadrant alignment
  - FinBERT news sentiment for the ticker
  - Correlation gate (from correlation engine)

Each signal is classified: BUY / SELL / SHORT / HOLD with confidence score.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger
from typing import Optional

try:
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False
    logger.warning("ta library not available — using manual indicator calculations.")

from data.ingestion.market_data import MarketDataFetcher
from config.assets import get_all_assets


@dataclass
class TradeSignal:
    """A single trade signal with full context for Systematic Justification."""
    ticker: str
    action: str          # "BUY" | "SELL" | "SHORT" | "COVER" | "HOLD"
    direction: str       # "long" | "short" | "neutral"
    confidence: float    # 0.0 – 1.0
    price: float
    timestamp: str

    # Context for Systematic Justification
    quadrant: str = ""
    quadrant_fit: str = ""
    sentiment_score: float = 0.0
    sentiment_label: str = "neutral"
    conflict_risk: bool = False

    # Technical signals
    rsi: float = 0.0
    macd_signal: str = ""
    bb_position: str = ""    # "above_upper" | "below_lower" | "mid"
    trend: str = ""          # "uptrend" | "downtrend" | "sideways"
    atr: float = 0.0

    # Risk metrics
    suggested_stop_loss: float = 0.0
    suggested_take_profit: float = 0.0
    risk_reward_ratio: float = 0.0
    position_size_pct: float = 0.0   # % of portfolio

    # Options suggestion
    options_strategy: Optional[str] = None

    # Reasons
    reasons: list[str] = field(default_factory=list)


class SignalGenerator:
    """
    Generates buy/sell/short signals for all assets in the universe.
    Applies Dalio principles as a filter layer on top of technicals.
    """

    def __init__(self, quadrant_engine=None, sentiment_engine=None, correlation_engine=None):
        self.fetcher = MarketDataFetcher()
        self.quadrant_engine = quadrant_engine
        self.sentiment_engine = sentiment_engine
        self.correlation_engine = correlation_engine
        self._assets = get_all_assets()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_signal(
        self,
        ticker: str,
        current_portfolio: Optional[list[str]] = None,
        interval: str = "1d",
    ) -> Optional[TradeSignal]:
        """
        Generate a trade signal for a single ticker.
        Returns None if no actionable signal or if correlation gate blocks it.
        """
        asset_info = self._assets.get(ticker, {})

        # Fetch price data
        df = self.fetcher.get_historical_data(ticker, period="2y", interval=interval)
        if df.empty or len(df) < 50:
            logger.debug(f"Insufficient data for {ticker}")
            return None

        latest_price = float(df["Close"].iloc[-1])

        # Technical analysis
        tech = self._compute_technicals(df)

        # Quadrant alignment
        quadrant_fit = "unknown"
        if self.quadrant_engine and self.quadrant_engine.get_current_quadrant():
            fit_result = self.quadrant_engine.get_asset_quadrant_fit(ticker, asset_info)
            quadrant_fit = fit_result.get("fit", "unknown")
            if quadrant_fit == "avoid":
                logger.debug(f"Skipping {ticker}: quadrant says AVOID.")
                return None

        # Sentiment
        sentiment_score = 0.0
        sentiment_label = "neutral"
        conflict_risk = False
        if self.sentiment_engine:
            try:
                sent = self.sentiment_engine.get_ticker_sentiment(ticker)
                sentiment_score = sent.get("score", 0.0)
                sentiment_label = sent.get("sentiment", "neutral")
                conflict_risk = sent.get("conflict_risk", False)
            except Exception:
                pass

        # Determine action
        action, confidence, reasons = self._determine_action(
            tech, sentiment_score, quadrant_fit, asset_info
        )

        if action == "HOLD" and confidence < 0.55:
            return None

        # Correlation gate
        if (
            current_portfolio
            and self.correlation_engine
            and action in ("BUY", "COVER")
        ):
            if self.correlation_engine.would_breach_threshold(current_portfolio, ticker):
                reasons.append("BLOCKED: Would breach portfolio correlation threshold.")
                action = "HOLD"
                confidence = 0.0

        # Risk sizing: ATR-based stop
        atr = tech.get("atr", latest_price * 0.02)
        stop_loss = (
            latest_price - 2 * atr if action in ("BUY", "COVER")
            else latest_price + 2 * atr
        )
        take_profit = (
            latest_price + 3 * atr if action in ("BUY", "COVER")
            else latest_price - 3 * atr
        )
        rr = abs(take_profit - latest_price) / abs(latest_price - stop_loss) if abs(latest_price - stop_loss) > 0 else 0

        options_strategy = self._suggest_options(action, tech, asset_info)

        current_quadrant = (
            self.quadrant_engine.get_current_quadrant()
            if self.quadrant_engine else "unknown"
        )

        return TradeSignal(
            ticker=ticker,
            action=action,
            direction="long" if action in ("BUY", "COVER") else "short" if action == "SHORT" else "neutral",
            confidence=round(confidence, 4),
            price=latest_price,
            timestamp=datetime.utcnow().isoformat(),
            quadrant=current_quadrant or "unknown",
            quadrant_fit=quadrant_fit,
            sentiment_score=sentiment_score,
            sentiment_label=sentiment_label,
            conflict_risk=conflict_risk,
            rsi=tech.get("rsi", 0),
            macd_signal=tech.get("macd_signal", ""),
            bb_position=tech.get("bb_position", ""),
            trend=tech.get("trend", ""),
            atr=round(atr, 4),
            suggested_stop_loss=round(stop_loss, 4),
            suggested_take_profit=round(take_profit, 4),
            risk_reward_ratio=round(rr, 2),
            position_size_pct=round(self._kelly_position_size(confidence, rr), 2),
            options_strategy=options_strategy,
            reasons=reasons,
        )

    def scan_universe(
        self,
        current_portfolio: Optional[list[str]] = None,
        top_n: int = 10,
    ) -> list[TradeSignal]:
        """
        Scan all assets in the universe and return top N signals by confidence.
        This is the main method called by the AI agent.
        """
        signals = []
        for ticker in self._assets:
            try:
                signal = self.generate_signal(ticker, current_portfolio)
                if signal and signal.action != "HOLD":
                    signals.append(signal)
            except Exception as e:
                logger.error(f"Signal generation failed for {ticker}: {e}")

        signals.sort(key=lambda s: s.confidence, reverse=True)
        logger.info(f"Universe scan complete: {len(signals)} actionable signals found.")
        return signals[:top_n]

    def suggest_new_opportunities(self, exclude: Optional[list[str]] = None) -> list[TradeSignal]:
        """
        Identify assets NOT currently in portfolio that show strong signals.
        This implements the 'suggest new stocks' requirement.
        """
        exclude = exclude or []
        candidates = [t for t in self._assets if t not in exclude]

        signals = []
        for ticker in candidates:
            try:
                signal = self.generate_signal(ticker, exclude)
                if signal and signal.confidence >= 0.65 and signal.action != "HOLD":
                    signals.append(signal)
            except Exception as e:
                logger.debug(f"Error scanning {ticker}: {e}")

        signals.sort(key=lambda s: s.confidence * s.risk_reward_ratio, reverse=True)
        return signals[:5]

    # ------------------------------------------------------------------
    # Private — Technicals
    # ------------------------------------------------------------------

    def _compute_technicals(self, df: pd.DataFrame) -> dict:
        """Compute all technical indicators."""
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]
        result = {}

        # RSI
        result["rsi"] = self._rsi(close, 14)

        # MACD
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        result["macd"] = float(macd.iloc[-1])
        result["macd_signal"] = "bullish" if macd.iloc[-1] > signal.iloc[-1] else "bearish"
        result["macd_crossover"] = (
            macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2]
        )

        # Bollinger Bands
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = sma20 + 2 * std20
        lower = sma20 - 2 * std20
        latest = float(close.iloc[-1])
        if latest > upper.iloc[-1]:
            result["bb_position"] = "above_upper"
        elif latest < lower.iloc[-1]:
            result["bb_position"] = "below_lower"
        else:
            result["bb_position"] = "mid"
        result["bb_pct"] = float(
            (latest - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1] + 1e-9)
        )

        # ATR (Average True Range)
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        result["atr"] = float(tr.rolling(14).mean().iloc[-1])

        # Trend: 50/200 MA crossover
        ma50 = float(close.rolling(50).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else ma50
        if close.iloc[-1] > ma50 > ma200:
            result["trend"] = "uptrend"
        elif close.iloc[-1] < ma50 < ma200:
            result["trend"] = "downtrend"
        else:
            result["trend"] = "sideways"

        # Volume confirmation
        avg_vol = float(volume.rolling(20).mean().iloc[-1])
        result["volume_surge"] = float(volume.iloc[-1]) > avg_vol * 1.5

        # Momentum (Rate of Change 10)
        result["roc_10"] = float((close.iloc[-1] / close.iloc[-10] - 1) * 100)

        # Swing: distance from 52-week high/low
        period = min(252, len(close))
        result["pct_from_52w_high"] = float(
            (close.iloc[-1] / close.tail(period).max() - 1) * 100
        )
        result["pct_from_52w_low"] = float(
            (close.iloc[-1] / close.tail(period).min() - 1) * 100
        )

        return result

    def _rsi(self, close: pd.Series, period: int = 14) -> float:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / (loss + 1e-9)
        rsi = 100 - (100 / (1 + rs))
        return round(float(rsi.iloc[-1]), 2)

    def _determine_action(
        self,
        tech: dict,
        sentiment_score: float,
        quadrant_fit: str,
        asset_info: dict,
    ) -> tuple[str, float, list[str]]:
        """Score technicals + sentiment + quadrant into a final action."""
        reasons = []
        score = 0.0  # Positive = bullish, Negative = bearish

        rsi = tech.get("rsi", 50)
        macd_signal = tech.get("macd_signal", "")
        bb_pos = tech.get("bb_position", "mid")
        trend = tech.get("trend", "sideways")
        roc = tech.get("roc_10", 0)
        volume_surge = tech.get("volume_surge", False)
        macd_crossover = tech.get("macd_crossover", False)

        # RSI
        if rsi < 30:
            score += 2.0
            reasons.append(f"RSI oversold ({rsi:.1f}) — potential reversal.")
        elif rsi > 70:
            score -= 2.0
            reasons.append(f"RSI overbought ({rsi:.1f}) — potential reversal/short.")
        elif rsi < 45:
            score += 0.5
        elif rsi > 55:
            score -= 0.5

        # MACD
        if macd_signal == "bullish":
            score += 1.5
            reasons.append("MACD above signal line — bullish momentum.")
        else:
            score -= 1.5
            reasons.append("MACD below signal line — bearish momentum.")

        if macd_crossover:
            score += 1.0
            reasons.append("Fresh MACD bullish crossover.")

        # Bollinger Bands
        if bb_pos == "below_lower":
            score += 1.5
            reasons.append("Price below lower Bollinger Band — oversold condition.")
        elif bb_pos == "above_upper":
            score -= 1.5
            reasons.append("Price above upper Bollinger Band — overbought condition.")

        # Trend
        if trend == "uptrend":
            score += 2.0
            reasons.append("Price in confirmed uptrend (price > MA50 > MA200).")
        elif trend == "downtrend":
            score -= 2.0
            reasons.append("Price in confirmed downtrend (price < MA50 < MA200).")

        # Volume
        if volume_surge:
            score = score * 1.2
            reasons.append("Volume surge confirms move.")

        # Sentiment
        score += sentiment_score * 2.0
        if abs(sentiment_score) > 0.1:
            sentiment_dir = "positive" if sentiment_score > 0 else "negative"
            reasons.append(f"News sentiment is {sentiment_dir} ({sentiment_score:+.3f}).")

        # Quadrant fit
        quadrant_boost = {"strong": 1.5, "moderate": 0.5, "weak": -0.5, "avoid": -3.0}
        score += quadrant_boost.get(quadrant_fit, 0)
        if quadrant_fit in ("strong", "moderate"):
            reasons.append(f"Asset has {quadrant_fit} quadrant alignment.")

        # Momentum
        if roc > 5:
            score += 0.5
        elif roc < -5:
            score -= 0.5

        # Convert score to action
        confidence = min(abs(score) / 10.0, 1.0)  # Normalise to 0–1
        if score >= 3.0:
            action = "BUY"
        elif score <= -3.0:
            action = "SHORT"
        elif score >= 1.5:
            action = "BUY"
            confidence *= 0.8
        elif score <= -1.5:
            action = "SELL"
            confidence *= 0.8
        else:
            action = "HOLD"
            confidence = 0.0

        return action, confidence, reasons

    def _suggest_options(self, action: str, tech: dict, asset_info: dict) -> Optional[str]:
        """Suggest an options strategy appropriate to the signal and asset type."""
        asset_type = asset_info.get("type", "")
        rsi = tech.get("rsi", 50)
        trend = tech.get("trend", "sideways")

        if asset_type in ("precious_metal", "energy", "agriculture", "industrial_metal"):
            if action == "BUY":
                if trend == "uptrend":
                    return "Long Call — buy ATM call, 30–60 DTE for leveraged upside."
                else:
                    return "Bull Call Spread — buy ATM call, sell OTM call to reduce premium."
            elif action in ("SHORT", "SELL"):
                if rsi > 65:
                    return "Long Put — buy ATM put, 30–60 DTE for downside exposure."
                else:
                    return "Bear Put Spread — buy ATM put, sell OTM put."
        elif "equity" in asset_type or asset_info.get("sector"):
            if action == "BUY":
                return "Covered Call — hold shares + sell OTM call to enhance yield."
            elif action in ("SHORT", "SELL"):
                return "Protective Put — buy OTM put as hedge or directional short."

        return None

    def _kelly_position_size(self, confidence: float, rr: float) -> float:
        """
        Kelly Criterion (half-Kelly for safety):
        f* = (p * b - q) / b  where b=R/R ratio, p=win_prob, q=1-p
        Capped at 10% of portfolio.
        """
        p = confidence
        q = 1 - p
        b = max(rr, 0.1)
        kelly = (p * b - q) / b
        half_kelly = max(kelly * 0.5, 0.01)
        return min(half_kelly * 100, 10.0)   # Return as %
