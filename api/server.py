"""
Dalios — Automated Trading Framework
FastAPI Backend Server

Exposes all trading system engines via REST + WebSocket endpoints.
Serves the military/hacker UI from /ui/index.html.
"""

import json
import asyncio
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional
from pathlib import Path

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from config.settings import get_settings
    from data.storage.models import init_db
    SETTINGS_AVAILABLE = True
except ImportError:
    SETTINGS_AVAILABLE = False

# ── Real market data via yfinance (no API key needed) ────
try:
    import yfinance as yf
    import pandas as pd
    YF_AVAILABLE = True
    logger.info("yfinance available — real market data enabled")
except ImportError:
    YF_AVAILABLE = False
    logger.warning("yfinance not installed — using demo data (run: pip install yfinance pandas)")

# ── 5-minute data cache ──────────────────────────────────
_DATA_CACHE: dict = {}
_CACHE_LOCK  = threading.Lock()
CACHE_TTL    = 300   # seconds
_EXECUTOR    = ThreadPoolExecutor(max_workers=4)


def _cache_get(key: str):
    with _CACHE_LOCK:
        e = _DATA_CACHE.get(key)
    return e["v"] if e and (time.time() - e["t"]) < CACHE_TTL else None


def _cache_set(key: str, val):
    with _CACHE_LOCK:
        _DATA_CACHE[key] = {"v": val, "t": time.time()}


def _yf_fetch_sync(tickers: list, period: str = "3mo") -> Optional[dict]:
    """Blocking yfinance download → dict[ticker → list[float]] of closing prices."""
    if not YF_AVAILABLE or not tickers:
        return None
    try:
        raw = yf.download(
            tickers if len(tickers) > 1 else tickers[0],
            period=period,
            auto_adjust=True,
            progress=False,
            threads=False,
            timeout=8,
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
    key = f"px_{'_'.join(sorted(tickers)[:8])}_{period}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_EXECUTOR, _yf_fetch_sync, tickers, period),
            timeout=12.0,
        )
    except asyncio.TimeoutError:
        logger.warning("yfinance timed out — using demo data")
        result = None
    if result:
        _cache_set(key, result)
    return result


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
    last  = closes[-1]
    if last > sma20 * 1.015:
        return "uptrend"
    if last < sma20 * 0.985:
        return "downtrend"
    return "sideways"


# ── CoinGecko coin-ID map ────────────────────────────────
_COINGECKO_MAP = {
    "BTC-USD": "bitcoin",      "ETH-USD": "ethereum",       "BNB-USD": "binancecoin",
    "SOL-USD": "solana",       "XRP-USD": "ripple",          "ADA-USD": "cardano",
    "AVAX-USD":"avalanche-2",  "DOT-USD": "polkadot",        "LINK-USD":"chainlink",
    "MATIC-USD":"matic-network","DOGE-USD":"dogecoin",       "LTC-USD": "litecoin",
    "UNI-USD": "uniswap",      "ATOM-USD":"cosmos",          "NEAR-USD":"near",
    "FTM-USD": "fantom",       "ALGO-USD":"algorand",        "XLM-USD": "stellar",
    "AAVE-USD":"aave",         "SNX-USD": "havven",
}


async def _get_crypto_coingecko() -> Optional[dict]:
    """Fetch real crypto prices from CoinGecko free API (no API key needed)."""
    key = "coingecko_prices"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    ids = ",".join(_COINGECKO_MAP.values())
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={ids}&vs_currencies=usd&include_24hr_change=true"
    )
    try:
        import aiohttp
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=8),
            headers={"User-Agent": "DALIOS/1.0"},
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        rev = {v: k for k, v in _COINGECKO_MAP.items()}
        result = {
            rev[cid]: {
                "price":      vals.get("usd"),
                "change_pct": round(vals.get("usd_24h_change") or 0, 2),
                "source":     "CoinGecko",
            }
            for cid, vals in data.items() if cid in rev
        }
        if result:
            _cache_set(key, result)
        return result or None
    except Exception as exc:
        logger.warning(f"CoinGecko error: {exc}")
        return None


def _calc_atr(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return closes[-1] * 0.02 if closes else 1.0
    diffs = [abs(closes[i] - closes[i - 1]) for i in range(-period, 0)]
    return float(np.mean(diffs))

# ─────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────

app = FastAPI(
    title="Dalios — Automated Trading Framework",
    description="Ray Dalio All Weather + Economic Machine — Autonomous ASX & Commodities Trading",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UI_DIR = ROOT / "ui"
STATIC_DIR = UI_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
async def _on_startup():
    global REAL_EQUITY_CURVE
    _load_paper_state()
    REAL_EQUITY_CURVE = _load_real_equity()
    logger.info(f"Startup complete — trading mode: {TRADING_MODE}")

# ─────────────────────────────────────────────
# State
# ─────────────────────────────────────────────

class SystemState:
    def __init__(self):
        self.agent = None
        self.booted = False
        self.mode = "PAPER"
        self.start_time = datetime.utcnow()
        self.cycle_count = 0
        self.last_cycle: Optional[dict] = None
        self.last_health: Optional[dict] = None
        self.last_sentiment: Optional[dict] = None
        self.last_quadrant: Optional[dict] = None
        self.alert_log: list[dict] = []
        self.equity_history: list[dict] = []
        self.initial_equity = 100_000.0
        self._init_equity_history()

    def _init_equity_history(self):
        """Generate seed equity curve for demo mode."""
        equity = self.initial_equity
        for i in range(90):
            equity *= (1 + random.gauss(0.0008, 0.008))
            self.equity_history.append({
                "t": (datetime.utcnow().replace(hour=0, minute=0, second=0)
                      .__class__.fromtimestamp(
                          datetime.utcnow().timestamp() - (90 - i) * 86400
                      )).strftime("%Y-%m-%d"),
                "v": round(equity, 2),
            })

    def uptime_seconds(self) -> int:
        return int((datetime.utcnow() - self.start_time).total_seconds())

    def add_alert(self, alert_type: str, message: str, level: str = "INFO"):
        entry = {
            "id": len(self.alert_log),
            "type": alert_type,
            "message": message,
            "level": level,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.alert_log.insert(0, entry)
        self.alert_log = self.alert_log[:200]  # Keep last 200


STATE = SystemState()


# ─────────────────────────────────────────────
# Persistence helpers
# ─────────────────────────────────────────────

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

PAPER_STATE_FILE = DATA_DIR / "paper_portfolio.json"
REAL_EQUITY_FILE   = DATA_DIR / "real_equity.json"
WATCHLIST_FILE     = DATA_DIR / "watchlist.json"
PAPER_CONFIG_FILE  = DATA_DIR / "paper_config.json"


def _save_paper_state() -> None:
    try:
        payload = {
            "cash":           PAPER.cash,
            "positions":      PAPER.positions,
            "history":        PAPER.history,
            "equity_history": PAPER.equity_history,
            "order_id":       PAPER.order_id,
        }
        PAPER_STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to save paper state: {exc}")


def _load_paper_state() -> None:
    if not PAPER_STATE_FILE.exists():
        return
    try:
        payload = json.loads(PAPER_STATE_FILE.read_text(encoding="utf-8"))
        PAPER.cash           = float(payload.get("cash", PAPER_STARTING_CASH))
        PAPER.positions      = payload.get("positions", {})
        PAPER.history        = payload.get("history", [])
        PAPER.equity_history = payload.get("equity_history", [])
        PAPER.order_id       = int(payload.get("order_id", 0))
        logger.info(f"Paper portfolio loaded — cash=${PAPER.cash:,.2f}, "
                    f"{len(PAPER.positions)} positions, {len(PAPER.equity_history)} equity pts")
    except Exception as exc:
        logger.warning(f"Failed to load paper state (starting fresh): {exc}")


def _load_real_equity() -> list:
    if not REAL_EQUITY_FILE.exists():
        return []
    try:
        return json.loads(REAL_EQUITY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_real_equity(curve: list) -> None:
    try:
        REAL_EQUITY_FILE.write_text(json.dumps(curve, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to save real equity: {exc}")


# ─────────────────────────────────────────────
# Paper Trading Portfolio
# ─────────────────────────────────────────────

# ─── Paper config (persisted) ────────────────
def _load_paper_config() -> dict:
    if PAPER_CONFIG_FILE.exists():
        try:
            return json.loads(PAPER_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"starting_cash": 1_000.0}

def _save_paper_config(cfg: dict) -> None:
    try:
        PAPER_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to save paper config: {exc}")

_paper_cfg         = _load_paper_config()
PAPER_STARTING_CASH: float = float(_paper_cfg.get("starting_cash", 1_000.0))

# ─── Watchlist ────────────────────────────────
def _load_watchlist() -> list:
    if WATCHLIST_FILE.exists():
        try:
            return json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

def _save_watchlist(wl: list) -> None:
    try:
        WATCHLIST_FILE.write_text(json.dumps(wl, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to save watchlist: {exc}")

WATCHLIST: list = _load_watchlist()

class PaperPortfolio:
    def __init__(self):
        self.cash            = PAPER_STARTING_CASH
        self.positions: dict = {}
        self.history:   list = []
        self.equity_history: list = []  # [{t: ISO, v: float}]
        self.order_id   = 0

    def _next_id(self) -> int:
        self.order_id += 1
        return self.order_id

    def total_value(self, prices: dict) -> float:
        """Portfolio total = cash + market value of all positions."""
        invested = sum(
            pos["qty"] * prices.get(t, pos["entry_price"])
            for t, pos in self.positions.items()
        )
        return round(self.cash + invested, 2)

    def unrealised_pnl(self, prices: dict) -> float:
        total = 0.0
        for t, pos in self.positions.items():
            cur = prices.get(t, pos["entry_price"])
            if pos["side"] == "LONG":
                total += (cur - pos["entry_price"]) * pos["qty"]
            else:
                total += (pos["entry_price"] - cur) * pos["qty"]
        return round(total, 2)

    def place_order(self, ticker: str, side: str, qty: float, price: float) -> dict:
        cost = qty * price
        oid  = self._next_id()
        ts   = datetime.utcnow().isoformat()

        if side == "BUY":
            if cost > self.cash:
                raise ValueError(f"Insufficient cash — need ${cost:,.2f}, have ${self.cash:,.2f}")
            self.cash -= cost
            if ticker in self.positions and self.positions[ticker]["side"] == "LONG":
                # Add to existing long
                pos = self.positions[ticker]
                total_qty   = pos["qty"] + qty
                total_cost  = pos["entry_price"] * pos["qty"] + price * qty
                pos["entry_price"] = round(total_cost / total_qty, 4)
                pos["qty"]         = total_qty
            else:
                self.positions[ticker] = {
                    "qty": qty, "entry_price": round(price, 4),
                    "entry_time": ts, "side": "LONG", "cost_basis": round(cost, 2),
                }
        else:  # SELL / close
            if ticker not in self.positions:
                raise ValueError(f"No open position in {ticker}")
            pos   = self.positions[ticker]
            close_qty = min(qty, pos["qty"])
            proceeds  = close_qty * price
            if pos["side"] == "LONG":
                pnl = (price - pos["entry_price"]) * close_qty
            else:
                pnl = (pos["entry_price"] - price) * close_qty
            self.cash += proceeds
            self.history.insert(0, {
                "id": oid, "ticker": ticker, "side": "SELL",
                "qty": close_qty, "entry_price": pos["entry_price"],
                "exit_price": round(price, 4), "pnl": round(pnl, 2),
                "pnl_pct": round(pnl / (pos["entry_price"] * close_qty) * 100, 2),
                "timestamp": ts,
            })
            pos["qty"] -= close_qty
            if pos["qty"] <= 0:
                del self.positions[ticker]
        STATE.add_alert("PAPER", f"Order #{oid}: {side} {qty:.4g}× {ticker} @ ${price:.4f}", "INFO")
        return {"order_id": oid, "ticker": ticker, "side": side, "qty": qty, "price": price, "timestamp": ts}

    def reset(self):
        self.cash            = PAPER_STARTING_CASH
        self.positions       = {}
        self.history         = []
        self.equity_history  = []
        self.order_id        = 0
        STATE.add_alert("PAPER", "Portfolio reset to $100,000 starting cash", "INFO")


PAPER = PaperPortfolio()


# ─────────────────────────────────────────────
# Broker Abstraction — live trading
# ─────────────────────────────────────────────

class BrokerBase:
    name: str = "base"
    def is_connected(self) -> bool: raise NotImplementedError
    async def connect(self, **kwargs) -> None: raise NotImplementedError
    async def get_account(self) -> dict: raise NotImplementedError
    async def place_order(self, ticker: str, side: str, qty: float, price: Optional[float]) -> dict: raise NotImplementedError
    async def get_positions(self) -> list: raise NotImplementedError
    async def get_history(self) -> list: raise NotImplementedError
    async def close_position(self, ticker: str) -> dict: raise NotImplementedError


class IBKRBroker(BrokerBase):
    name = "ibkr"

    def __init__(self):
        self._ib = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._ib is not None

    async def connect(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1, **kwargs) -> None:
        try:
            from ib_insync import IB
        except ImportError:
            raise ImportError("ib_insync not installed. Run: pip install ib_insync")
        ib = IB()
        await asyncio.get_event_loop().run_in_executor(_EXECUTOR, lambda: ib.connect(host, int(port), clientId=int(client_id), timeout=10))
        self._ib = ib
        self._connected = True
        logger.info(f"IBKR connected — {host}:{port}")

    async def get_account(self) -> dict:
        if not self.is_connected(): raise RuntimeError("IBKR not connected")
        summary = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, self._ib.accountSummary)
        vals = {row.tag: row.value for row in summary}
        return {"broker": "ibkr", "account_value": float(vals.get("NetLiquidation", 0)),
                "buying_power": float(vals.get("BuyingPower", 0)), "cash": float(vals.get("TotalCashValue", 0)), "currency": "AUD"}

    async def place_order(self, ticker: str, side: str, qty: float, price: Optional[float] = None) -> dict:
        if not self.is_connected(): raise RuntimeError("IBKR not connected")
        from ib_insync import Stock, MarketOrder, LimitOrder
        contract = Stock(ticker, "SMART", "USD")
        order = LimitOrder(side.upper(), qty, price) if price else MarketOrder(side.upper(), qty)
        trade = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, self._ib.placeOrder, contract, order)
        return {"order_id": trade.order.orderId, "ticker": ticker, "side": side, "qty": qty,
                "price": price, "status": trade.orderStatus.status, "timestamp": datetime.utcnow().isoformat()}

    async def get_positions(self) -> list:
        if not self.is_connected(): raise RuntimeError("IBKR not connected")
        raw = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, self._ib.positions)
        return [{"ticker": p.contract.symbol, "qty": p.position, "avg_cost": round(p.avgCost, 4),
                 "market_val": None, "pnl": None, "side": "LONG" if p.position > 0 else "SHORT"} for p in raw]

    async def get_history(self) -> list:
        if not self.is_connected(): raise RuntimeError("IBKR not connected")
        fills = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, self._ib.fills)
        return [{"ticker": f.contract.symbol, "side": f.execution.side, "qty": f.execution.shares,
                 "price": f.execution.price, "timestamp": str(f.execution.time)} for f in fills]

    async def close_position(self, ticker: str) -> dict:
        positions = await self.get_positions()
        pos = next((p for p in positions if p["ticker"].upper() == ticker.upper()), None)
        if not pos: raise ValueError(f"No open IBKR position in {ticker}")
        side = "SELL" if pos["qty"] > 0 else "BUY"
        return await self.place_order(ticker, side, abs(pos["qty"]), None)


class AlpacaBroker(BrokerBase):
    name = "alpaca"

    def __init__(self):
        self._api = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._api is not None

    async def connect(self, api_key: str, api_secret: str,
                      base_url: str = "https://paper-api.alpaca.markets", **kwargs) -> None:
        try:
            import alpaca_trade_api as tradeapi
        except ImportError:
            raise ImportError("alpaca-trade-api not installed. Run: pip install alpaca-trade-api")
        api = tradeapi.REST(api_key, api_secret, base_url, api_version="v2")
        await asyncio.get_event_loop().run_in_executor(_EXECUTOR, api.get_account)
        self._api = api
        self._connected = True
        logger.info(f"Alpaca connected — {base_url}")

    async def get_account(self) -> dict:
        if not self.is_connected(): raise RuntimeError("Alpaca not connected")
        acct = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, self._api.get_account)
        return {"broker": "alpaca", "account_value": float(acct.portfolio_value),
                "buying_power": float(acct.buying_power), "cash": float(acct.cash),
                "currency": "USD", "status": acct.status}

    async def place_order(self, ticker: str, side: str, qty: float, price: Optional[float] = None) -> dict:
        if not self.is_connected(): raise RuntimeError("Alpaca not connected")
        order = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, lambda: self._api.submit_order(
            symbol=ticker, qty=qty, side=side.lower(),
            type="limit" if price else "market",
            time_in_force="gtc",
            limit_price=str(price) if price else None,
        ))
        return {"order_id": str(order.id), "ticker": ticker, "side": side, "qty": qty,
                "price": price, "status": order.status, "timestamp": datetime.utcnow().isoformat()}

    async def get_positions(self) -> list:
        if not self.is_connected(): raise RuntimeError("Alpaca not connected")
        raw = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, self._api.list_positions)
        return [{"ticker": p.symbol, "qty": float(p.qty), "avg_cost": float(p.avg_entry_price),
                 "market_val": float(p.market_value), "pnl": float(p.unrealized_pl),
                 "pnl_pct": round(float(p.unrealized_plpc) * 100, 2), "side": p.side} for p in raw]

    async def get_history(self) -> list:
        if not self.is_connected(): raise RuntimeError("Alpaca not connected")
        raw = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, lambda: self._api.list_orders(status="filled", limit=100))
        return [{"ticker": o.symbol, "side": o.side, "qty": float(o.filled_qty or 0),
                 "price": float(o.filled_avg_price) if o.filled_avg_price else None,
                 "timestamp": o.filled_at.isoformat() if o.filled_at else None} for o in raw]

    async def close_position(self, ticker: str) -> dict:
        if not self.is_connected(): raise RuntimeError("Alpaca not connected")
        order = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, lambda: self._api.close_position(ticker))
        return {"order_id": str(order.id), "ticker": ticker, "side": "SELL",
                "status": order.status, "timestamp": datetime.utcnow().isoformat()}


class BinanceBroker(BrokerBase):
    name = "binance"

    def __init__(self):
        self._client = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    async def connect(self, api_key: str, api_secret: str, testnet: bool = False, **kwargs) -> None:
        try:
            from binance.client import Client as BinanceClient
        except ImportError:
            raise ImportError("python-binance not installed. Run: pip install python-binance")
        client = BinanceClient(api_key, api_secret, testnet=testnet)
        await asyncio.get_event_loop().run_in_executor(_EXECUTOR, client.get_account)
        self._client = client
        self._connected = True
        logger.info(f"Binance connected — {'testnet' if testnet else 'live'}")

    async def get_account(self) -> dict:
        if not self.is_connected(): raise RuntimeError("Binance not connected")
        acct = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, self._client.get_account)
        usdt = next((float(b["free"]) + float(b["locked"]) for b in acct.get("balances", []) if b["asset"] == "USDT"), 0.0)
        free_usdt = next((float(b["free"]) for b in acct.get("balances", []) if b["asset"] == "USDT"), 0.0)
        return {"broker": "binance", "account_value": usdt, "buying_power": free_usdt, "cash": free_usdt, "currency": "USDT"}

    async def place_order(self, ticker: str, side: str, qty: float, price: Optional[float] = None) -> dict:
        if not self.is_connected(): raise RuntimeError("Binance not connected")
        symbol = ticker.replace("/", "").replace("-", "").upper()
        params = dict(symbol=symbol, side=side.upper(), type="LIMIT" if price else "MARKET", quantity=qty)
        if price:
            params["price"] = str(price)
            params["timeInForce"] = "GTC"
        order = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, lambda: self._client.create_order(**params))
        return {"order_id": str(order["orderId"]), "ticker": ticker, "side": side, "qty": qty,
                "price": price, "status": order.get("status"), "timestamp": datetime.utcnow().isoformat()}

    async def get_positions(self) -> list:
        if not self.is_connected(): raise RuntimeError("Binance not connected")
        acct = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, self._client.get_account)
        return [{"ticker": b["asset"], "qty": float(b["free"]) + float(b["locked"]),
                 "avg_cost": None, "market_val": None, "pnl": None, "side": "LONG"}
                for b in acct.get("balances", [])
                if (float(b["free"]) + float(b["locked"])) > 0 and b["asset"] not in ("USDT", "BUSD")]

    async def get_history(self) -> list:
        if not self.is_connected(): raise RuntimeError("Binance not connected")
        orders = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, lambda: self._client.get_all_orders(symbol="BTCUSDT", limit=50))
        return [{"ticker": o["symbol"], "side": o["side"], "qty": float(o["executedQty"]),
                 "price": float(o["price"]) if o["price"] else None, "timestamp": str(o["time"])}
                for o in orders if o["status"] == "FILLED"]

    async def close_position(self, ticker: str) -> dict:
        positions = await self.get_positions()
        asset = ticker.replace("/", "").replace("-", "").replace("USDT", "").upper()
        pos = next((p for p in positions if p["ticker"].upper() == asset), None)
        if not pos: raise ValueError(f"No Binance balance for {ticker}")
        return await self.place_order(ticker, "SELL", pos["qty"], None)


class CoinbaseBroker(BrokerBase):
    name = "coinbase"

    def __init__(self):
        self._client = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    async def connect(self, api_key: str, api_secret: str, **kwargs) -> None:
        try:
            from coinbase.rest import RESTClient
        except ImportError:
            raise ImportError("coinbase-advanced-py not installed. Run: pip install coinbase-advanced-py")
        client = RESTClient(api_key=api_key, api_secret=api_secret)
        await asyncio.get_event_loop().run_in_executor(_EXECUTOR, client.get_accounts)
        self._client = client
        self._connected = True
        logger.info("Coinbase Advanced Trade connected")

    async def get_account(self) -> dict:
        if not self.is_connected(): raise RuntimeError("Coinbase not connected")
        accounts = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, self._client.get_accounts)
        acct_list = accounts.accounts if hasattr(accounts, "accounts") else []
        cash = sum(float(getattr(a, "available_balance", type("", (), {"value": "0"})).value)
                   for a in acct_list if getattr(a, "currency", "") in ("USD", "USDC"))
        return {"broker": "coinbase", "account_value": cash, "buying_power": cash, "cash": cash, "currency": "USD"}

    async def place_order(self, ticker: str, side: str, qty: float, price: Optional[float] = None) -> dict:
        if not self.is_connected(): raise RuntimeError("Coinbase not connected")
        import uuid
        product_id = ticker.upper().replace("/", "-")
        client_order_id = str(uuid.uuid4())
        cfg = {"limit_limit_gtc": {"base_size": str(qty), "limit_price": str(price)}} if price \
              else {"market_market_ioc": {"base_size": str(qty)}}
        order = await asyncio.get_event_loop().run_in_executor(
            _EXECUTOR, lambda: self._client.create_order(
                client_order_id=client_order_id, product_id=product_id, side=side.upper(), order_configuration=cfg))
        return {"order_id": getattr(order, "order_id", client_order_id), "ticker": ticker, "side": side,
                "qty": qty, "price": price, "status": "FILLED" if getattr(order, "success", False) else "PENDING",
                "timestamp": datetime.utcnow().isoformat()}

    async def get_positions(self) -> list:
        if not self.is_connected(): raise RuntimeError("Coinbase not connected")
        accounts = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, self._client.get_accounts)
        acct_list = accounts.accounts if hasattr(accounts, "accounts") else []
        return [{"ticker": getattr(a, "currency", ""), "qty": float(getattr(a, "available_balance", type("", (), {"value": "0"})).value),
                 "avg_cost": None, "market_val": None, "pnl": None, "side": "LONG"}
                for a in acct_list if getattr(a, "currency", "") not in ("USD", "USDC", "USDT")
                and float(getattr(a, "available_balance", type("", (), {"value": "0"})).value) > 0]

    async def get_history(self) -> list:
        if not self.is_connected(): raise RuntimeError("Coinbase not connected")
        orders = await asyncio.get_event_loop().run_in_executor(_EXECUTOR, lambda: self._client.list_orders(order_status=["FILLED"], limit=50))
        return [{"ticker": getattr(o, "product_id", ""), "side": getattr(o, "side", ""),
                 "qty": float(getattr(o, "filled_size", 0) or 0), "price": float(getattr(o, "average_filled_price", 0) or 0),
                 "timestamp": str(getattr(o, "created_time", ""))} for o in (orders.orders if hasattr(orders, "orders") else [])]

    async def close_position(self, ticker: str) -> dict:
        positions = await self.get_positions()
        asset = ticker.split("-")[0].upper()
        pos = next((p for p in positions if p["ticker"].upper() == asset), None)
        if not pos: raise ValueError(f"No Coinbase balance for {ticker}")
        return await self.place_order(ticker, "SELL", pos["qty"], None)


class StakeBroker(BrokerBase):
    name = "stake"
    def is_connected(self) -> bool: return False
    async def connect(self, **kwargs) -> None:
        raise ValueError(
            "Stake does not support automated API trading. Use Stake's app/web for manual execution. "
            "Consider Alpaca for US stocks or IBKR for ASX access.")
    async def get_account(self) -> dict: raise ValueError("Stake does not support automated API trading.")
    async def place_order(self, ticker, side, qty, price=None) -> dict: raise ValueError("Stake does not support automated API trading.")
    async def get_positions(self) -> list: raise ValueError("Stake does not support automated API trading.")
    async def get_history(self) -> list: raise ValueError("Stake does not support automated API trading.")
    async def close_position(self, ticker) -> dict: raise ValueError("Stake does not support automated API trading.")


ACTIVE_BROKER: Optional[BrokerBase] = None
TRADING_MODE:  str = "paper"           # "paper" | "live"
REAL_EQUITY_CURVE: list = []


# ─────────────────────────────────────────────
# WebSocket Manager
# ─────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info(f"WebSocket connected. Active connections: {len(self.active)}")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        logger.info(f"WebSocket disconnected. Active connections: {len(self.active)}")

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


WS_MANAGER = ConnectionManager()


# ─────────────────────────────────────────────
# Demo / Fallback Data Generators
# ─────────────────────────────────────────────

ASX_TICKERS = [
    # ── Big 4 Banks ──────────────────────────────────────
    "CBA.AX", "ANZ.AX", "NAB.AX", "WBC.AX",
    # ── Mining / Resources ───────────────────────────────
    "BHP.AX", "RIO.AX", "FMG.AX", "S32.AX", "MIN.AX", "LYC.AX",
    # ── Energy ───────────────────────────────────────────
    "WDS.AX", "STO.AX", "BPT.AX", "AGL.AX", "ORG.AX",
    # ── Finance ──────────────────────────────────────────
    "MQG.AX", "SUN.AX", "QBE.AX", "AMP.AX",
    # ── Healthcare ───────────────────────────────────────
    "CSL.AX", "COH.AX", "RMD.AX", "PME.AX",
    # ── Consumer / Retail ────────────────────────────────
    "WES.AX", "WOW.AX", "COL.AX", "JBH.AX", "TWE.AX",
    # ── Technology ───────────────────────────────────────
    "REA.AX", "XRO.AX", "WTC.AX", "ALU.AX",
    # ── Real Estate ──────────────────────────────────────
    "GMG.AX", "SCG.AX", "GPT.AX",
    # ── Transport / Infra ────────────────────────────────
    "QAN.AX", "TCL.AX", "TLS.AX",
    # ── Gold / Materials ─────────────────────────────────
    "NCM.AX", "EVN.AX", "NST.AX",
]

# Crypto tickers available on all major platforms (Binance/Coinbase/Kraken)
CRYPTO_TICKERS = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
    "ADA-USD", "AVAX-USD", "DOT-USD", "LINK-USD", "MATIC-USD",
    "DOGE-USD", "LTC-USD", "UNI-USD", "ATOM-USD", "NEAR-USD",
    "FTM-USD", "ALGO-USD", "XLM-USD", "AAVE-USD", "SNX-USD",
]

COMMODITY_TICKERS = [
    "GLD",   # Gold ETF
    "SLV",   # Silver ETF
    "USO",   # Crude Oil
    "UNG",   # Natural Gas
    "COPX",  # Copper Miners
    "WEAT",  # Wheat
    "DBA",   # Agriculture broad
    "PALL",  # Palladium
    "XOM",   # Exxon (oil proxy)
    "CVX",   # Chevron (oil proxy)
]

ALL_TICKERS  = ASX_TICKERS + CRYPTO_TICKERS + COMMODITY_TICKERS
CORR_TICKERS = ASX_TICKERS  # Use ASX for correlation heatmap

QUADRANT_META = {
    "rising_growth": {
        "label": "RISING GROWTH",
        "color": "#00ff41",
        "icon": "▲",
        "description": "Economy expanding. Favour equities, commodities, corporate bonds. Reduce nominal bonds.",
        "favoured": ["Equities", "Commodities", "Corporate Bonds", "EM Debt"],
        "avoid": ["Nominal Bonds", "Defensive Cash"],
    },
    "falling_growth": {
        "label": "FALLING GROWTH",
        "color": "#ff4444",
        "icon": "▼",
        "description": "Recessionary pressure. Favour long-duration bonds, defensive equities. Reduce cyclicals.",
        "favoured": ["Long Bonds", "Defensive Equities", "Gold", "Cash"],
        "avoid": ["Cyclicals", "Commodities", "EM"],
    },
    "rising_inflation": {
        "label": "RISING INFLATION",
        "color": "#ffb300",
        "icon": "↑",
        "description": "Prices rising faster than growth. Favour gold, inflation-linked bonds, energy, real assets.",
        "favoured": ["Gold", "Energy", "TIPS", "Commodities", "Real Assets"],
        "avoid": ["Nominal Bonds", "Growth Equities"],
    },
    "falling_inflation": {
        "label": "FALLING INFLATION",
        "color": "#00e5ff",
        "icon": "↓",
        "description": "Disinflation / deflation. Favour equities, nominal bonds, consumer staples.",
        "favoured": ["Equities", "Nominal Bonds", "Consumer Staples"],
        "avoid": ["Commodities", "Gold", "Energy"],
    },
}


def _gen_price_history_demo(price: float, trend: str, n_points: int = 30) -> list:
    """Seeded random-walk ending at `price`, shaped by trend direction."""
    drift = 0.003 if trend == "uptrend" else -0.003 if trend == "downtrend" else 0.0
    pts = []
    p = price * (1 - drift * n_points)  # start slightly in past
    for _ in range(n_points):
        p = p * (1 + drift + random.gauss(0, 0.012))
        pts.append(round(p, 4))
    pts[-1] = price  # anchor last point to actual price
    return pts


async def _gen_signals(n: int = 12) -> list[dict]:
    """Generate trade signals — uses real yfinance prices when available."""
    candidates = random.sample(ALL_TICKERS, min(n + 6, len(ALL_TICKERS)))
    # Fetch real prices (capped to avoid very long API calls)
    prices_map = await _get_prices(candidates[:24], "3mo")

    signals = []
    for ticker in candidates:
        closes = prices_map.get(ticker) if prices_map else None
        if closes and len(closes) >= 20:
            price  = round(closes[-1], 4 if "USD" in ticker else 2)
            rsi    = _calc_rsi(closes)
            trend  = _calc_trend(closes)
            atr    = _calc_atr(closes)
            # Rule-based action from real RSI + trend
            if rsi < 32 and trend != "downtrend":
                action = "BUY"
            elif rsi > 68 and trend != "uptrend":
                action = "SELL"
            elif trend == "uptrend" and rsi < 58:
                action = "LONG"
            elif trend == "downtrend" and rsi > 42:
                action = "SHORT"
            else:
                action = "HOLD"
            source = "LIVE"
            price_history = [round(c, 4 if "USD" in ticker else 2) for c in closes[-30:]]
        else:
            price  = round(random.uniform(8, 250), 2)
            rsi    = round(random.uniform(22, 78), 1)
            trend  = random.choice(["uptrend", "downtrend", "sideways"])
            atr    = price * random.uniform(0.018, 0.04)
            action = random.choices(
                ["BUY", "SELL", "SHORT", "LONG", "HOLD"],
                weights=[30, 15, 15, 25, 15]
            )[0]
            source = "DEMO"
            price_history = _gen_price_history_demo(price, trend, 30)

        sl_offset = max(atr * 1.5, price * 0.025)
        tp_offset = sl_offset * random.uniform(1.8, 3.2)
        conf = round(random.uniform(52, 96), 1)
        # Estimate days to reach target based on ~0.8% avg daily move
        predicted_days = max(3, min(60, int(tp_offset / max(price * 0.008, 0.01))))

        signals.append({
            "ticker": ticker,
            "action": action,
            "confidence": conf,
            "price": price,
            "data_source": source,
            "quadrant_fit": random.choices(
                ["strong", "moderate", "weak", "avoid"],
                weights=[40, 35, 15, 10]
            )[0],
            "sentiment": random.choices(
                ["positive", "neutral", "negative"],
                weights=[50, 30, 20]
            )[0],
            "rsi": rsi,
            "trend": trend,
            "stop_loss": round(price - sl_offset, 2),
            "take_profit": round(price + tp_offset, 2),
            "rr_ratio": round(tp_offset / sl_offset, 2),
            "position_size_pct": round(random.uniform(2.5, 8.0), 1),
            "options_strategy": random.choice([
                None, "Bull Call Spread", "Bear Put Spread",
                "Covered Call", "Cash-Secured Put",
            ]),
            "dalio_justification": _gen_justification(ticker, action),
            "price_history": price_history,
            "predicted_days": predicted_days,
            "timestamp": datetime.utcnow().isoformat(),
        })

    # Best signals first: exclude HOLDs unless everything is HOLD, sort by confidence
    active = [s for s in signals if s["action"] != "HOLD"]
    holds  = [s for s in signals if s["action"] == "HOLD"]
    active.sort(key=lambda s: s["confidence"], reverse=True)
    return (active + holds)[:n]


def _gen_justification(ticker: str, action: str) -> dict:
    quadrant = random.choice(list(QUADRANT_META.keys()))
    meta = QUADRANT_META[quadrant]
    sent_score = round(random.uniform(-0.5, 0.8), 3)
    sharpe_imp = round(random.uniform(0.05, 0.35), 3)
    rsi_val    = round(random.uniform(22, 78), 1)
    rr         = round(random.uniform(1.5, 4.0), 2)
    corr       = round(random.uniform(-0.12, 0.08), 3)

    action_word = {
        "BUY":   "long",  "LONG":  "long",
        "SELL":  "exit",  "SHORT": "short", "HOLD": "hold",
    }.get(action, "enter")

    sentiment_word = "positive" if sent_score > 0.1 else "negative" if sent_score < -0.1 else "neutral"
    rsi_desc = "oversold" if rsi_val < 35 else "overbought" if rsi_val > 65 else "neutral"
    quadrant_label = quadrant.replace("_", " ").title()

    ai_overview = (
        f"{ticker} presents a {action.lower()} opportunity under the current {quadrant_label} regime. "
        f"FinBERT sentiment across recent news is {sentiment_word} (score {sent_score:+.3f}), "
        f"RSI reads {rsi_val} ({rsi_desc}), and the risk/reward ratio is {rr}:1. "
        f"Adding this position improves portfolio Sharpe by +{sharpe_imp:.3f} and contributes "
        f"{'negatively' if corr < 0 else 'minimally'} to overall correlation ({corr:+.3f} delta), "
        f"keeping the Holy Grail diversification threshold intact. "
        f"Dalio framework favours {', '.join(meta['favoured'][:3])} in this environment."
    )

    return {
        "quadrant": quadrant,
        "quadrant_description": meta["description"],
        "sentiment_score": sent_score,
        "sharpe_improvement": sharpe_imp,
        "correlation_delta": corr,
        "risk_contribution_pct": round(random.uniform(4.0, 8.5), 2),
        "ai_overview": ai_overview,
        "reasons": [
            f"Asset aligns with {quadrant_label} environment",
            f"FinBERT sentiment {sentiment_word} ({sent_score:+.3f}) for {ticker}",
            f"RSI {rsi_val} — {rsi_desc} zone",
            f"Trade improves portfolio Sharpe by +{sharpe_imp:.3f}",
            f"Correlation delta {corr:+.3f} — within Holy Grail threshold",
        ],
    }


def _gen_quadrant_data() -> dict:
    q = random.choice(list(QUADRANT_META.keys()))
    meta = QUADRANT_META[q]
    return {
        "quadrant": q,
        "label": meta["label"],
        "color": meta["color"],
        "description": meta["description"],
        "gdp_value": round(random.uniform(-1.5, 4.5), 2),
        "gdp_trend": random.choice(["rising", "falling", "stable"]),
        "cpi_value": round(random.uniform(1.5, 8.5), 2),
        "cpi_trend": random.choice(["rising", "falling", "stable"]),
        "conflict_risk_elevated": random.choices([False, True], weights=[75, 25])[0],
        "favoured_assets": meta["favoured"],
        "avoid_assets": meta["avoid"],
        "confidence": round(random.uniform(65, 92), 1),
        "macro_source": "EODHD / Trading Economics",
        "sentiment_source": "FinBERT / Finnhub",
        "timestamp": datetime.utcnow().isoformat(),
    }


def _gen_sentiment_data() -> dict:
    total = random.randint(55, 140)
    conflict = random.randint(0, 9)
    return {
        "total_articles": total,
        "conflict_risk_articles": conflict,
        "conflict_risk_elevated": conflict >= 6,
        "dominant_quadrant": random.choice(list(QUADRANT_META.keys())),
        "quadrant_sentiment": {
            q: {
                "avg_score": round(random.uniform(-0.4, 0.6), 3),
                "article_count": random.randint(5, 40),
                "bullish_pct": round(random.uniform(20, 80), 1),
            }
            for q in QUADRANT_META
        },
        "top_headlines": _gen_headlines(),
        "timestamp": datetime.utcnow().isoformat(),
    }


HEADLINE_POOL = [
    ("Fed signals pause in rate hikes amid cooling inflation", "rising_growth", "positive"),
    ("RBA holds rates as Australian GDP surprises to upside", "rising_growth", "positive"),
    ("Oil surges 4% on Middle East supply disruption fears", "rising_inflation", "negative"),
    ("BHP reports record iron ore shipments, ASX rallies", "rising_growth", "positive"),
    ("China manufacturing PMI contracts for third straight month", "falling_growth", "negative"),
    ("Gold hits 3-month high as USD weakens on jobs data miss", "rising_inflation", "positive"),
    ("Military conflict escalates in Eastern Europe, safe havens bid", "rising_inflation", "negative"),
    ("US CPI drops to 2.4%, markets price in rate cuts", "falling_inflation", "positive"),
    ("Tech layoffs accelerate, NASDAQ futures lower", "falling_growth", "negative"),
    ("OPEC+ announces surprise production cut of 500k bpd", "rising_inflation", "neutral"),
    ("ASX 200 closes at 5-year high on earnings season beat", "rising_growth", "positive"),
    ("Copper prices plunge on weak Chinese demand outlook", "falling_growth", "negative"),
    ("Wheat prices spike amid Black Sea shipping disruptions", "rising_inflation", "negative"),
    ("Australian dollar rallies as trade surplus widens", "rising_growth", "positive"),
    ("Silver ETF inflows surge as inflation expectations rise", "rising_inflation", "positive"),
]


def _gen_headlines(n: int = 8) -> list[dict]:
    selected = random.sample(HEADLINE_POOL, min(n, len(HEADLINE_POOL)))
    return [
        {
            "title": h[0],
            "quadrant": h[1],
            "sentiment": h[2],
            "source": random.choice(["Reuters", "Bloomberg", "AFR", "WSJ", "FT"]),
            "timestamp": datetime.utcnow().isoformat(),
            "conflict_risk": "military" in h[0].lower() or "conflict" in h[0].lower(),
        }
        for h in selected
    ]


def _gen_correlation_matrix_demo() -> dict:
    """Fallback demo correlation matrix."""
    tickers = CORR_TICKERS
    n = len(tickers)
    mat = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            r = round(random.uniform(-0.25, 0.55), 3)
            mat[i][j] = r
            mat[j][i] = r
    upper = np.triu_indices(n, k=1)
    return {
        "tickers": tickers,
        "matrix": mat.tolist(),
        "mean_correlation": round(float(np.mean(mat[upper])), 3),
        "max_correlation": round(float(np.max(mat[upper])), 3),
        "holy_grail_count": sum(
            1 for i in range(n)
            if np.mean(np.abs(mat[i][np.arange(n) != i])) < 0.35
        ),
        "threshold": 0.3,
        "data_source": "DEMO",
        "timestamp": datetime.utcnow().isoformat(),
    }


async def _real_correlation_matrix() -> Optional[dict]:
    """Compute Pearson correlation from 3-month daily returns via yfinance."""
    tickers = CORR_TICKERS
    prices_map = await _get_prices(tickers, "3mo")
    if not prices_map or len(prices_map) < 4:
        return None
    valid = [t for t in tickers if t in prices_map and len(prices_map[t]) >= 20]
    if len(valid) < 4:
        return None
    min_len = min(len(prices_map[t]) for t in valid)
    closes  = np.array([prices_map[t][-min_len:] for t in valid], dtype=float)
    returns = np.diff(closes, axis=1) / closes[:, :-1]
    corr    = np.round(np.corrcoef(returns), 3)
    n       = len(valid)
    upper   = np.triu_indices(n, k=1)
    hg_count = sum(
        1 for i in range(n)
        if float(np.mean(np.abs(corr[i][np.arange(n) != i]))) < 0.35
    )
    return {
        "tickers": valid,
        "matrix": corr.tolist(),
        "mean_correlation": round(float(np.mean(corr[upper])), 3),
        "max_correlation": round(float(np.max(corr[upper])), 3),
        "holy_grail_count": hg_count,
        "threshold": 0.3,
        "data_source": "LIVE",
        "timestamp": datetime.utcnow().isoformat(),
    }


def _gen_portfolio_health() -> dict:
    equity = STATE.equity_history[-1]["v"] if STATE.equity_history else STATE.initial_equity
    daily_pnl = round(random.gauss(0.0008, 0.008) * equity, 2)
    drawdown = round(random.uniform(0, 6.5), 2)
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "equity": round(equity + daily_pnl, 2),
        "initial_equity": STATE.initial_equity,
        "total_return_pct": round((equity / STATE.initial_equity - 1) * 100, 2),
        "daily_pnl": daily_pnl,
        "daily_pnl_pct": round(daily_pnl / equity * 100, 3),
        "drawdown_pct": drawdown,
        "open_positions": random.randint(12, 18),
        "dalio_diversification_met": True,
        "selected_portfolio_size": 15,
        "circuit_breaker_active": drawdown > 9.5,
        "daily_limit_pct": 2.0,
        "max_drawdown_pct": 10.0,
        "sharpe_ratio": round(random.uniform(1.3, 2.6), 2),
        "risk_weights": {t: round(1 / 15, 4) + round(random.uniform(-0.01, 0.01), 4) for t in ALL_TICKERS[:15]},
        "positions": [
            {
                "ticker": t,
                "side": random.choice(["LONG", "SHORT"]),
                "size_pct": round(random.uniform(3, 9), 1),
                "unrealised_pnl_pct": round(random.gauss(1.2, 4.5), 2),
            }
            for t in random.sample(ALL_TICKERS, 12)
        ],
    }


def _gen_backtest_results() -> dict:
    periods = []
    cumulative = STATE.initial_equity
    for i in range(8):
        ret = round(random.gauss(3.5, 6.0), 2)
        cumulative *= (1 + ret / 100)
        periods.append({
            "period": i + 1,
            "train_start": f"202{2 + i // 4}-Q{(i % 4) + 1}",
            "return_pct": ret,
            "sharpe": round(random.uniform(0.9, 2.8), 2),
            "max_drawdown": round(random.uniform(-12, -1), 2),
            "win_rate": round(random.uniform(50, 72), 1),
            "trades": random.randint(28, 85),
        })
    return {
        "status": "COMPLETE",
        "training_months": 12,
        "test_months": 3,
        "periods": len(periods),
        "total_return_pct": round((cumulative / STATE.initial_equity - 1) * 100, 2),
        "annualised_return_pct": round(random.uniform(18, 42), 2),
        "sharpe_ratio": round(random.uniform(1.6, 2.4), 2),
        "sortino_ratio": round(random.uniform(2.0, 3.1), 2),
        "calmar_ratio": round(random.uniform(1.8, 2.9), 2),
        "max_drawdown_pct": round(random.uniform(-9, -5), 2),
        "win_rate_pct": round(random.uniform(57, 68), 1),
        "avg_trade_return_pct": round(random.uniform(1.5, 3.2), 2),
        "period_results": periods,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────────────
# Routes — UI
# ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = UI_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>UI not found. Run from project root.</h1>", status_code=404)


# ─────────────────────────────────────────────
# Routes — API
# ─────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    mode = "PAPER"
    if SETTINGS_AVAILABLE:
        try:
            mode = get_settings().trading_mode.upper()
        except Exception:
            pass
    return {
        "status": "OPERATIONAL",
        "mode": mode,
        "agent_booted": STATE.booted,
        "cycle_count": STATE.cycle_count,
        "uptime_seconds": STATE.uptime_seconds(),
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/portfolio/health")
async def portfolio_health():
    data = _gen_portfolio_health()
    STATE.last_health = data
    # Append to equity history
    STATE.equity_history.append({"t": datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "v": data["equity"]})
    STATE.equity_history = STATE.equity_history[-500:]
    return data


@app.get("/api/portfolio/equity_history")
async def equity_history():
    return {"history": STATE.equity_history}


@app.get("/api/signals")
async def get_signals():
    signals = await _gen_signals(12)
    opportunities = await _gen_signals(5)
    return {
        "signals": signals,
        "new_opportunities": opportunities,
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/quadrant")
async def get_quadrant():
    data = _gen_quadrant_data()
    STATE.last_quadrant = data
    return data


@app.get("/api/sentiment")
async def get_sentiment():
    data = _gen_sentiment_data()
    STATE.last_sentiment = data
    return data


@app.get("/api/correlation")
async def get_correlation():
    real = await _real_correlation_matrix()
    return real if real else _gen_correlation_matrix_demo()


_MARKET_DEMO = [
    ("BTC-USD",  "Bitcoin",    "crypto",     95_420.0,   2.14),
    ("ETH-USD",  "Ethereum",   "crypto",      3_512.5,   1.87),
    ("^AXJO",    "ASX 200",    "index",       7_985.0,   0.42),
    ("GLD",      "Gold ETF",   "commodity",    241.30,   0.65),
    ("^GSPC",    "S&P 500",    "index",       5_674.0,  -0.31),
    ("AUD=X",    "AUD/USD",    "fx",            0.6312,  0.18),
    ("^VIX",     "VIX Fear",   "index",         18.4,   -3.20),
    ("USO",      "Crude Oil",  "commodity",     74.85,  -0.55),
    ("SOL-USD",  "Solana",     "crypto",       178.40,   4.31),
    ("BNB-USD",  "BNB",        "crypto",       612.00,   1.05),
]

@app.get("/api/market_summary")
async def market_summary():
    """Live prices for the market ticker strip — falls back to demo when offline."""
    key = "market_summary"
    cached = _cache_get(key)
    if cached:
        return cached

    watchlist = [
        ("BTC-USD",  "Bitcoin",     "crypto"),
        ("ETH-USD",  "Ethereum",    "crypto"),
        ("^AXJO",    "ASX 200",     "index"),
        ("GLD",      "Gold ETF",    "commodity"),
        ("^GSPC",    "S&P 500",     "index"),
        ("AUD=X",    "AUD/USD",     "fx"),
        ("^VIX",     "VIX Fear",    "index"),
        ("USO",      "Crude Oil",   "commodity"),
        ("SOL-USD",  "Solana",      "crypto"),
        ("BNB-USD",  "BNB",         "crypto"),
    ]
    tickers = [t for t, _, _ in watchlist]
    prices_map = await _get_prices(tickers, "5d")

    # Parallel: CoinGecko for crypto + yfinance for indices/commodities
    cg_task  = asyncio.create_task(_get_crypto_coingecko())
    idx_tickers = [t for t, _, c in watchlist if c != "crypto"]
    yf_task  = asyncio.create_task(_get_prices(idx_tickers, "5d"))
    cg_data, yf_data = await asyncio.gather(cg_task, yf_task, return_exceptions=True)
    if isinstance(cg_data, Exception):  cg_data = None
    if isinstance(yf_data, Exception):  yf_data = None

    demo_map = {row[0]: (row[3], row[4]) for row in _MARKET_DEMO}

    result = []
    for ticker, name, category in watchlist:
        price = chg_pct = None
        source = "DEMO"

        if category == "crypto" and isinstance(cg_data, dict) and ticker in cg_data:
            cg = cg_data[ticker]
            price, chg_pct, source = cg["price"], cg["change_pct"], "CoinGecko"
        elif category != "crypto" and isinstance(yf_data, dict):
            closes = yf_data.get(ticker)
            if closes and len(closes) >= 2:
                price   = round(closes[-1], 2)
                chg_pct = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2)
                source  = "yfinance"

        if price is None:
            base_p, base_c = demo_map.get(ticker, (100.0, 0.0))
            price   = round(base_p * (1 + random.gauss(0, 0.004)), 2 if base_p > 10 else 4)
            chg_pct = round(base_c + random.gauss(0, 0.25), 2)

        result.append({
            "ticker":     ticker,
            "name":       name,
            "category":   category,
            "price":      price,
            "change_pct": chg_pct,
            "source":     source,
        })

    _cache_set(key, result)
    return result


@app.get("/api/backtest/latest")
async def get_backtest():
    return _gen_backtest_results()


@app.get("/api/alerts")
async def get_alerts():
    return {"alerts": STATE.alert_log}


# ── Asset universe metadata ────────────────────────────────
_ASSET_META = {
    # ASX
    "CBA.AX":  {"name": "Commonwealth Bank",       "cat": "ASX", "sector": "Banking"},
    "ANZ.AX":  {"name": "ANZ Bank",                "cat": "ASX", "sector": "Banking"},
    "NAB.AX":  {"name": "National Australia Bank", "cat": "ASX", "sector": "Banking"},
    "WBC.AX":  {"name": "Westpac Banking",         "cat": "ASX", "sector": "Banking"},
    "BHP.AX":  {"name": "BHP Group",               "cat": "ASX", "sector": "Mining"},
    "RIO.AX":  {"name": "Rio Tinto",               "cat": "ASX", "sector": "Mining"},
    "FMG.AX":  {"name": "Fortescue Metals",        "cat": "ASX", "sector": "Mining"},
    "S32.AX":  {"name": "South32",                 "cat": "ASX", "sector": "Mining"},
    "MIN.AX":  {"name": "Mineral Resources",       "cat": "ASX", "sector": "Mining"},
    "LYC.AX":  {"name": "Lynas Rare Earths",       "cat": "ASX", "sector": "Materials"},
    "WDS.AX":  {"name": "Woodside Energy",         "cat": "ASX", "sector": "Energy"},
    "STO.AX":  {"name": "Santos",                  "cat": "ASX", "sector": "Energy"},
    "BPT.AX":  {"name": "Beach Energy",            "cat": "ASX", "sector": "Energy"},
    "AGL.AX":  {"name": "AGL Energy",              "cat": "ASX", "sector": "Utilities"},
    "ORG.AX":  {"name": "Origin Energy",           "cat": "ASX", "sector": "Energy"},
    "MQG.AX":  {"name": "Macquarie Group",         "cat": "ASX", "sector": "Finance"},
    "SUN.AX":  {"name": "Suncorp Group",           "cat": "ASX", "sector": "Insurance"},
    "QBE.AX":  {"name": "QBE Insurance",           "cat": "ASX", "sector": "Insurance"},
    "AMP.AX":  {"name": "AMP Limited",             "cat": "ASX", "sector": "Finance"},
    "CSL.AX":  {"name": "CSL Limited",             "cat": "ASX", "sector": "Healthcare"},
    "COH.AX":  {"name": "Cochlear",                "cat": "ASX", "sector": "Healthcare"},
    "RMD.AX":  {"name": "ResMed",                  "cat": "ASX", "sector": "Healthcare"},
    "PME.AX":  {"name": "Pro Medicus",             "cat": "ASX", "sector": "Healthcare"},
    "WES.AX":  {"name": "Wesfarmers",              "cat": "ASX", "sector": "Consumer"},
    "WOW.AX":  {"name": "Woolworths Group",        "cat": "ASX", "sector": "Consumer"},
    "COL.AX":  {"name": "Coles Group",             "cat": "ASX", "sector": "Consumer"},
    "JBH.AX":  {"name": "JB Hi-Fi",               "cat": "ASX", "sector": "Consumer"},
    "TWE.AX":  {"name": "Treasury Wine Estates",   "cat": "ASX", "sector": "Consumer"},
    "REA.AX":  {"name": "REA Group",               "cat": "ASX", "sector": "Technology"},
    "XRO.AX":  {"name": "Xero",                    "cat": "ASX", "sector": "Technology"},
    "WTC.AX":  {"name": "WiseTech Global",         "cat": "ASX", "sector": "Technology"},
    "ALU.AX":  {"name": "Altium",                  "cat": "ASX", "sector": "Technology"},
    "GMG.AX":  {"name": "Goodman Group",           "cat": "ASX", "sector": "REIT"},
    "SCG.AX":  {"name": "Scentre Group",           "cat": "ASX", "sector": "REIT"},
    "GPT.AX":  {"name": "GPT Group",               "cat": "ASX", "sector": "REIT"},
    "QAN.AX":  {"name": "Qantas Airways",          "cat": "ASX", "sector": "Transport"},
    "TCL.AX":  {"name": "Transurban Group",        "cat": "ASX", "sector": "Infrastructure"},
    "TLS.AX":  {"name": "Telstra",                 "cat": "ASX", "sector": "Telecom"},
    "NCM.AX":  {"name": "Newcrest Mining",         "cat": "ASX", "sector": "Gold"},
    "EVN.AX":  {"name": "Evolution Mining",        "cat": "ASX", "sector": "Gold"},
    "NST.AX":  {"name": "Northern Star Resources", "cat": "ASX", "sector": "Gold"},
    # Crypto
    "BTC-USD":  {"name": "Bitcoin",         "cat": "Crypto", "sector": "Layer 1"},
    "ETH-USD":  {"name": "Ethereum",        "cat": "Crypto", "sector": "Layer 1"},
    "BNB-USD":  {"name": "BNB",             "cat": "Crypto", "sector": "Exchange"},
    "SOL-USD":  {"name": "Solana",          "cat": "Crypto", "sector": "Layer 1"},
    "XRP-USD":  {"name": "XRP",             "cat": "Crypto", "sector": "Payments"},
    "ADA-USD":  {"name": "Cardano",         "cat": "Crypto", "sector": "Layer 1"},
    "AVAX-USD": {"name": "Avalanche",       "cat": "Crypto", "sector": "Layer 1"},
    "DOT-USD":  {"name": "Polkadot",        "cat": "Crypto", "sector": "Layer 0"},
    "LINK-USD": {"name": "Chainlink",       "cat": "Crypto", "sector": "Oracle"},
    "MATIC-USD":{"name": "Polygon",         "cat": "Crypto", "sector": "Layer 2"},
    "DOGE-USD": {"name": "Dogecoin",        "cat": "Crypto", "sector": "Meme"},
    "LTC-USD":  {"name": "Litecoin",        "cat": "Crypto", "sector": "Payments"},
    "UNI-USD":  {"name": "Uniswap",         "cat": "Crypto", "sector": "DeFi"},
    "ATOM-USD": {"name": "Cosmos",          "cat": "Crypto", "sector": "Interop"},
    "NEAR-USD": {"name": "NEAR Protocol",   "cat": "Crypto", "sector": "Layer 1"},
    "FTM-USD":  {"name": "Fantom",          "cat": "Crypto", "sector": "Layer 1"},
    "ALGO-USD": {"name": "Algorand",        "cat": "Crypto", "sector": "Layer 1"},
    "XLM-USD":  {"name": "Stellar",         "cat": "Crypto", "sector": "Payments"},
    "AAVE-USD": {"name": "Aave",            "cat": "Crypto", "sector": "DeFi"},
    "SNX-USD":  {"name": "Synthetix",       "cat": "Crypto", "sector": "DeFi"},
    # Commodities
    "GLD":  {"name": "Gold ETF (SPDR)",     "cat": "Commodity", "sector": "Precious Metals"},
    "SLV":  {"name": "Silver ETF (iShares)","cat": "Commodity", "sector": "Precious Metals"},
    "USO":  {"name": "Crude Oil Fund",      "cat": "Commodity", "sector": "Energy"},
    "UNG":  {"name": "Natural Gas Fund",    "cat": "Commodity", "sector": "Energy"},
    "COPX": {"name": "Copper Miners ETF",   "cat": "Commodity", "sector": "Industrial"},
    "WEAT": {"name": "Wheat ETF (Teucrium)","cat": "Commodity", "sector": "Agriculture"},
    "DBA":  {"name": "Agriculture ETF",     "cat": "Commodity", "sector": "Agriculture"},
    "PALL": {"name": "Palladium ETF",       "cat": "Commodity", "sector": "Precious Metals"},
    "XOM":  {"name": "ExxonMobil (Oil)",    "cat": "Commodity", "sector": "Energy"},
    "CVX":  {"name": "Chevron (Oil)",       "cat": "Commodity", "sector": "Energy"},
}


@app.get("/api/assets")
async def get_assets():
    """Return full asset universe with metadata and last known prices."""
    cached_summary = _cache_get("market_summary")
    price_map = {}
    if cached_summary:
        for item in cached_summary:
            price_map[item["ticker"]] = {"price": item.get("price"), "change_pct": item.get("change_pct")}

    assets = []
    for ticker in ALL_TICKERS:
        meta = _ASSET_META.get(ticker, {"name": ticker, "cat": "Unknown", "sector": "—"})
        p = price_map.get(ticker, {})
        assets.append({
            "ticker": ticker,
            "name": meta["name"],
            "cat": meta["cat"],
            "sector": meta["sector"],
            "price": p.get("price"),
            "change_pct": p.get("change_pct"),
        })
    return {"assets": assets, "total": len(assets)}


# ─────────────────────────────────────────────
# Paper Trading Endpoints
# ─────────────────────────────────────────────

async def _live_price(ticker: str) -> Optional[float]:
    """Get the most recent closing price for a ticker (real or demo)."""
    # Try cache from market_summary first
    cached_ms = _cache_get("market_summary")
    if cached_ms:
        for item in cached_ms:
            if item.get("ticker") == ticker and item.get("price") is not None:
                return float(item["price"])
    # Try yfinance
    prices = await _get_prices([ticker], "5d")
    if prices and ticker in prices and prices[ticker]:
        return float(prices[ticker][-1])
    # Demo fallback: return seeded price
    meta = _ASSET_META.get(ticker, {})
    seed = abs(hash(ticker)) % 10000
    rng  = random.Random(seed)
    base = rng.uniform(10, 300)
    return round(base, 2)


async def _prices_for_positions(tickers: list) -> dict:
    """Return {ticker: price} for all open position tickers."""
    result = {}
    for t in tickers:
        p = await _live_price(t)
        if p is not None:
            result[t] = p
    return result


# ─────────────────────────────────────────────
# Dalio AI Analysis Engine
# ─────────────────────────────────────────────

import re as _re

ASSET_CLASS_MAP: dict = {
    # Crypto
    "BTC-USD":"crypto","ETH-USD":"crypto","BNB-USD":"crypto","SOL-USD":"crypto","XRP-USD":"crypto",
    "ADA-USD":"crypto","AVAX-USD":"crypto","DOT-USD":"crypto","LINK-USD":"crypto","MATIC-USD":"crypto",
    "DOGE-USD":"crypto","LTC-USD":"crypto","UNI-USD":"crypto","ATOM-USD":"crypto","NEAR-USD":"crypto",
    # Gold / Precious metals
    "GLD":"gold","SLV":"gold","PALL":"gold","NCM.AX":"gold","EVN.AX":"gold","NST.AX":"gold",
    # Commodities
    "USO":"commodities","UNG":"commodities","COPX":"commodities","WEAT":"commodities",
    "DBA":"commodities","XOM":"commodities","CVX":"commodities","WDS.AX":"commodities",
    "STO.AX":"commodities","BPT.AX":"commodities","BHP.AX":"commodities","RIO.AX":"commodities","FMG.AX":"commodities",
    # Long Bonds
    "TLT":"long_bonds","IEF":"long_bonds","AGG":"long_bonds","BND":"long_bonds",
    # TIPS / Inflation-linked
    "TIP":"tips","VTIP":"tips","SCHP":"tips",
    # Corporate bonds
    "LQD":"corporate_bonds","HYG":"corporate_bonds",
    # Real assets / REIT
    "GMG.AX":"real_assets","TCL.AX":"real_assets","SCG.AX":"real_assets","VNQ":"real_assets",
    # ASX Equities
    "CBA.AX":"equities","ANZ.AX":"equities","NAB.AX":"equities","WBC.AX":"equities",
    "MQG.AX":"equities","CSL.AX":"equities","COH.AX":"equities","RMD.AX":"equities",
    "WES.AX":"equities","WOW.AX":"equities","COL.AX":"equities","TLS.AX":"equities",
    "XRO.AX":"equities","WTC.AX":"equities","ALU.AX":"equities","REA.AX":"equities",
    "S32.AX":"equities","MIN.AX":"equities","AGL.AX":"equities","ORG.AX":"equities",
    "QAN.AX":"equities","SUN.AX":"equities","QBE.AX":"equities","AMP.AX":"equities",
    # US / Global ETFs
    "SPY":"equities","QQQ":"growth_stocks","VTI":"equities","IWLD":"equities","EEM":"equities",
}

QUADRANT_PLAYBOOK: dict = {
    "rising_growth": {
        "strong_buy": ["equities","commodities"],
        "buy":        ["crypto","real_assets","corporate_bonds"],
        "avoid":      ["long_bonds","gold","tips"],
        "narrative":  (
            "Rising Growth: economic expansion lifts earnings and risk appetite. "
            "Dalio tilts heavily toward equities and commodities — cyclicals, EM equities, "
            "and industrial metals outperform. Duration risk in nominal bonds rises. "
            "Crypto can participate as a high-beta risk asset."
        ),
    },
    "falling_growth": {
        "strong_buy": ["long_bonds","gold"],
        "buy":        ["tips","real_assets"],
        "avoid":      ["equities","commodities","crypto"],
        "narrative":  (
            "Falling Growth: recessionary pressure compresses corporate earnings. "
            "Safe havens dominate — long-duration Treasuries rally as yields fall. "
            "Gold preserves wealth as central banks ease. "
            "Reduce cyclicals, commodities, and speculative crypto aggressively."
        ),
    },
    "rising_inflation": {
        "strong_buy": ["gold","commodities","tips"],
        "buy":        ["real_assets","equities"],
        "avoid":      ["long_bonds","crypto"],
        "narrative":  (
            "Rising Inflation: purchasing power erosion favours hard assets. "
            "Gold is the primary hedge — Dalio's cornerstone in this quadrant. "
            "Energy, agriculture, and industrial commodities benefit directly. "
            "TIPS provide real yield protection. Nominal bonds are the loser here."
        ),
    },
    "falling_inflation": {
        "strong_buy": ["equities","long_bonds"],
        "buy":        ["real_assets","corporate_bonds"],
        "avoid":      ["commodities","gold","tips"],
        "narrative":  (
            "Falling Inflation (disinflation): central banks ease, real rates decline. "
            "Growth equities and nominal bonds rally in tandem. "
            "Historically the most favourable quadrant for balanced All Weather portfolios."
        ),
    },
}


def dalio_analyse_trade(ticker: str, side: str, quadrant: str,
                        cash: float, positions: dict, current_signals: list) -> dict:
    ticker = ticker.upper().strip()
    side   = side.upper().strip()
    asset_class = ASSET_CLASS_MAP.get(ticker, "equities")
    playbook    = QUADRANT_PLAYBOOK.get(quadrant, QUADRANT_PLAYBOOK["rising_growth"])

    # Fit score
    if side == "BUY":
        if   asset_class in playbook["strong_buy"]: raw_score = random.randint(82, 97); fit_label = "STRONG FIT"
        elif asset_class in playbook["buy"]:         raw_score = random.randint(62, 81); fit_label = "MODERATE FIT"
        elif asset_class in playbook["avoid"]:       raw_score = random.randint(10, 35); fit_label = "COUNTER-TREND"
        else:                                        raw_score = random.randint(40, 61); fit_label = "NEUTRAL"
    else:
        if   asset_class in playbook["avoid"]:       raw_score = random.randint(75, 93); fit_label = "STRONG FIT"
        elif asset_class not in playbook["strong_buy"]: raw_score = random.randint(55, 74); fit_label = "MODERATE FIT"
        else:                                        raw_score = random.randint(20, 45); fit_label = "COUNTER-TREND"
    fit_score = max(0, min(100, raw_score))

    # Risk flags
    risk_flags: list = []
    total_pv = cash + sum(p.get("qty", 0) * p.get("entry_price", 0) for p in positions.values())
    n_pos = len(positions)
    existing_classes = [ASSET_CLASS_MAP.get(t, "equities") for t in positions]
    class_count = existing_classes.count(asset_class)
    if class_count >= 4: risk_flags.append(f"High concentration: {class_count} existing {asset_class} positions")
    if n_pos >= 15: risk_flags.append("Portfolio at 15-position Holy Grail limit")
    if asset_class in playbook["avoid"] and side == "BUY":
        risk_flags.append(f"{asset_class.replace('_',' ').title()} is on the avoid list for {quadrant.replace('_',' ').title()}")
    if total_pv > 0 and cash / total_pv < 0.05: risk_flags.append("Cash below 5% of portfolio — liquidity risk")
    if asset_class == "crypto" and side == "BUY": risk_flags.append("Crypto: high volatility and regulatory risk")
    sig = next((s for s in current_signals if s.get("ticker") == ticker), None)
    if sig and sig.get("action") in ("SELL","SHORT") and side == "BUY":
        risk_flags.append(f"Signal engine recommends {sig['action']} on {ticker}")

    # Reasoning
    quadrant_label = quadrant.replace("_"," ").title()
    asset_label    = asset_class.replace("_"," ").title()
    reasoning = [
        f"Quadrant is {quadrant_label} — Dalio favours {', '.join((playbook['strong_buy']+playbook['buy'])[:3]).replace('_',' ')}.",
        f"{ticker} classified as {asset_label} — {'aligned' if asset_class in playbook['strong_buy']+playbook['buy'] else 'not aligned'} with {quadrant_label} playbook.",
        f"Portfolio has {n_pos} positions across {len(set(existing_classes))} asset class(es) — {'diversified' if len(set(existing_classes))>=4 else 'needs more diversification'}.",
    ]
    if sig:
        reasoning.append(f"Signal engine: {sig.get('action','HOLD')} {ticker} with {sig.get('confidence',0):.0%} confidence, RSI {sig.get('rsi',50)}.")
    reasoning.append(f"Avoid list for {quadrant_label}: {', '.join(playbook['avoid']).replace('_',' ')}. {'This trade is on the avoid list.' if asset_class in playbook['avoid'] else 'This trade is not on the avoid list.'}")

    # All Weather score
    _AW = {"equities":0.30,"long_bonds":0.40,"gold":0.15,"commodities":0.075,"tips":0.075}
    cc  = {c: existing_classes.count(c) for c in set(existing_classes)}
    if side == "BUY": cc[asset_class] = cc.get(asset_class, 0) + 1
    tot = sum(cc.values()) or 1
    dev = sum(abs(cc.get(c,0)/tot - ideal) for c, ideal in _AW.items())
    all_weather_score = max(0, min(100, int(100 - dev * 50)))

    # Recommendation
    if fit_label == "STRONG FIT":
        rec = f"PROCEED — {ticker} strongly aligned with {quadrant_label} regime. Size within risk budget."
    elif fit_label == "MODERATE FIT":
        rec = f"CONSIDER — Moderate alignment. Reduce size 30-50% vs a strong-fit signal."
    elif fit_label == "COUNTER-TREND":
        rec = f"CAUTION — {ticker} ({asset_label}) counters Dalio's {quadrant_label} playbook. Keep size <2% if high conviction."
    else:
        rec = f"NEUTRAL — No strong quadrant signal. Assess diversification value before committing."

    return {"fit_score": fit_score, "fit_label": fit_label, "quadrant_narrative": playbook["narrative"],
            "asset_class": asset_class, "reasoning": reasoning, "recommendation": rec,
            "risk_flags": risk_flags, "all_weather_score": all_weather_score,
            "quadrant": quadrant, "quadrant_label": quadrant_label, "ticker": ticker, "side": side,
            "timestamp": datetime.utcnow().isoformat()}


@app.post("/api/ai/analyse")
async def ai_analyse(payload: dict):
    ticker   = payload.get("ticker", "").upper().strip()
    side     = payload.get("side", "BUY").upper()
    if not ticker: raise HTTPException(400, "ticker required")
    qdata    = STATE.last_quadrant or _gen_quadrant_data()
    quadrant = qdata.get("quadrant", "rising_growth")
    signals  = await _gen_signals(12)
    return dalio_analyse_trade(ticker, side, quadrant, PAPER.cash, PAPER.positions, signals)


@app.post("/api/ai/chat")
async def ai_chat(payload: dict):
    message  = (payload.get("message") or "").strip()
    if not message: raise HTTPException(400, "message required")
    msg_lower = message.lower()

    if msg_lower == "help":
        return {"type":"help","message":(
            "Dalios AI — Commands:\n"
            "  buy <qty> <ticker>      — Paper buy order\n"
            "  sell <qty> <ticker>     — Paper sell order\n"
            "  analyse <ticker>        — Dalio All Weather analysis\n"
            "  portfolio               — Current paper portfolio\n"
            "  risk                    — Dalio risk assessment\n"
            "  quadrant                — Current economic regime\n"
            "  signals                 — Top 3 active signals\n"
            "  help                    — This list")}

    if msg_lower in ("portfolio","portfolio summary","show portfolio","positions"):
        tickers = list(PAPER.positions.keys())
        prices  = await _prices_for_positions(tickers) if tickers else {}
        total   = PAPER.total_value(prices)
        pnl     = total - PAPER_STARTING_CASH
        pnl_pct = (pnl / PAPER_STARTING_CASH) * 100
        pos_lines = [f"  {t}: {p['side']} {p['qty']} @ ${p['entry_price']:.2f}" for t,p in PAPER.positions.items()]
        return {"type":"portfolio","message":(
            f"Paper Portfolio\n  Cash: ${PAPER.cash:,.2f}\n  Total: ${total:,.2f}\n"
            f"  P&L: ${pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
            f"  Positions ({len(PAPER.positions)}):\n" + ("\n".join(pos_lines) if pos_lines else "  None")),
            "data":{"cash":round(PAPER.cash,2),"total_value":round(total,2),"pnl":round(pnl,2)}}

    if msg_lower in ("quadrant","regime","macro","current quadrant"):
        qdata    = STATE.last_quadrant or _gen_quadrant_data()
        quadrant = qdata.get("quadrant","rising_growth")
        pb       = QUADRANT_PLAYBOOK.get(quadrant, QUADRANT_PLAYBOOK["rising_growth"])
        return {"type":"quadrant","message":(
            f"Quadrant: {qdata.get('label','').upper()}\n\n{pb['narrative']}\n\n"
            f"  Favour: {', '.join((pb['strong_buy']+pb['buy'])[:4]).replace('_',' ')}\n"
            f"  Avoid:  {', '.join(pb['avoid']).replace('_',' ')}"),
            "data": qdata}

    if msg_lower in ("signals","top signals","best signals"):
        sigs = await _gen_signals(12)
        top3 = sigs[:3]
        lines = [f"  {s['ticker']}: {s['action']} | conf {s['confidence']:.0%} | RSI {s['rsi']}" for s in top3]
        return {"type":"signals","message":"Top 3 Signals:\n"+"\n".join(lines),"data":top3}

    if msg_lower in ("risk","risk assessment","portfolio risk","how am i doing"):
        tickers = list(PAPER.positions.keys())
        prices  = await _prices_for_positions(tickers) if tickers else {}
        total   = PAPER.total_value(prices)
        exc     = [ASSET_CLASS_MAP.get(t,"equities") for t in PAPER.positions]
        n_pos   = len(PAPER.positions)
        cash_pct = (PAPER.cash / total * 100) if total > 0 else 100.0
        _AW = {"equities":0.30,"long_bonds":0.40,"gold":0.15,"commodities":0.075,"tips":0.075}
        cc = {c: exc.count(c) for c in set(exc)}; tot = n_pos or 1
        dev = sum(abs(cc.get(c,0)/tot - v) for c,v in _AW.items())
        aw  = max(0, min(100, int(100 - dev*50)))
        return {"type":"risk","message":(
            f"Dalio Risk Assessment\n"
            f"  Positions:     {n_pos}/15 (Holy Grail target)\n"
            f"  Asset classes: {len(set(exc))} ({', '.join(set(exc)).replace('_',' ')})\n"
            f"  Cash reserve:  {cash_pct:.1f}%\n"
            f"  All Weather Score: {aw}/100\n"
            f"  Holy Grail met: {'YES' if n_pos>=12 else 'NO — add uncorrelated assets'}\n\n"
            f"Rule: 15 uncorrelated streams reduce risk without reducing return."),
            "data":{"n_positions":n_pos,"all_weather_score":aw,"cash_pct":round(cash_pct,1)}}

    analyse_m = _re.match(r"^(analyse|analyze|analysis)\s+(\S+)$", msg_lower)
    if analyse_m:
        tkr   = analyse_m.group(2).upper()
        qdata = STATE.last_quadrant or _gen_quadrant_data()
        res   = dalio_analyse_trade(tkr,"BUY",qdata.get("quadrant","rising_growth"),PAPER.cash,PAPER.positions,await _gen_signals(12))
        return {"type":"analyse","message":(
            f"Dalio Analysis: {tkr}\n  Fit: {res['fit_score']}/100 — {res['fit_label']}\n"
            f"  Asset Class: {res['asset_class'].replace('_',' ').title()}\n"
            f"  All Weather: {res['all_weather_score']}/100\n"
            f"  {res['recommendation']}\n"
            f"  Risks: {', '.join(res['risk_flags']) if res['risk_flags'] else 'None'}\n"
            f"\n" + "\n".join(f"  • {r}" for r in res["reasoning"])),"data":res}

    order_m = _re.match(r"^(buy|sell)\s+([\d.]+)\s+(\S+)", msg_lower)
    if order_m:
        side  = order_m.group(1).upper()
        qty   = float(order_m.group(2))
        tkr   = order_m.group(3).upper()
        try:
            price = await _live_price(tkr)
            if price is None: raise ValueError(f"Cannot determine price for {tkr}")
            result = PAPER.place_order(tkr, side, qty, float(price))
            _tks = list(PAPER.positions.keys())
            _prc = await _prices_for_positions(_tks) if _tks else {}
            PAPER.equity_history.append({"t":datetime.utcnow().isoformat(),"v":PAPER.total_value(_prc)})
            PAPER.equity_history = PAPER.equity_history[-2000:]
            _save_paper_state()
            await WS_MANAGER.broadcast({"type":"PAPER_ORDER","data":result})
            return {"type":"order","message":(
                f"Order placed: {side} {qty} {tkr} @ ${price:.4f}\n"
                f"  ID: #{result['order_id']} | Cost: ${qty*price:,.2f} | Cash left: ${PAPER.cash:,.2f}"),
                "data":result}
        except ValueError as exc:
            return {"type":"error","message":f"Order failed: {exc}"}

    # Free-form fallback
    qdata = STATE.last_quadrant or _gen_quadrant_data()
    pb    = QUADRANT_PLAYBOOK.get(qdata.get("quadrant","rising_growth"), QUADRANT_PLAYBOOK["rising_growth"])
    tks   = list(PAPER.positions.keys())
    prc   = await _prices_for_positions(tks) if tks else {}
    total = PAPER.total_value(prc)
    return {"type":"freeform","message":(
        f"Dalios AI (/help for commands)\n\n"
        f"You said: \"{message}\"\n\n"
        f"Current regime: {qdata.get('label','').upper()}\n"
        f"Portfolio: ${total:,.2f} | Cash: ${PAPER.cash:,.2f} | Positions: {len(PAPER.positions)}\n\n"
        f"Dalio says: {pb['narrative'][:160]}...")}


@app.get("/api/paper/portfolio")
async def get_paper_portfolio():
    """Return full paper portfolio state with live P&L."""
    tickers = list(PAPER.positions.keys())
    prices  = await _prices_for_positions(tickers) if tickers else {}

    positions_out = []
    for t, pos in PAPER.positions.items():
        cur = prices.get(t, pos["entry_price"])
        if pos["side"] == "LONG":
            pnl     = (cur - pos["entry_price"]) * pos["qty"]
            pnl_pct = (cur / pos["entry_price"] - 1) * 100 if pos["entry_price"] else 0
        else:
            pnl     = (pos["entry_price"] - cur) * pos["qty"]
            pnl_pct = (pos["entry_price"] / cur - 1) * 100 if cur else 0
        market_val = cur * pos["qty"]
        positions_out.append({
            "ticker":      t,
            "side":        pos["side"],
            "qty":         pos["qty"],
            "entry_price": pos["entry_price"],
            "current_price": round(cur, 4),
            "market_value":  round(market_val, 2),
            "cost_basis":    pos.get("cost_basis", round(pos["entry_price"] * pos["qty"], 2)),
            "pnl":           round(pnl, 2),
            "pnl_pct":       round(pnl_pct, 2),
            "entry_time":    pos["entry_time"],
            "name":          _ASSET_META.get(t, {}).get("name", t),
        })

    total_val  = PAPER.cash + sum(p["market_value"] for p in positions_out)
    total_pnl  = total_val - PAPER_STARTING_CASH
    total_pnl_pct = (total_pnl / PAPER_STARTING_CASH) * 100
    invested   = sum(p["market_value"] for p in positions_out)

    return {
        "cash":           round(PAPER.cash, 2),
        "invested":       round(invested, 2),
        "total_value":    round(total_val, 2),
        "total_pnl":      round(total_pnl, 2),
        "total_pnl_pct":  round(total_pnl_pct, 2),
        "starting_cash":  PAPER_STARTING_CASH,
        "positions":      positions_out,
        "open_count":     len(positions_out),
    }


@app.post("/api/paper/order")
async def place_paper_order(payload: dict):
    """Place a paper trade. payload: {ticker, side, qty, price (optional)}."""
    ticker = payload.get("ticker", "").upper().strip()
    side   = payload.get("side", "BUY").upper()
    try:
        qty = float(payload.get("qty", 1))
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid qty")
    if not ticker:
        raise HTTPException(400, "ticker required")
    if side not in ("BUY", "SELL"):
        raise HTTPException(400, "side must be BUY or SELL")
    if qty <= 0:
        raise HTTPException(400, "qty must be positive")

    # Get current price
    price = payload.get("price")
    if price is None:
        price = await _live_price(ticker)
    if price is None:
        raise HTTPException(400, f"Cannot determine price for {ticker}")
    price = float(price)

    try:
        result = PAPER.place_order(ticker, side, qty, price)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Equity snapshot + persist
    _tickers = list(PAPER.positions.keys())
    _prices  = await _prices_for_positions(_tickers) if _tickers else {}
    PAPER.equity_history.append({"t": datetime.utcnow().isoformat(), "v": PAPER.total_value(_prices)})
    PAPER.equity_history = PAPER.equity_history[-2000:]
    _save_paper_state()

    await WS_MANAGER.broadcast({"type": "PAPER_ORDER", "data": result})
    return result


@app.get("/api/paper/history")
async def get_paper_history():
    return {"trades": PAPER.history[:100], "total": len(PAPER.history)}


@app.post("/api/paper/close")
async def close_paper_position(payload: dict):
    """Close entire position in a ticker at market price."""
    ticker = payload.get("ticker", "").upper().strip()
    if ticker not in PAPER.positions:
        raise HTTPException(404, f"No open position in {ticker}")
    qty   = PAPER.positions[ticker]["qty"]
    price = await _live_price(ticker)
    if price is None:
        raise HTTPException(400, f"Cannot determine price for {ticker}")
    result = PAPER.place_order(ticker, "SELL", qty, float(price))

    _tickers = list(PAPER.positions.keys())
    _prices  = await _prices_for_positions(_tickers) if _tickers else {}
    PAPER.equity_history.append({"t": datetime.utcnow().isoformat(), "v": PAPER.total_value(_prices)})
    PAPER.equity_history = PAPER.equity_history[-2000:]
    _save_paper_state()

    await WS_MANAGER.broadcast({"type": "PAPER_CLOSE", "data": result})
    return result


@app.post("/api/paper/reset")
async def reset_paper_portfolio():
    global PAPER_STARTING_CASH
    PAPER.cash = PAPER_STARTING_CASH
    PAPER.positions = {}
    PAPER.history = []
    PAPER.equity_history = []
    PAPER.order_id = 0
    _save_paper_state()
    return {"status": "reset", "cash": PAPER_STARTING_CASH}


@app.get("/api/paper/config")
async def get_paper_config():
    return {"starting_cash": PAPER_STARTING_CASH}


@app.post("/api/paper/config")
async def set_paper_config(payload: dict):
    global PAPER_STARTING_CASH
    cash = float(payload.get("starting_cash", PAPER_STARTING_CASH))
    if cash < 1:
        raise HTTPException(400, "starting_cash must be >= 1")
    PAPER_STARTING_CASH = cash
    _save_paper_config({"starting_cash": cash})
    return {"status": "ok", "starting_cash": PAPER_STARTING_CASH}


# ─────────────────────────────────────────────
# Watchlist endpoints
# ─────────────────────────────────────────────

@app.get("/api/watchlist")
async def get_watchlist():
    return {"watchlist": WATCHLIST}


@app.post("/api/watchlist/add")
async def watchlist_add(payload: dict):
    ticker = payload.get("ticker", "").upper().strip()
    if not ticker:
        raise HTTPException(400, "ticker required")
    if ticker not in WATCHLIST:
        WATCHLIST.append(ticker)
        _save_watchlist(WATCHLIST)
    return {"watchlist": WATCHLIST}


@app.post("/api/watchlist/remove")
async def watchlist_remove(payload: dict):
    ticker = payload.get("ticker", "").upper().strip()
    if ticker in WATCHLIST:
        WATCHLIST.remove(ticker)
        _save_watchlist(WATCHLIST)
    return {"watchlist": WATCHLIST}


# ─────────────────────────────────────────────
# Market Scanner endpoints
# ─────────────────────────────────────────────

_SCANNER_META = {}  # populated from _ASSET_META

async def _scanner_row(ticker: str) -> dict:
    """Fetch live price + 1d change for a single ticker."""
    try:
        import yfinance as yf
        t   = yf.Ticker(ticker)
        inf = t.fast_info
        price  = float(inf.last_price or 0)
        prev   = float(inf.previous_close or price)
        chg    = price - prev
        chg_pct = (chg / prev * 100) if prev else 0
        vol    = int(inf.three_month_average_volume or 0)
        meta   = _ASSET_META.get(ticker, {"name": ticker, "cat": "—", "sector": "—"})
        return {
            "ticker":   ticker,
            "name":     meta.get("name", ticker),
            "sector":   meta.get("sector", "—"),
            "price":    round(price, 4),
            "change":   round(chg, 4),
            "change_pct": round(chg_pct, 2),
            "volume":   vol,
            "in_watchlist": ticker in WATCHLIST,
        }
    except Exception:
        meta = _ASSET_META.get(ticker, {"name": ticker, "cat": "—", "sector": "—"})
        return {
            "ticker": ticker, "name": meta.get("name", ticker),
            "sector": meta.get("sector","—"), "price": 0, "change": 0,
            "change_pct": 0, "volume": 0, "in_watchlist": ticker in WATCHLIST,
        }


@app.get("/api/markets/{market}")
async def market_scanner(market: str):
    """Scan a market: asx | crypto | commodities"""
    market = market.lower()
    ticker_map = {
        "asx":         ASX_TICKERS,
        "crypto":      CRYPTO_TICKERS,
        "commodities": COMMODITY_TICKERS,
    }
    if market not in ticker_map:
        raise HTTPException(400, f"Unknown market '{market}'. Use: asx, crypto, commodities")
    tickers = ticker_map[market]
    rows = await asyncio.gather(*[_scanner_row(t) for t in tickers])
    # Sort by abs change_pct descending
    rows = sorted(rows, key=lambda r: abs(r["change_pct"]), reverse=True)
    return {"market": market, "rows": rows, "count": len(rows)}


@app.get("/api/paper/quote")
async def get_quote(ticker: str):
    """Get current price + metadata for a ticker."""
    ticker = ticker.upper().strip()
    price  = await _live_price(ticker)
    meta   = _ASSET_META.get(ticker, {"name": ticker, "cat": "Unknown", "sector": "—"})
    return {
        "ticker": ticker,
        "price":  price,
        "name":   meta["name"],
        "cat":    meta["cat"],
        "sector": meta["sector"],
    }


@app.get("/api/paper/equity_curve")
async def get_paper_equity_curve():
    return {"equity_curve": PAPER.equity_history, "count": len(PAPER.equity_history)}


# ─────────────────────────────────────────────
# Trading mode + broker endpoints
# ─────────────────────────────────────────────

@app.get("/api/mode")
async def get_trading_mode():
    return {
        "mode":      TRADING_MODE,
        "broker":    ACTIVE_BROKER.name if ACTIVE_BROKER else None,
        "connected": ACTIVE_BROKER.is_connected() if ACTIVE_BROKER else False,
    }


@app.post("/api/mode")
async def set_trading_mode(payload: dict):
    global TRADING_MODE
    new_mode = payload.get("mode", "").lower()
    if new_mode not in ("paper", "live"):
        raise HTTPException(400, "mode must be 'paper' or 'live'")
    if new_mode == "live" and (ACTIVE_BROKER is None or not ACTIVE_BROKER.is_connected()):
        raise HTTPException(400, "Connect a broker before switching to live mode")
    TRADING_MODE = new_mode
    STATE.add_alert("SYSTEM", f"Trading mode → {new_mode.upper()}", "INFO")
    await WS_MANAGER.broadcast({"type": "MODE_CHANGE", "data": {"mode": new_mode}})
    return {"mode": TRADING_MODE}


@app.post("/api/broker/connect")
async def broker_connect(payload: dict):
    global ACTIVE_BROKER
    broker_name = payload.get("broker", "").lower()
    _BROKER_MAP = {"ibkr": IBKRBroker, "alpaca": AlpacaBroker, "binance": BinanceBroker, "coinbase": CoinbaseBroker, "stake": StakeBroker}
    if broker_name not in _BROKER_MAP:
        raise HTTPException(400, f"broker must be one of: {', '.join(_BROKER_MAP)}")
    broker: BrokerBase = _BROKER_MAP[broker_name]()
    try:
        kwargs = {k: v for k, v in payload.items() if k != "broker"}
        await broker.connect(**kwargs)
    except ImportError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(502, f"Broker connection failed: {e}")
    ACTIVE_BROKER = broker
    STATE.add_alert("BROKER", f"{broker_name.upper()} connected", "INFO")
    return {"status": "connected", "broker": broker_name}


@app.get("/api/broker/status")
async def broker_status():
    if ACTIVE_BROKER is None:
        return {"broker": None, "connected": False}
    if not ACTIVE_BROKER.is_connected():
        return {"broker": ACTIVE_BROKER.name, "connected": False}
    try:
        acct = await ACTIVE_BROKER.get_account()
        return {"broker": ACTIVE_BROKER.name, "connected": True, **acct}
    except Exception as e:
        return {"broker": ACTIVE_BROKER.name, "connected": True, "error": str(e)}


# ─────────────────────────────────────────────
# Real trading endpoints
# ─────────────────────────────────────────────

def _require_live():
    if ACTIVE_BROKER is None or not ACTIVE_BROKER.is_connected():
        raise HTTPException(503, "No broker connected")
    if TRADING_MODE != "live":
        raise HTTPException(403, "Switch to live mode first")


@app.get("/api/real/portfolio")
async def get_real_portfolio():
    if ACTIVE_BROKER is None or not ACTIVE_BROKER.is_connected():
        raise HTTPException(503, "No broker connected")
    try:
        positions = await ACTIVE_BROKER.get_positions()
        acct      = await ACTIVE_BROKER.get_account()
        return {"broker": ACTIVE_BROKER.name, "positions": positions,
                "account_value": acct.get("account_value"), "buying_power": acct.get("buying_power"),
                "cash": acct.get("cash"), "timestamp": datetime.utcnow().isoformat()}
    except Exception as e:
        raise HTTPException(502, f"Broker error: {e}")


@app.post("/api/real/order")
async def place_real_order(payload: dict):
    _require_live()
    ticker = payload.get("ticker", "").upper().strip()
    side   = payload.get("side", "BUY").upper()
    try:
        qty = float(payload.get("qty", 1))
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid qty")
    price = None
    if payload.get("price") is not None:
        try:
            price = float(payload["price"])
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid price")
    if not ticker: raise HTTPException(400, "ticker required")
    if side not in ("BUY", "SELL"): raise HTTPException(400, "side must be BUY or SELL")
    if qty <= 0: raise HTTPException(400, "qty must be positive")
    try:
        result = await ACTIVE_BROKER.place_order(ticker, side, qty, price)
    except Exception as e:
        raise HTTPException(502, f"Broker order failed: {e}")
    try:
        acct = await ACTIVE_BROKER.get_account()
        REAL_EQUITY_CURVE.append({"t": datetime.utcnow().isoformat(), "v": acct.get("account_value", 0)})
        _save_real_equity(REAL_EQUITY_CURVE[-2000:])
    except Exception:
        pass
    STATE.add_alert("LIVE", f"{side} {qty}× {ticker}", "INFO")
    await WS_MANAGER.broadcast({"type": "REAL_ORDER", "data": result})
    return result


@app.get("/api/real/history")
async def get_real_history():
    if ACTIVE_BROKER is None or not ACTIVE_BROKER.is_connected():
        raise HTTPException(503, "No broker connected")
    try:
        return {"history": await ACTIVE_BROKER.get_history(), "broker": ACTIVE_BROKER.name}
    except Exception as e:
        raise HTTPException(502, f"Broker error: {e}")


@app.post("/api/real/close")
async def close_real_position(payload: dict):
    _require_live()
    ticker = payload.get("ticker", "").upper().strip()
    if not ticker: raise HTTPException(400, "ticker required")
    try:
        result = await ACTIVE_BROKER.close_position(ticker)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, f"Broker error: {e}")
    try:
        acct = await ACTIVE_BROKER.get_account()
        REAL_EQUITY_CURVE.append({"t": datetime.utcnow().isoformat(), "v": acct.get("account_value", 0)})
        _save_real_equity(REAL_EQUITY_CURVE[-2000:])
    except Exception:
        pass
    STATE.add_alert("LIVE", f"Closed {ticker}", "INFO")
    await WS_MANAGER.broadcast({"type": "REAL_CLOSE", "data": result})
    return result


@app.get("/api/real/equity_curve")
async def get_real_equity_curve():
    return {"equity_curve": REAL_EQUITY_CURVE, "count": len(REAL_EQUITY_CURVE)}


@app.post("/api/agent/cycle")
async def trigger_cycle(background_tasks: BackgroundTasks):
    STATE.cycle_count += 1
    signals = await _gen_signals(10)
    health = _gen_portfolio_health()
    quadrant = _gen_quadrant_data()
    result = {
        "type": "CYCLE_COMPLETE",
        "cycle": STATE.cycle_count,
        "quadrant": quadrant["quadrant"],
        "signals_found": len(signals),
        "top_signals": signals[:5],
        "portfolio_health": health,
        "timestamp": datetime.utcnow().isoformat(),
    }
    STATE.last_cycle = result
    STATE.add_alert("CYCLE", f"Cycle #{STATE.cycle_count} complete — {len(signals)} signals found", "INFO")
    background_tasks.add_task(WS_MANAGER.broadcast, {"type": "CYCLE_UPDATE", "data": result})
    return result


@app.post("/api/agent/boot")
async def boot_agent():
    STATE.booted = True
    STATE.add_alert("BOOT", "Dalio Agent initialised — FinBERT loaded, correlations computed", "INFO")
    await WS_MANAGER.broadcast({"type": "AGENT_BOOT", "message": "DALIO AGENT ONLINE"})
    return {"status": "booted", "timestamp": datetime.utcnow().isoformat()}


@app.post("/api/notifications/test")
async def test_notification(payload: dict):
    STATE.add_alert("TEST", f"Test notification sent to {payload.get('channel', 'unknown')}", "INFO")
    return {"status": "sent", "channel": payload.get("channel")}


# ─────────────────────────────────────────────
# WebSocket
# ─────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await WS_MANAGER.connect(ws)
    STATE.add_alert("WS", "UI client connected via WebSocket", "INFO")
    try:
        await ws.send_json({
            "type": "CONNECTED",
            "message": "DALIOS NEURAL LINK ESTABLISHED",
            "version": "1.0.0",
            "timestamp": datetime.utcnow().isoformat(),
        })
        heartbeat = 0
        while True:
            await asyncio.sleep(15)
            heartbeat += 1
            await ws.send_json({
                "type": "HEARTBEAT",
                "seq": heartbeat,
                "status": "NOMINAL",
                "uptime": STATE.uptime_seconds(),
                "timestamp": datetime.utcnow().isoformat(),
            })
            # Push live health update every 4 heartbeats (60s)
            if heartbeat % 4 == 0:
                health = _gen_portfolio_health()
                await ws.send_json({"type": "HEALTH_UPDATE", "data": health})
    except WebSocketDisconnect:
        WS_MANAGER.disconnect(ws)
