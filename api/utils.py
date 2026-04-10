"""
Dalios -- Shared Utilities
Ticker normalisation, credential encryption, rate limiting, technical indicators, caching.
"""

import base64
import os
import threading
import time
import asyncio
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from loguru import logger


# ── 5-minute data cache ──────────────────────────────────
_DATA_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL = 300   # seconds
_EXECUTOR = ThreadPoolExecutor(max_workers=4)


def _cache_get(key: str, ttl: int = None):
    with _CACHE_LOCK:
        e = _DATA_CACHE.get(key)
    max_age = ttl if ttl is not None else CACHE_TTL
    return e["v"] if e and (time.time() - e["t"]) < max_age else None


def _cache_set(key: str, val):
    with _CACHE_LOCK:
        _DATA_CACHE[key] = {"v": val, "t": time.time()}


# ── Basic credential obfuscation ────────────────────────
_CRED_APP_KEY = os.environ.get("DALIO_CRED_KEY", "DaLiOs_AlLwEaThEr_2024!").encode("utf-8")


def _xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _encrypt_value(plaintext: str) -> str:
    """XOR + base64 obfuscation for stored credentials."""
    xored = _xor_bytes(plaintext.encode("utf-8"), _CRED_APP_KEY)
    return base64.b64encode(xored).decode("ascii")


def _decrypt_value(encoded: str) -> str:
    """Reverse XOR + base64 obfuscation."""
    xored = base64.b64decode(encoded.encode("ascii"))
    return _xor_bytes(xored, _CRED_APP_KEY).decode("utf-8")


def _encrypt_creds(creds: dict) -> dict:
    """Encrypt all string values in a broker credentials dict."""
    result = {}
    for broker, data in creds.items():
        if not isinstance(data, dict):
            # Preserve non-dict entries like _last_active
            result[broker] = data
            continue
        result[broker] = {}
        for k, v in data.items():
            if isinstance(v, str):
                result[broker][k] = {"_enc": _encrypt_value(v)}
            else:
                result[broker][k] = v
    return result


def _decrypt_creds(creds: dict) -> dict:
    """Decrypt all encrypted values in a broker credentials dict."""
    result = {}
    for broker, data in creds.items():
        if not isinstance(data, dict):
            # Preserve non-dict entries like _last_active
            result[broker] = data
            continue
        result[broker] = {}
        for k, v in data.items():
            if isinstance(v, dict) and "_enc" in v:
                try:
                    result[broker][k] = _decrypt_value(v["_enc"])
                except Exception:
                    result[broker][k] = v
            else:
                result[broker][k] = v
    return result


# ── In-memory rate limiter ────────────────────────────────
class RateLimiter:
    """Simple sliding-window rate limiter. Tracks request timestamps per IP."""

    # Endpoints with stricter limits (10 req/min)
    TRADING_PATHS = {"/api/paper/order", "/api/real/order", "/api/broker/connect"}
    # Exempt auto-refresh endpoints from rate limiting
    EXEMPT_PATHS = {"/api/status", "/api/portfolio/health", "/api/alerts", "/api/market/summary"}

    def __init__(self, general_limit: int = 120, trading_limit: int = 10, window: int = 60):
        self.general_limit = general_limit
        self.trading_limit = trading_limit
        self.window = window          # seconds
        self._hits: dict[str, list[float]] = {}   # key -> list of timestamps
        self._lock = threading.Lock()

    def _key(self, ip: str, path: str) -> tuple[str, int]:
        """Return (bucket_key, max_allowed) for this request."""
        if path in self.EXEMPT_PATHS:
            return f"exempt:{ip}", 999  # effectively unlimited
        for tp in self.TRADING_PATHS:
            if path == tp:
                return f"trade:{ip}", self.trading_limit
        return f"general:{ip}", self.general_limit

    def is_allowed(self, ip: str, path: str) -> bool:
        bucket, limit = self._key(ip, path)
        now = time.time()
        with self._lock:
            timestamps = self._hits.get(bucket, [])
            # Drop timestamps outside the window
            cutoff = now - self.window
            timestamps = [t for t in timestamps if t > cutoff]
            if len(timestamps) >= limit:
                self._hits[bucket] = timestamps
                return False
            timestamps.append(now)
            self._hits[bucket] = timestamps
            return True


# ── Data source rate limiter ──────────────────────────────
class SourceRateLimiter:
    """Per-data-source rate limiter with concurrency control."""

    def __init__(self):
        self._limits = {
            "yfinance":  {"max_per_min": 30, "max_concurrent": 5},
        }
        self._timestamps: dict[str, list[float]] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._lock = threading.Lock()

    def _get_semaphore(self, source: str) -> asyncio.Semaphore:
        if source not in self._semaphores:
            limit = self._limits.get(source, {}).get("max_concurrent", 5)
            self._semaphores[source] = asyncio.Semaphore(limit)
        return self._semaphores[source]

    async def acquire(self, source: str):
        sem = self._get_semaphore(source)
        await sem.acquire()
        max_rpm = self._limits.get(source, {}).get("max_per_min", 60)
        now = time.time()
        with self._lock:
            ts = self._timestamps.get(source, [])
            ts = [t for t in ts if t > now - 60]
            if len(ts) >= max_rpm:
                sem.release()
                await asyncio.sleep(2.0)
                await sem.acquire()
            now = time.time()
            ts = self._timestamps.get(source, [])
            ts = [t for t in ts if t > now - 60]
            ts.append(now)
            self._timestamps[source] = ts

    def release(self, source: str):
        sem = self._semaphores.get(source)
        if sem:
            sem.release()


SOURCE_LIMITER = SourceRateLimiter()


def _cache_get_with_age(key: str):
    """Return (value, age_seconds) or (None, 0) if not cached."""
    with _CACHE_LOCK:
        e = _DATA_CACHE.get(key)
    if e and (time.time() - e["t"]) < CACHE_TTL:
        return e["v"], int(time.time() - e["t"])
    return None, 0


# ── Technical Indicators ──────────────────────────────────

def _calc_rsi(closes: list, period: int = 14) -> float:
    """Wilder RSI from closing price list."""
    if len(closes) < period + 2:
        return 50.0
    arr = np.array(closes, dtype=float)
    delta = np.diff(arr)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_g = float(np.mean(gain[:period]))
    avg_l = float(np.mean(loss[:period]))
    for g, l in zip(gain[period:], loss[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l == 0:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + avg_g / avg_l), 1)


def _calc_trend(closes: list) -> str:
    if len(closes) < 20:
        return "sideways"
    sma20 = float(np.mean(closes[-20:]))
    last = closes[-1]
    if last > sma20 * 1.015:
        return "uptrend"
    if last < sma20 * 0.985:
        return "downtrend"
    return "sideways"


def _calc_atr(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return closes[-1] * 0.02 if closes else 1.0
    diffs = [abs(closes[i] - closes[i - 1]) for i in range(-period, 0)]
    return float(np.mean(diffs))


def _calc_ema(closes: list, span: int) -> list:
    """Compute exponential moving average from a list of closes."""
    if not closes:
        return []
    alpha = 2.0 / (span + 1)
    ema = [closes[0]]
    for c in closes[1:]:
        ema.append(alpha * c + (1 - alpha) * ema[-1])
    return ema


def _calc_macd(closes: list) -> dict:
    """Compute MACD, signal line, and crossover from a list of closes."""
    if len(closes) < 35:
        return {"macd": 0.0, "signal_line": 0.0, "macd_signal": "neutral", "macd_crossover": False}
    ema12 = _calc_ema(closes, 12)
    ema26 = _calc_ema(closes, 26)
    macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    signal_line = _calc_ema(macd_line, 9)
    macd_val = macd_line[-1]
    sig_val = signal_line[-1]
    prev_macd = macd_line[-2] if len(macd_line) >= 2 else 0
    prev_sig = signal_line[-2] if len(signal_line) >= 2 else 0
    return {
        "macd": round(macd_val, 4),
        "signal_line": round(sig_val, 4),
        "macd_signal": "bullish" if macd_val > sig_val else "bearish",
        "macd_crossover": macd_val > sig_val and prev_macd <= prev_sig,
    }


def _calc_bollinger(closes: list, period: int = 20) -> dict:
    """Compute Bollinger Bands position from a list of closes."""
    if len(closes) < period:
        return {"bb_position": "mid", "bb_pct": 0.5}
    window = closes[-period:]
    sma = float(np.mean(window))
    std = float(np.std(window))
    upper = sma + 2 * std
    lower = sma - 2 * std
    last = closes[-1]
    if last > upper:
        pos = "above_upper"
    elif last < lower:
        pos = "below_lower"
    else:
        pos = "mid"
    bb_pct = (last - lower) / (upper - lower + 1e-9)
    return {"bb_position": pos, "bb_pct": round(bb_pct, 4)}


def _calc_sma(closes: list, period: int) -> float:
    """Simple moving average of last `period` values."""
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    return float(np.mean(closes[-period:]))


# ── Ticker normalisation ──────────────────────────────────

def _normalize_ticker(ticker: str) -> str:
    """Normalise user-entered tickers to yfinance format.
    bhp -> BHP.AX (if known), etc.
    """
    # Import ticker lists here to avoid circular import at module level
    from api.scanners import ASX_TICKERS
    t = ticker.upper().strip()
    # Already correct format -- pass through
    if t.endswith(".AX") or "." in t:
        return t
    # Check if it matches an ASX ticker without suffix
    _asx_bases = {c.replace(".AX", "") for c in ASX_TICKERS}
    if t in _asx_bases:
        return f"{t}.AX"
    return t


# ── yfinance availability ──────────────────────────────────
try:
    import yfinance as yf
    import pandas as pd
    YF_AVAILABLE = True
    # Use a shared requests session to avoid repeated cookie/crumb fetches
    # that trigger Yahoo rate limits
    try:
        import requests as _req
        _yf_session = _req.Session()
        _yf_session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        yf.utils.get_json = None  # force fresh session
    except Exception:
        _yf_session = None
    logger.info("yfinance available -- real market data enabled")
except ImportError:
    YF_AVAILABLE = False
    _yf_session = None
    logger.warning("yfinance not installed -- using demo data (run: pip install yfinance pandas)")

_VALID_PERIODS = {'1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', '10y', 'ytd', 'max'}
_VALID_INTERVALS = {'1m', '2m', '5m', '15m', '30m', '60m', '90m', '1h', '1d', '5d', '1wk', '1mo', '3mo'}


def _yf_fetch_sync(tickers: list, period: str = "3mo") -> Optional[dict]:
    """Blocking yfinance download -> dict[ticker -> list[float]] of closing prices."""
    if not YF_AVAILABLE or not tickers:
        return None
    try:
        raw = yf.download(
            tickers if len(tickers) > 1 else tickers[0],
            period=period,
            auto_adjust=True,
            progress=False,
            threads=True,
            timeout=10,
        )
        if raw is None or raw.empty:
            return None
        # Normalise MultiIndex vs flat columns
        if isinstance(raw.columns, pd.MultiIndex):
            close = raw["Close"] if "Close" in raw.columns.get_level_values(0) else None
        elif "Close" in raw.columns:
            t = tickers[0]
            close = pd.DataFrame({t: raw["Close"]})
        else:
            return None
        if close is None or close.empty:
            return None
        result = {}
        for t in tickers:
            col = close[t] if t in close.columns else None
            if col is not None:
                vals = [float(v) for v in col.dropna().tolist()[-90:]]
                if vals:
                    result[t] = vals
        return result or None
    except Exception as exc:
        logger.warning(f"yfinance error: {exc}")
        return None


async def _get_prices(tickers: list, period: str = "3mo") -> Optional[dict]:
    """Async wrapper around yfinance; caches 5 min. Times out in 12s."""
    if period not in _VALID_PERIODS:
        logger.warning(f"Invalid period '{period}', returning empty")
        return None
    key = f"px_{hash(tuple(sorted(tickers)))}_{period}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_EXECUTOR, _yf_fetch_sync, tickers, period),
            timeout=12.0,
        )
    except asyncio.TimeoutError:
        logger.warning("yfinance timed out -- using demo data")
        result = None
    if result:
        _cache_set(key, result)
    return result


# ── Format helpers ──────────────────────────────────────

def _fmt_vol(v) -> str:
    if not v: return "--"
    v = float(v)
    if v >= 1e9: return f"{v/1e9:.2f}B"
    if v >= 1e6: return f"{v/1e6:.1f}M"
    if v >= 1e3: return f"{v/1e3:.0f}K"
    return str(int(v))
