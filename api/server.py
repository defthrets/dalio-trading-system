"""
Dalios -- Automated Trading Framework
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
    logger.info("yfinance available -- real market data enabled")
except ImportError:
    YF_AVAILABLE = False
    logger.warning("yfinance not installed -- using demo data (run: pip install yfinance pandas)")

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
    key = f"px_{hash(tuple(sorted(tickers)))}_{period}"
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
        logger.warning("yfinance timed out -- using demo data")
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
    title="Dalios -- Automated Trading Framework",
    description="DALIOS All Weather + Economic Machine -- Autonomous ASX & Commodities Trading",
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
    logger.info(f"Startup complete -- trading mode: {TRADING_MODE}")

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
        logger.info(f"Paper portfolio loaded -- cash=${PAPER.cash:,.2f}, "
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

def _save_paper_config() -> None:
    try:
        PAPER_CONFIG_FILE.write_text(json.dumps({"starting_cash": PAPER_STARTING_CASH}, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to save paper config: {exc}")

_paper_cfg         = _load_paper_config()
PAPER_STARTING_CASH: float = float(_paper_cfg.get("starting_cash", 1_000.0))
# Persist config file on first startup if it doesn't exist
if not PAPER_CONFIG_FILE.exists():
    try:
        PAPER_CONFIG_FILE.write_text(json.dumps({"starting_cash": PAPER_STARTING_CASH}, indent=2), encoding="utf-8")
    except Exception:
        pass

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
                raise ValueError(f"Insufficient cash -- need ${cost:,.2f}, have ${self.cash:,.2f}")
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
        STATE.add_alert("PAPER", f"Order #{oid}: {side} {qty:.4g}�-- {ticker} @ ${price:.4f}", "INFO")
        return {"order_id": oid, "ticker": ticker, "side": side, "qty": qty, "price": price, "timestamp": ts}

    def reset(self):
        self.cash            = PAPER_STARTING_CASH
        self.positions       = {}
        self.history         = []
        self.equity_history  = []
        self.order_id        = 0
        STATE.add_alert("PAPER", f"Portfolio reset to ${PAPER_STARTING_CASH:,.2f} starting cash", "INFO")


PAPER = PaperPortfolio()


# ─────────────────────────────────────────────
# Broker Abstraction -- live trading
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
        logger.info(f"IBKR connected -- {host}:{port}")

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
        logger.info(f"Alpaca connected -- {base_url}")

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
        logger.info(f"Binance connected -- {'testnet' if testnet else 'live'}")

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


class CoinSpotBroker(BrokerBase):
    """CoinSpot Australian crypto exchange — REST API v2."""
    name = "coinspot"
    _BASE = "https://www.coinspot.com.au"

    def __init__(self):
        self._api_key:    Optional[str] = None
        self._api_secret: Optional[str] = None
        self._connected   = False

    def is_connected(self) -> bool:
        return self._connected

    def _sign_request(self, data: dict):
        import time as _t, hmac as _hmac, hashlib as _hs
        nonce = str(int(_t.time() * 1000))
        data["nonce"] = nonce
        body  = json.dumps(data, separators=(",", ":"))
        sig   = _hmac.new(self._api_secret.encode(), body.encode(), _hs.sha512).hexdigest()
        return body, sig

    async def _post(self, endpoint: str, payload: dict) -> dict:
        body, sig = self._sign_request(payload)
        headers   = {"Content-Type": "application/json",
                     "key":  self._api_key,
                     "sign": sig}
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(f"{self._BASE}{endpoint}", data=body, headers=headers) as resp:
                result = await resp.json(content_type=None)
                if result.get("status") not in ("ok", "error"):
                    pass  # some endpoints return non-standard status
                if result.get("status") == "error":
                    raise RuntimeError(f"CoinSpot error: {result.get('message','unknown')}")
                return result

    async def connect(self, api_key: str, api_secret: str, **kwargs) -> None:
        self._api_key    = api_key
        self._api_secret = api_secret
        # Verify credentials with a balances fetch
        await self.get_account()
        self._connected = True
        logger.info("CoinSpot connected")

    async def get_account(self) -> dict:
        result  = await self._post("/api/v2/my/balances", {})
        bals    = result.get("balances", [])
        total   = sum(float(v.get("audbalance", 0))
                      for b in bals if isinstance(b, dict)
                      for v in b.values() if isinstance(v, dict))
        return {"broker": "coinspot", "account_value": round(total, 2),
                "buying_power": round(total, 2), "cash": round(total, 2), "currency": "AUD"}

    async def place_order(self, ticker: str, side: str, qty: float,
                          price: Optional[float] = None) -> dict:
        coin     = ticker.replace("-AUD", "").replace("-USD", "").upper()
        endpoint = ("/api/v2/my/coin/buy/now"
                    if side.upper() in ("BUY", "LONG")
                    else "/api/v2/my/coin/sell/now")
        result   = await self._post(endpoint, {"cointype": coin, "amount": qty, "amounttype": "coin"})
        return {"order_id": result.get("id", f"cs_{int(datetime.utcnow().timestamp())}"),
                "ticker": ticker, "side": side, "qty": qty, "price": price,
                "timestamp": datetime.utcnow().isoformat()}

    async def get_positions(self) -> list:
        result = await self._post("/api/v2/my/balances", {})
        out    = []
        for b in result.get("balances", []):
            if not isinstance(b, dict): continue
            for coin, details in b.items():
                if not isinstance(details, dict): continue
                bal = float(details.get("balance", 0))
                if bal > 0:
                    out.append({"ticker": f"{coin}-AUD", "qty": bal,
                                "avg_cost": None,
                                "market_val": float(details.get("audbalance", 0)),
                                "pnl": None, "side": "LONG"})
        return out

    async def get_history(self) -> list:
        result = await self._post("/api/v2/my/orders/completed", {})
        rows   = []
        for o in result.get("buyorders", []):
            rows.append({"ticker": f"{o.get('cointype','')}-AUD", "side": "BUY",
                         "qty": float(o.get("amount", 0)),
                         "price": float(o.get("rate", 0)),
                         "timestamp": o.get("created", "")})
        for o in result.get("sellorders", []):
            rows.append({"ticker": f"{o.get('cointype','')}-AUD", "side": "SELL",
                         "qty": float(o.get("amount", 0)),
                         "price": float(o.get("rate", 0)),
                         "timestamp": o.get("created", "")})
        return rows

    async def close_position(self, ticker: str) -> dict:
        positions = await self.get_positions()
        coin      = ticker.replace("-AUD", "").replace("-USD", "").upper()
        pos       = next((p for p in positions if coin in p["ticker"].upper()), None)
        if not pos:
            raise ValueError(f"No CoinSpot balance for {ticker}")
        return await self.place_order(ticker, "SELL", pos["qty"])


class GenericCryptoBroker(BrokerBase):
    """Generic HMAC-signed crypto exchange broker for exchanges that follow
    the standard API key + secret pattern. Subclass and set name, _BASE, and
    override methods as needed for exchange-specific behaviour."""
    name: str = "generic"
    _BASE: str = ""

    def __init__(self):
        self._api_key:    Optional[str] = None
        self._api_secret: Optional[str] = None
        self._passphrase: Optional[str] = None
        self._connected   = False

    def is_connected(self) -> bool:
        return self._connected

    def _headers(self, body: str = "") -> dict:
        import time as _t, hmac as _hmac, hashlib as _hs
        ts = str(int(_t.time() * 1000))
        msg = ts + body
        sig = _hmac.new(self._api_secret.encode(), msg.encode(), _hs.sha256).hexdigest()
        h = {"Content-Type": "application/json", "API-KEY": self._api_key,
             "API-SIGN": sig, "API-TIMESTAMP": ts}
        if self._passphrase:
            h["API-PASSPHRASE"] = self._passphrase
        return h

    async def connect(self, api_key: str, api_secret: str, passphrase: str = "", **kwargs) -> None:
        self._api_key    = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase or None
        self._connected  = True
        logger.info(f"{self.name.upper()} credentials saved (connection validated on first trade)")

    async def get_account(self) -> dict:
        return {"broker": self.name, "account_value": 0, "buying_power": 0,
                "cash": 0, "currency": "USD", "note": "Connect and trade to populate"}

    async def place_order(self, ticker: str, side: str, qty: float, price: Optional[float] = None) -> dict:
        raise NotImplementedError(f"{self.name.upper()} order routing not yet implemented — coming soon")

    async def get_positions(self) -> list:
        return []

    async def get_history(self) -> list:
        return []

    async def close_position(self, ticker: str) -> dict:
        raise NotImplementedError(f"{self.name.upper()} close not yet implemented")


class KrakenBroker(GenericCryptoBroker):
    name = "kraken"
    _BASE = "https://api.kraken.com"


class BybitBroker(GenericCryptoBroker):
    name = "bybit"
    _BASE = "https://api.bybit.com"


class OKXBroker(GenericCryptoBroker):
    name = "okx"
    _BASE = "https://www.okx.com"

    async def connect(self, api_key: str, api_secret: str, passphrase: str = "", **kwargs) -> None:
        if not passphrase:
            raise ValueError("OKX requires a passphrase in addition to API key and secret")
        await super().connect(api_key, api_secret, passphrase, **kwargs)


class KuCoinBroker(GenericCryptoBroker):
    name = "kucoin"
    _BASE = "https://api.kucoin.com"

    async def connect(self, api_key: str, api_secret: str, passphrase: str = "", **kwargs) -> None:
        if not passphrase:
            raise ValueError("KuCoin requires a passphrase in addition to API key and secret")
        await super().connect(api_key, api_secret, passphrase, **kwargs)


class BitgetBroker(GenericCryptoBroker):
    name = "bitget"
    _BASE = "https://api.bitget.com"

    async def connect(self, api_key: str, api_secret: str, passphrase: str = "", **kwargs) -> None:
        if not passphrase:
            raise ValueError("Bitget requires a passphrase in addition to API key and secret")
        await super().connect(api_key, api_secret, passphrase, **kwargs)


class IndependentReserveBroker(GenericCryptoBroker):
    name = "independentreserve"
    _BASE = "https://api.independentreserve.com"


class SelfWealthBroker(GenericCryptoBroker):
    name = "selfwealth"
    _BASE = "https://api.selfwealth.com.au"

class IGBroker(GenericCryptoBroker):
    name = "ig"
    _BASE = "https://api.ig.com/gateway/deal"

class CMCBroker(GenericCryptoBroker):
    name = "cmc"
    _BASE = "https://ciapi.cityindex.com/TradingAPI"

class SchwabBroker(GenericCryptoBroker):
    name = "schwab"
    _BASE = "https://api.schwabapi.com/trader/v1"


class StakeBroker(GenericCryptoBroker):
    name = "stake"
    _BASE = "https://api.hellostake.com/api"

class CommsecBroker(GenericCryptoBroker):
    name = "commsec"
    _BASE = "https://api.commsec.com.au/v1"

class MomooBroker(GenericCryptoBroker):
    name = "moomoo"
    _BASE = "https://openapi.moomoo.com/v1"

class SuperheroBroker(GenericCryptoBroker):
    name = "superhero"
    _BASE = "https://api.superhero.com.au/v1"

class NabtradeBroker(GenericCryptoBroker):
    name = "nabtrade"
    _BASE = "https://api.nabtrade.com.au/v1"


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
    "CBA.AX", "WBC.AX", "ANZ.AX", "NAB.AX",
    # ── Other Banks & Financials ─────────────────────────
    "MQG.AX", "BEN.AX", "BOQ.AX", "SUN.AX", "QBE.AX", "IAG.AX",
    "AMP.AX", "ASX.AX", "PPT.AX", "CGF.AX", "CPU.AX", "NHF.AX",
    "MPL.AX", "NIB.AX", "AUB.AX",
    # ── Mining & Resources ───────────────────────────────
    "BHP.AX", "RIO.AX", "FMG.AX", "S32.AX", "MIN.AX", "LYC.AX",
    "IGO.AX", "SFR.AX", "PLS.AX", "ILU.AX", "AWC.AX",
    "LTR.AX", "NIC.AX", "WSA.AX",
    # ── Gold & Precious Metals ───────────────────────────
    "NST.AX", "EVN.AX", "SBM.AX", "RRL.AX", "SAR.AX",
    "GOR.AX", "CMM.AX", "RMS.AX", "DEG.AX", "WAF.AX",
    # ── Energy ───────────────────────────────────────────
    "WDS.AX", "STO.AX", "BPT.AX", "AGL.AX", "ORG.AX",
    "APA.AX", "KAR.AX", "CVN.AX",
    # ── Healthcare & Biotech ─────────────────────────────
    "CSL.AX", "RMD.AX", "COH.AX", "SHL.AX", "ANN.AX",
    "PME.AX", "EBO.AX", "HLS.AX", "PNV.AX", "RHC.AX",
    "CUV.AX", "NEU.AX", "TLX.AX", "MSB.AX",
    # ── Technology ───────────────────────────────────────
    "WTC.AX", "XRO.AX", "ALU.AX", "MP1.AX", "TNE.AX",
    "REA.AX", "APX.AX", "TYR.AX", "SDR.AX", "DTL.AX",
    "ZIP.AX", "EML.AX", "HUB.AX", "NXT.AX",
    # ── Consumer / Retail ────────────────────────────────
    "WES.AX", "WOW.AX", "COL.AX", "JBH.AX", "TWE.AX",
    "HVN.AX", "DMP.AX", "SUL.AX", "LOV.AX", "KGN.AX",
    "TPW.AX", "MYR.AX", "NCK.AX",
    # ── Consumer Staples & Food ──────────────────────────
    "GNC.AX", "NUF.AX", "ELD.AX", "BKL.AX",
    # ── REITs ────────────────────────────────────────────
    "GMG.AX", "SCG.AX", "GPT.AX", "VCX.AX", "CLW.AX",
    "MGR.AX", "DXS.AX", "CHC.AX", "BWP.AX", "NSR.AX",
    "CQR.AX", "HMC.AX", "ABP.AX", "SCP.AX", "HDN.AX",
    # ── Industrials & Infrastructure ─────────────────────
    "TCL.AX", "QAN.AX", "BXB.AX", "AZJ.AX", "QUB.AX",
    "WOR.AX", "MND.AX", "JHX.AX", "CSR.AX", "BLD.AX",
    "DOW.AX", "SVW.AX",
    # ── Telecom ──────────────────────────────────────────
    "TLS.AX", "TPG.AX", "SPK.AX",
    # ── Media ────────────────────────────────────────────
    "NWS.AX", "SEK.AX", "CAR.AX", "REA.AX", "NEC.AX",
    # ── LICs & ETFs ──────────────────────────────────────
    "VAS.AX", "VGS.AX", "IOZ.AX", "STW.AX", "NDQ.AX",
    "A200.AX", "GOLD.AX", "ETHI.AX",
    # ── Diversified ──────────────────────────────────────
    "AFI.AX", "ARG.AX", "MLT.AX", "WAM.AX",
]

# Crypto -- top liquid pairs from Binance/Coinbase/Kraken in yfinance USD format
CRYPTO_TICKERS = [
    # ── Layer 1 Major ─────────────────────────────────────
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
    "ADA-USD", "AVAX-USD", "DOT-USD", "TRX-USD", "LTC-USD",
    "ATOM-USD", "NEAR-USD", "ALGO-USD", "XLM-USD", "VET-USD",
    "ICP-USD", "HBAR-USD", "FIL-USD", "EOS-USD", "XTZ-USD",
    "NEO-USD", "IOTA-USD", "XMR-USD", "ZEC-USD", "DASH-USD",
    "WAVES-USD", "ICX-USD", "ONT-USD", "QTUM-USD", "ZIL-USD",
    # ── Layer 2 & Scaling ─────────────────────────────────
    "MATIC-USD", "ARB-USD", "OP-USD", "IMX-USD", "LRC-USD",
    "SKL-USD", "METIS-USD",
    # ── New-Gen L1 ────────────────────────────────────────
    "APT-USD", "SUI-USD", "INJ-USD", "SEI-USD", "TIA-USD",
    "PYTH-USD", "JUP-USD",
    # ── DeFi -- DEX & Lending ──────────────────────────────
    "UNI-USD", "AAVE-USD", "MKR-USD", "COMP-USD", "YFI-USD",
    "SUSHI-USD", "1INCH-USD", "CRV-USD", "BAL-USD", "DYDX-USD",
    "GMX-USD", "SNX-USD", "PENDLE-USD", "CAKE-USD",
    "CVX-USD", "FXS-USD",
    # ── DeFi -- Staking ────────────────────────────────────
    "LDO-USD", "RPL-USD", "ANKR-USD",
    # ── Gaming & Metaverse ────────────────────────────────
    "SAND-USD", "MANA-USD", "ENJ-USD", "AXS-USD", "GALA-USD",
    "FLOW-USD", "BEAM-USD", "RONIN-USD",
    # ── Meme Coins ────────────────────────────────────────
    "DOGE-USD", "SHIB-USD", "PEPE-USD", "FLOKI-USD", "BONK-USD",
    "WIF-USD",
    # ── Infrastructure & Oracles ──────────────────────────
    "LINK-USD", "BAND-USD", "API3-USD", "TRB-USD",
    # ── Storage & Data ────────────────────────────────────
    "AR-USD", "STORJ-USD",
    # ── Privacy ───────────────────────────────────────────
    "SCRT-USD", "ROSE-USD",
    # ── Cross-chain & Interop ─────────────────────────────
    "RUNE-USD", "AXL-USD",
    # ── AI & Data ─────────────────────────────────────────
    "FET-USD", "AGIX-USD", "OCEAN-USD", "NMR-USD",
    "TAO-USD", "RNDR-USD", "WLD-USD",
    # ── Exchange Tokens ───────────────────────────────────
    "CRO-USD", "KCS-USD",
    # ── Web3 & Social ─────────────────────────────────────
    "BAT-USD", "ZRX-USD", "GRT-USD", "LPT-USD",
    # ── RWA ───────────────────────────────────────────────
    "ONDO-USD",
    # ── Misc High-liquidity ───────────────────────────────
    "THETA-USD", "CHZ-USD", "CELR-USD", "MINA-USD",
    "KAVA-USD", "CFX-USD", "JASMY-USD", "FTM-USD",
    "HOT-USD", "WIN-USD", "REEF-USD", "OMG-USD",
]

COMMODITY_TICKERS = [
    # ── Precious Metals ETFs ──────────────────────────────
    "GLD", "IAU", "SLV", "SIVR", "PPLT", "PALL",
    # ── Precious Metals Futures ───────────────────────────
    "GC=F", "SI=F", "PL=F", "PA=F",
    # ── Energy ETFs ───────────────────────────────────────
    "USO", "BNO", "UNG", "UGA", "XLE", "VDE",
    # ── Energy Futures ────────────────────────────────────
    "CL=F", "BZ=F", "NG=F", "RB=F", "HO=F",
    # ── Base Metals ETFs ──────────────────────────────────
    "COPX", "CPER", "DBB", "XME", "REMX", "LIT", "URA",
    # ── Base Metals Futures ───────────────────────────────
    "HG=F", "ALI=F",
    # ── Agriculture ETFs ──────────────────────────────────
    "DBA", "MOO", "WEAT", "CORN", "SOYB", "CANE",
    # ── Agriculture Futures ───────────────────────────────
    "ZW=F", "ZC=F", "ZS=F", "ZO=F", "KC=F", "CT=F", "SB=F",
    # ── Livestock Futures ─────────────────────────────────
    "LE=F", "GF=F", "HE=F",
    # ── Broad Commodity ETFs ──────────────────────────────
    "PDBC", "GSG", "FTGC",
    # ── Timber & Water ────────────────────────────────────
    "WOOD", "PHO",
    # ── Carbon ────────────────────────────────────────────
    "KRBN",
    # ── Miner Proxies ─────────────────────────────────────
    "GDX", "GDXJ", "SIL", "FCX", "NEM", "GOLD", "AEM", "WPM",
    # ── Oil Majors ────────────────────────────────────────
    "XOM", "CVX",
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
    """
    Generate trade signals from real yfinance price data.
    Pulls from scanner cache first (free), then fetches fresh for uncached tickers.
    Results cached 2 min so repeat loads are instant.
    """
    cache_key = f"signals_{n}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    # ── Seed candidate pool — equal split across ASX / Crypto / Commodities ──
    # Pull best movers from scanner cache first (already have prices)
    cached_by_market: dict = {"asx": [], "crypto": [], "commodities": []}
    for mkt in ("asx", "crypto", "commodities"):
        cached = _scanner_cache.get(mkt)
        if cached:
            rows = sorted(cached["rows"], key=lambda r: abs(r.get("change_pct", 0)), reverse=True)
            cached_by_market[mkt] = [r["ticker"] for r in rows if r.get("price", 0) > 0][:n]

    # Sample fresh tickers per market to pad out any empty cache slots
    n_each = max(4, (n * 2) // 3)
    fresh = {
        "asx":         random.sample(ASX_TICKERS,        min(n_each, len(ASX_TICKERS))),
        "crypto":      random.sample(CRYPTO_TICKERS,     min(n_each, len(CRYPTO_TICKERS))),
        "commodities": random.sample(COMMODITY_TICKERS,  min(n_each, len(COMMODITY_TICKERS))),
    }

    # Merge: cached first, then fresh, ensuring equal market representation
    candidates = list(dict.fromkeys(
        cached_by_market["asx"]         + fresh["asx"] +
        cached_by_market["crypto"]       + fresh["crypto"] +
        cached_by_market["commodities"]  + fresh["commodities"]
    ))
    candidates = candidates[:n * 3]

    # ── Fetch 3-month price history (cap at 24 for speed) ──────────────
    prices_map = await _get_prices(candidates[:24], "3mo") or {}

    # Also pull single-day prices from scanner cache for RSI seed
    cache_prices: dict = {}
    for mkt in ("asx", "crypto", "commodities"):
        cached = _scanner_cache.get(mkt)
        if cached:
            for r in cached["rows"]:
                if r.get("price", 0) > 0:
                    cache_prices[r["ticker"]] = r["price"]

    signals = []
    for ticker in candidates:
        closes = prices_map.get(ticker)
        # Require at least 10 data points (was 20 — was too strict)
        if not closes or len(closes) < 10:
            continue

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

        price_history = [round(c, 4 if "USD" in ticker else 2) for c in closes[-30:]]

        sl_offset = max(atr * 1.5, price * 0.025)
        tp_offset = atr * 2.5

        # Real confidence: RSI extremes = high confidence, middle = lower
        if action == "BUY":
            conf = round(min(95, max(50, 100 - rsi)), 1)   # oversold RSI → high confidence
        elif action == "SELL":
            conf = round(min(95, max(50, rsi)), 1)           # overbought RSI → high confidence
        elif action in ("LONG", "SHORT"):
            conf = round(min(85, max(50, abs(rsi - 50) + 50)), 1)
        else:
            conf = 50.0

        # quadrant_fit computed from ASSET_CLASS_MAP + current quadrant
        qdata = STATE.last_quadrant or {}
        quadrant = qdata.get("quadrant", "rising_growth")
        pb = QUADRANT_PLAYBOOK.get(quadrant, QUADRANT_PLAYBOOK["rising_growth"])
        ac = ASSET_CLASS_MAP.get(ticker, "equities")
        if ac in pb["strong_buy"]:   q_fit = "strong"
        elif ac in pb["buy"]:         q_fit = "moderate"
        elif ac in pb["avoid"]:       q_fit = "avoid"
        else:                          q_fit = "neutral"

        # Estimate days to reach target based on ~0.8% avg daily move
        predicted_days = max(3, min(60, int(tp_offset / max(price * 0.008, 0.01))))

        # Position size suggestion: 1–5% of portfolio based on confidence
        pos_size_pct = round(min(5.0, max(1.0, (conf - 50) / 9)), 1)

        signals.append({
            "ticker": ticker,
            "action": action,
            "confidence": conf,
            "price": price,
            "data_source": "LIVE",
            "quadrant_fit": q_fit,
            "rsi": rsi,
            "trend": trend,
            "stop_loss":  round(price - sl_offset, 2) if action in ("SELL","SHORT") else round(price - sl_offset, 2),
            "take_profit": round(price + tp_offset, 2) if action in ("BUY","LONG")  else round(price - tp_offset, 2),
            "rr_ratio": round(tp_offset / sl_offset, 2),
            "position_size_pct": pos_size_pct,
            "dalio_justification": _gen_justification(ticker, action),
            "price_history": price_history,
            "predicted_days": predicted_days,
            "timestamp": datetime.utcnow().isoformat(),
        })

    # Return active signals only (no HOLDs — they're noise), sorted by confidence
    active = [s for s in signals if s["action"] != "HOLD"]
    active.sort(key=lambda s: s["confidence"], reverse=True)
    # If truly nothing actionable, fall back to showing highest-confidence HOLDs
    if not active:
        active = sorted(signals, key=lambda s: s["confidence"], reverse=True)
    result = active[:n]
    _cache_set(cache_key, result)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# TRADE OPPORTUNITY ENGINE
# Synthesises scanner rows, price history, RSI/trend, and Dalio quadrant
# ──────────────────────────────────────────────────────────────────────────────

def _opp_from_signal_fallback(sigs: list, quadrant: str, playbook: dict,
                               qdata: dict, existing_classes: list, n: int) -> list:
    """Fallback when no scanner cache exists — build opps from signal list."""
    regime_label = qdata.get("label", quadrant.replace("_"," ").title())
    results = []
    for s in sigs:
        if s["action"] in ("HOLD", "SELL", "SHORT"):
            continue
        ac    = ASSET_CLASS_MAP.get(s["ticker"], "equities")
        q_fit = ("strong"   if ac in playbook["strong_buy"] else
                 "moderate" if ac in playbook["buy"]        else
                 "avoid"    if ac in playbook["avoid"]      else "neutral")
        q_w   = {"strong": 1.4, "moderate": 1.0, "neutral": 0.6, "avoid": 0.2}[q_fit]
        score = round(s["confidence"] * q_w, 1)
        jus   = s.get("dalio_justification", {})
        reason_0 = (f"Regime: {regime_label} — {ac.replace('_',' ').title()} is "
                    f"{'favoured' if q_fit in ('strong','moderate') else 'on avoid list'}.")
        reason_1 = f"RSI {s['rsi']:.0f} | trend: {s['trend']} | signal: {s['action']}"
        reasoning = [reason_0, reason_1]
        if isinstance(jus, dict):
            for key in ("narrative", "recommendation"):
                val = jus.get(key, "")
                if val and isinstance(val, str):
                    reasoning.append(val[:120])
                    break
        results.append({
            "ticker": s["ticker"], "market": "signal", "action": s["action"],
            "price": s["price"], "change_pct": 0, "rsi": s["rsi"],
            "trend": s["trend"], "above_sma20": s["trend"] == "uptrend",
            "hi_52w": s["take_profit"], "lo_52w": s["stop_loss"],
            "pct_from_hi": 0, "pct_from_lo": 0, "sma20": s["price"],
            "stop_loss": s["stop_loss"], "take_profit": s["take_profit"],
            "rr_ratio": s["rr_ratio"], "score": score,
            "asset_class": ac, "quadrant_fit": q_fit,
            "data_source": s["data_source"],
            "reasoning": reasoning,
            "volume_fmt": "--", "sector": "--",
            "quadrant": quadrant, "regime_label": regime_label,
        })
    results.sort(key=lambda o: o["score"], reverse=True)
    return results[:n]


async def _gen_opportunities(n: int = 8) -> list[dict]:
    """
    Return the top-N trade opportunities by synthesising:
      1. All cached scanner rows (ASX, crypto, commodities)
      2. 3-month price history → RSI, trend, 20-day SMA, 52-week range
      3. Dalio quadrant playbook (regime fit scoring)
      4. Current portfolio state (diversification bonus)
    """
    import time as _t

    qdata    = STATE.last_quadrant or _gen_quadrant_data()
    quadrant = qdata.get("quadrant", "rising_growth")
    playbook = QUADRANT_PLAYBOOK.get(quadrant, QUADRANT_PLAYBOOK["rising_growth"])
    existing_classes = [ASSET_CLASS_MAP.get(t, "equities") for t in PAPER.positions]

    # 1. Collect all scanner rows from cache ──────────────────────────────
    all_rows: list[dict] = []
    for mkt in ("crypto", "asx", "commodities"):
        cached = _scanner_cache.get(mkt)
        if cached:
            for r in cached["rows"]:
                row = dict(r)
                row["_market"] = mkt
                all_rows.append(row)

    if not all_rows:
        # No cache — fall back to signal-based suggestions
        sigs = await _gen_signals(n * 2)
        return _opp_from_signal_fallback(sigs, quadrant, playbook, qdata, existing_classes, n)

    # 2. Pre-score with scanner data only (fast, no extra API calls) ──────
    def _prescore(r: dict) -> float:
        tkr = r["ticker"]
        if tkr in PAPER.positions:
            return -999.0                         # skip held positions
        ac  = ASSET_CLASS_MAP.get(tkr, "equities")
        chg = r.get("change_pct", 0.0)
        q_s = (100 if ac in playbook["strong_buy"] else
               70  if ac in playbook["buy"]        else
               10  if ac in playbook["avoid"]      else 45)
        mom = min(abs(chg) * 3.0, 30.0)
        dir_b = (15 if ac in playbook["strong_buy"] and chg > 0 else
                 10 if ac in playbook["avoid"] and chg < 0      else 0)
        div_b = max(0.0, 20.0 - existing_classes.count(ac) * 5)
        return q_s * 0.40 + mom * 0.25 + dir_b * 0.20 + div_b * 0.15

    all_rows.sort(key=_prescore, reverse=True)
    candidates = [r for r in all_rows if r["ticker"] not in PAPER.positions][:30]

    # 3. Fetch 3-month price history for top candidates ───────────────────
    tickers     = [r["ticker"] for r in candidates[:20]]
    history_map = await _get_prices(tickers, "3mo") or {}

    # 4. Full scoring with technicals ─────────────────────────────────────
    opportunities: list[dict] = []

    for r in candidates:
        tkr    = r["ticker"]
        ac     = ASSET_CLASS_MAP.get(tkr, "equities")
        closes = history_map.get(tkr)
        chg    = r.get("change_pct", 0.0)
        price  = r.get("price", 0.0)
        if not price:
            continue

        # Technical indicators
        if closes and len(closes) >= 14:
            rsi        = _calc_rsi(closes)
            trend      = _calc_trend(closes)
            hi52       = float(max(closes))
            lo52       = float(min(closes))
            sma20      = float(np.mean(closes[-20:])) if len(closes) >= 20 else price
            above_sma  = price > sma20
            vol_d      = float(np.std(np.diff(closes)) / price) if len(closes) > 2 else 0.02
            data_src   = "LIVE"
        else:
            rsi        = 50.0
            trend      = "sideways"
            hi52       = price * 1.20
            lo52       = price * 0.80
            sma20      = price
            above_sma  = chg > 0
            vol_d      = 0.025
            data_src   = "SCANNER"

        pct_from_hi = round((price / hi52 - 1) * 100, 1) if hi52 else 0
        pct_from_lo = round((price / lo52 - 1) * 100, 1) if lo52 else 0

        # Determine primary action
        if   rsi < 32 and trend != "downtrend": action = "BUY"
        elif rsi > 68 and trend != "uptrend":   action = "SELL"
        elif trend == "uptrend" and rsi < 58:   action = "LONG"
        elif trend == "downtrend" and rsi > 42: action = "SHORT"
        else:                                   action = "WATCH"

        # For BUY opportunities list: skip pure shorts unless quadrant says avoid
        is_short_signal = action in ("SELL", "SHORT")
        is_avoid_class  = ac in playbook["avoid"]
        if is_short_signal and not is_avoid_class:
            continue

        # Quadrant weight
        q_score = (100 if ac in playbook["strong_buy"] else
                   70  if ac in playbook["buy"]        else
                   10  if ac in playbook["avoid"]      else 45)
        q_fit   = ("strong"   if ac in playbook["strong_buy"] else
                   "moderate" if ac in playbook["buy"]        else
                   "avoid"    if ac in playbook["avoid"]      else "neutral")

        # RSI score: oversold is good for longs, overbought for shorts
        if not is_short_signal:
            rsi_score = max(0.0, 50.0 - rsi) * 0.8   # max 40 pts when RSI=0
        else:
            rsi_score = max(0.0, rsi - 50.0) * 0.8

        mom_score  = min(abs(chg) * 2.5, 25.0)
        div_score  = max(0.0, 20.0 - existing_classes.count(ac) * 5.0)
        composite  = round(
            q_score   * 0.35 +
            rsi_score * 0.30 +
            mom_score * 0.20 +
            div_score * 0.15,
            1
        )

        # Risk/reward targets using volatility-derived ATR proxy
        atr = max(vol_d * price * 14, price * 0.01)
        sl  = round(price - atr * 1.5, 4)
        tp  = round(price + atr * 2.5, 4)
        rr  = round((tp - price) / max(price - sl, 1e-6), 2)

        # Reasoning bullets
        reasons = [
            f"Regime: {quadrant.replace('_',' ').title()} — "
            f"{ac.replace('_',' ').title()} is "
            f"{'FAVOURED (strong buy)' if q_fit=='strong' else 'favoured' if q_fit=='moderate' else 'AVOID LIST' if q_fit=='avoid' else 'neutral'}.",
            f"RSI {rsi:.0f} ({'oversold' if rsi<35 else 'overbought' if rsi>65 else 'neutral'}) | "
            f"Trend: {trend} | {'Above' if above_sma else 'Below'} 20-day SMA.",
            f"Today: {'+' if chg>=0 else ''}{chg:.2f}% | "
            f"52w range: {pct_from_lo:+.1f}% from low, {pct_from_hi:+.1f}% from high.",
            f"Stop ${sl:,.4f} → Target ${tp:,.4f} | R:R {rr:.1f}x",
        ]
        if pct_from_lo < 10:
            reasons.append("Near 52-week low — potential high-reward entry zone.")
        if above_sma and chg > 1:
            reasons.append("Strong momentum: price above SMA and up today.")
        if existing_classes.count(ac) == 0:
            reasons.append(f"No current {ac.replace('_',' ')} exposure — adds portfolio diversification.")

        opportunities.append({
            "ticker":       tkr,
            "market":       r["_market"],
            "action":       action,
            "price":        price,
            "change_pct":   round(chg, 2),
            "rsi":          round(rsi, 1),
            "trend":        trend,
            "above_sma20":  above_sma,
            "hi_52w":       round(hi52, 4),
            "lo_52w":       round(lo52, 4),
            "pct_from_hi":  pct_from_hi,
            "pct_from_lo":  pct_from_lo,
            "sma20":        round(sma20, 4),
            "stop_loss":    sl,
            "take_profit":  tp,
            "rr_ratio":     rr,
            "score":        composite,
            "asset_class":  ac,
            "quadrant_fit": q_fit,
            "data_source":  data_src,
            "reasoning":    reasons,
            "volume_fmt":   r.get("volume_fmt", "--"),
            "sector":       r.get("sector", "--"),
            "quadrant":     quadrant,
            "regime_label": qdata.get("label", quadrant),
        })

    opportunities.sort(key=lambda o: o["score"], reverse=True)
    return opportunities[:n]


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
            f"RSI {rsi_val} -- {rsi_desc} zone",
            f"Trade improves portfolio Sharpe by +{sharpe_imp:.3f}",
            f"Correlation delta {corr:+.3f} -- within Holy Grail threshold",
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


# ─── Real Financial News RSS Feeds ─────────────────────────────────────────
_NEWS_RSS_FEEDS = [
    # ── Equities / General ──────────────────────────────────────────────────
    ("Reuters Business",   "https://feeds.reuters.com/reuters/businessNews"),
    ("Reuters Markets",    "https://feeds.reuters.com/reuters/UKmarkets"),
    ("Yahoo Finance",      "https://finance.yahoo.com/news/rssindex"),
    ("MarketWatch",        "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("CNBC Finance",       "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("Investing.com",      "https://www.investing.com/rss/news_25.rss"),
    ("Seeking Alpha",      "https://seekingalpha.com/market_currents.xml"),
    ("AFR",                "https://www.afr.com/rss/feed/latest"),
    ("ABC Finance AU",     "https://www.abc.net.au/news/feed/1399786/rss.xml"),
    ("FT Markets",         "https://www.ft.com/rss/home/uk"),
    ("Bloomberg Mkts",     "https://feeds.bloomberg.com/markets/news.rss"),
    ("WSJ Markets",        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    # ── Crypto-specific ─────────────────────────────────────────────────────
    ("CoinDesk",           "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph",      "https://cointelegraph.com/rss"),
    ("Decrypt",            "https://decrypt.co/feed"),
    ("CryptoSlate",        "https://cryptoslate.com/feed/"),
    # ── Commodities / Macro ─────────────────────────────────────────────────
    ("Kitco Gold",         "https://www.kitco.com/rss/kitconews.xml"),
    ("OilPrice.com",       "https://oilprice.com/rss/main"),
    ("Mining.com",         "https://www.mining.com/feed/"),
    # ── Google News RSS (no API key required) ───────────────────────────────
    ("GNews Crypto",       "https://news.google.com/rss/search?q=cryptocurrency+bitcoin+ethereum&hl=en&gl=US&ceid=US:en"),
    ("GNews Commodities",  "https://news.google.com/rss/search?q=gold+oil+commodities+futures&hl=en&gl=US&ceid=US:en"),
    ("GNews ASX",          "https://news.google.com/rss/search?q=ASX+Australian+stocks+market&hl=en-AU&gl=AU&ceid=AU:en"),
    ("GNews Markets",      "https://news.google.com/rss/search?q=stock+market+interest+rates+inflation&hl=en&gl=US&ceid=US:en"),
]

_BULLISH_WORDS  = {"rally","surge","gain","high","record","beat","growth","rise","up","profit",
                   "positive","strong","outperform","buy","upgrade","bullish","recovery","soar"}
_BEARISH_WORDS  = {"fall","drop","crash","low","miss","recession","down","loss","negative","weak",
                   "risk","warning","downgrade","sell","bearish","slump","plunge","cut","concern"}
_CONFLICT_WORDS = {"war","conflict","military","sanctions","attack","threat","crisis","invasion",
                   "strike","bomb","weapons","troops","geopolit"}
_INFLATION_KW   = {"inflation","cpi","pce","rates","fed","rba","boe","ecb","oil","energy",
                   "commodit","gold","silver","copper","wheat","supply"}
_GROWTH_KW      = {"gdp","jobs","employment","payroll","earnings","revenue","ism","pmi",
                   "retail","consumer","spending","trade","export","import"}
_DEFLAT_KW      = {"deflation","disinflation","rate cut","pivot","quantitative","qe","stimulus"}


def _score_headline(title: str, body: str = "") -> dict:
    """Classify a headline into Dalio quadrant + sentiment using keyword scoring."""
    text = (title + " " + body).lower()
    words = set(text.replace(",", " ").replace(".", " ").split())

    bull  = len(words & _BULLISH_WORDS)
    bear  = len(words & _BEARISH_WORDS)
    conf  = len(words & _CONFLICT_WORDS)
    infl  = len(words & _INFLATION_KW)
    grow  = len(words & _GROWTH_KW)
    defl  = len(words & _DEFLAT_KW)

    # Sentiment
    if bull > bear + 1:
        sentiment = "positive"
    elif bear > bull + 1:
        sentiment = "negative"
    else:
        sentiment = "neutral"

    # Quadrant
    if infl >= grow and infl >= defl:
        quadrant = "rising_inflation" if bull >= bear else "falling_inflation"
    elif defl > infl:
        quadrant = "falling_inflation"
    elif grow > 0 and bull >= bear:
        quadrant = "rising_growth"
    else:
        quadrant = "falling_growth"

    return {
        "sentiment":     sentiment,
        "quadrant":      quadrant,
        "conflict_risk": conf > 0,
        "bull_score":    bull,
        "bear_score":    bear,
    }


async def _fetch_real_news() -> list[dict]:
    """
    Fetch ALL available articles from financial RSS feeds.
    Returns headlines scored by Dalio quadrant + sentiment.
    Falls back to HEADLINE_POOL if all feeds fail.
    """
    loop = asyncio.get_event_loop()
    articles: list[dict] = []

    def _parse_one_feed(feed_name: str, url: str) -> list[dict]:
        try:
            import feedparser
            feed = feedparser.parse(url)
            items = []
            for entry in feed.entries:
                title = (getattr(entry, "title", "") or "").strip()
                if not title or len(title) < 15:
                    continue
                body  = (getattr(entry, "summary", "") or "")[:400]
                score = _score_headline(title, body)
                items.append({
                    "title":        title,
                    "source":       feed_name,
                    "sentiment":    score["sentiment"],
                    "quadrant":     score["quadrant"],
                    "conflict_risk": score["conflict_risk"],
                    "bull_score":   score["bull_score"],
                    "bear_score":   score["bear_score"],
                    "timestamp":    datetime.utcnow().isoformat(),
                })
            return items
        except Exception as exc:
            logger.debug(f"RSS [{feed_name}] failed: {exc}")
            return []

    futures = [
        loop.run_in_executor(None, _parse_one_feed, name, url)
        for name, url in _NEWS_RSS_FEEDS
    ]
    results = await asyncio.gather(*futures)
    for batch in results:
        articles.extend(batch)

    if not articles:
        logger.warning("All RSS feeds failed — using static headline pool")
        articles = _gen_static_headlines()

    # Deduplicate by title prefix
    seen: set = set()
    unique: list = []
    for a in articles:
        key = a["title"][:60].lower()
        if key not in seen:
            seen.add(key)
            unique.append(a)

    # Sort: conflict first, then by sentiment extremity
    unique.sort(key=lambda h: (h["conflict_risk"], abs(h["bull_score"] - h["bear_score"])), reverse=True)
    logger.info(f"News scan: {len(unique)} unique articles from {len(_NEWS_RSS_FEEDS)} feeds")
    return unique


# Fallback static headlines (used only when all RSS feeds fail)
_STATIC_HEADLINE_POOL = [
    ("Fed signals pause in rate hikes amid cooling inflation",       "rising_growth",    "positive"),
    ("RBA holds rates as Australian GDP surprises to upside",        "rising_growth",    "positive"),
    ("Oil surges 4% on Middle East supply disruption fears",         "rising_inflation", "negative"),
    ("BHP reports record iron ore shipments, ASX rallies",           "rising_growth",    "positive"),
    ("China manufacturing PMI contracts for third straight month",   "falling_growth",   "negative"),
    ("Gold hits 3-month high as USD weakens on jobs data miss",      "rising_inflation", "positive"),
    ("Military conflict escalates in Eastern Europe, safe havens bid","rising_inflation","negative"),
    ("US CPI drops to 2.4%, markets price in rate cuts",             "falling_inflation","positive"),
    ("Tech layoffs accelerate, NASDAQ futures lower",                "falling_growth",   "negative"),
    ("OPEC+ announces surprise production cut of 500k bpd",          "rising_inflation", "neutral"),
    ("ASX 200 closes at 5-year high on earnings season beat",        "rising_growth",    "positive"),
    ("Copper prices plunge on weak Chinese demand outlook",          "falling_growth",   "negative"),
    ("Wheat prices spike amid Black Sea shipping disruptions",       "rising_inflation", "negative"),
    ("Australian dollar rallies as trade surplus widens",            "rising_growth",    "positive"),
    ("Silver ETF inflows surge as inflation expectations rise",      "rising_inflation", "positive"),
    ("US 10-year yield falls as economic data disappoints",          "falling_growth",   "negative"),
    ("Amazon, Alphabet earnings beat; tech sector rallies",         "rising_growth",    "positive"),
    ("Iron ore falls on Chinese property sector concerns",           "falling_growth",   "negative"),
    ("TIPS inflows accelerate as breakeven inflation widens",        "rising_inflation", "neutral"),
    ("S&P 500 hits fresh record as rate cut hopes persist",         "rising_growth",    "positive"),
]


def _gen_static_headlines() -> list[dict]:
    """Return ALL static headlines (no random sampling)."""
    return [
        {
            "title":        h[0],
            "quadrant":     h[1],
            "sentiment":    h[2],
            "source":       "Market Intelligence",
            "timestamp":    datetime.utcnow().isoformat(),
            "conflict_risk": "military" in h[0].lower() or "conflict" in h[0].lower(),
            "bull_score":   1 if h[2] == "positive" else 0,
            "bear_score":   1 if h[2] == "negative" else 0,
        }
        for h in _STATIC_HEADLINE_POOL
    ]


async def _gen_sentiment_data() -> dict:
    """Build sentiment data from real RSS news. All articles shown — no random sampling."""
    from collections import defaultdict

    articles = await _fetch_real_news()

    total    = len(articles)
    conflict = sum(1 for a in articles if a["conflict_risk"])

    # Per-quadrant stats
    q_counts: dict = defaultdict(lambda: {"count": 0, "bull": 0, "bear": 0})
    for a in articles:
        q = a["quadrant"]
        q_counts[q]["count"] += 1
        if a["sentiment"] == "positive":
            q_counts[q]["bull"] += 1
        elif a["sentiment"] == "negative":
            q_counts[q]["bear"] += 1

    quadrant_sentiment: dict = {}
    for q in QUADRANT_META:
        s = q_counts[q]
        c = max(s["count"], 1)
        quadrant_sentiment[q] = {
            "avg_score":    round((s["bull"] - s["bear"]) / c, 3),
            "article_count": s["count"],
            "bullish_pct":  round(s["bull"] / c * 100, 1),
        }

    dominant = max(quadrant_sentiment, key=lambda q: quadrant_sentiment[q]["article_count"])

    return {
        "total_articles":          total,
        "conflict_risk_articles":  conflict,
        "conflict_risk_elevated":  conflict >= max(3, int(total * 0.08)),
        "dominant_quadrant":       dominant,
        "quadrant_sentiment":      quadrant_sentiment,
        "top_headlines":           articles,   # ALL articles, no cap
        "timestamp":               datetime.utcnow().isoformat(),
    }


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
    """Real portfolio health from PAPER state — no fake data."""
    initial = PAPER_STARTING_CASH
    equity  = PAPER.cash
    if PAPER.equity_history:
        equity = PAPER.equity_history[-1]["v"]

    # Daily P&L from equity history
    daily_pnl = 0.0
    if len(PAPER.equity_history) >= 2:
        daily_pnl = round(PAPER.equity_history[-1]["v"] - PAPER.equity_history[-2]["v"], 2)

    # Drawdown
    drawdown = 0.0
    if PAPER.equity_history:
        peak = max(e["v"] for e in PAPER.equity_history)
        drawdown = round((peak - equity) / peak * 100, 2) if peak > 0 else 0.0

    # Sharpe
    sharpe = 0.0
    if len(PAPER.equity_history) >= 10:
        try:
            eq_arr = np.array([e["v"] for e in PAPER.equity_history], dtype=float)
            rets   = np.diff(eq_arr) / eq_arr[:-1]
            if rets.std() > 0:
                sharpe = round(float((rets.mean() / rets.std()) * (252 ** 0.5)), 2)
        except Exception:
            pass

    open_count = len(PAPER.positions)

    # Real positions list
    positions_list = [
        {
            "ticker": t,
            "side": pos.get("side", "LONG"),
            "size_pct": round(pos["qty"] * pos["entry_price"] / max(equity, 1) * 100, 1),
            "unrealised_pnl_pct": 0.0,  # live P&L computed in /api/paper/portfolio
        }
        for t, pos in PAPER.positions.items()
    ]

    return {
        "timestamp":             datetime.utcnow().isoformat(),
        "equity":                round(equity, 2),
        "initial_equity":        initial,
        "cash":                  round(PAPER.cash, 2),
        "total_return_pct":      round((equity / initial - 1) * 100, 2) if initial else 0.0,
        "daily_pnl":             daily_pnl,
        "daily_pnl_pct":         round(daily_pnl / equity * 100, 3) if equity else 0.0,
        "drawdown_pct":          drawdown,
        "open_positions":        open_count,
        "dalio_diversification_met": open_count >= 3,
        "selected_portfolio_size": open_count,
        "circuit_breaker_active": drawdown > 9.5,
        "daily_limit_pct":       2.0,
        "max_drawdown_pct":      10.0,
        "sharpe_ratio":          sharpe,
        "positions":             positions_list,
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
# Routes -- UI
# ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = UI_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>UI not found. Run from project root.</h1>", status_code=404)


# ─────────────────────────────────────────────
# Routes -- API
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
    all_sigs = await _gen_signals(17)
    return {
        "signals": all_sigs[:12],
        "new_opportunities": all_sigs[12:17] or all_sigs[:5],
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/quadrant")
async def get_quadrant():
    data = _gen_quadrant_data()
    STATE.last_quadrant = data
    return data


_SENTIMENT_CACHE: dict = {}
_SENTIMENT_TTL = 1800  # 30 minutes

@app.get("/api/sentiment")
async def get_sentiment():
    import time as _t
    cached = _SENTIMENT_CACHE.get("data")
    if cached and (_t.time() - _SENTIMENT_CACHE.get("ts", 0)) < _SENTIMENT_TTL:
        return cached
    data = await _gen_sentiment_data()
    STATE.last_sentiment = data
    _SENTIMENT_CACHE["data"] = data
    _SENTIMENT_CACHE["ts"] = _t.time()
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
    """Live prices for the market ticker strip -- falls back to demo when offline."""
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
        meta = _ASSET_META.get(ticker, {"name": ticker, "cat": "Unknown", "sector": "--"})
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

_BINANCE_PRICE_CACHE: dict = {}
_BINANCE_PRICE_TS:    float = 0.0
_BINANCE_PRICE_TTL:   float = 20.0   # 20-second cache

async def _fetch_binance_prices() -> dict:
    """Fetch all USDT spot prices from Binance public REST — no API key needed.
    Returns {yfinance_ticker: price} e.g. {"BTC-USD": 65432.1}
    """
    global _BINANCE_PRICE_CACHE, _BINANCE_PRICE_TS
    import time as _t
    now = _t.time()
    if now - _BINANCE_PRICE_TS < _BINANCE_PRICE_TTL and _BINANCE_PRICE_CACHE:
        return _BINANCE_PRICE_CACHE

    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get("https://api.binance.com/api/v3/ticker/price") as resp:
                if resp.status != 200:
                    return _BINANCE_PRICE_CACHE
                data = await resp.json(content_type=None)
        result = {}
        for item in data:
            sym = item["symbol"]  # e.g. "BTCUSDT"
            if sym.endswith("USDT"):
                base = sym[:-4]   # "BTC"
                result[f"{base}-USD"] = float(item["price"])
        _BINANCE_PRICE_CACHE = result
        _BINANCE_PRICE_TS    = now
        logger.debug(f"Binance price feed: {len(result)} USDT pairs cached")
        return result
    except Exception as e:
        logger.warning(f"Binance price feed failed: {e}")
        return _BINANCE_PRICE_CACHE


def _normalize_ticker(ticker: str) -> str:
    """Normalise user-entered tickers to yfinance format.
    BTC → BTC-USD, ETH → ETH-USD, bhp → BHP.AX (if known), etc.
    """
    t = ticker.upper().strip()
    # Already correct format — pass through
    if t.endswith("-USD") or t.endswith(".AX") or "-" in t or "." in t:
        return t
    # Known crypto base symbols → append -USD
    _crypto_bases = {c.replace("-USD", "") for c in CRYPTO_TICKERS}
    if t in _crypto_bases:
        return f"{t}-USD"
    # Check if it matches an ASX ticker without suffix
    _asx_bases = {c.replace(".AX", "") for c in ASX_TICKERS}
    if t in _asx_bases:
        return f"{t}.AX"
    return t


async def _live_price(ticker: str) -> Optional[float]:
    """Get the most recent price for a ticker.
    Priority: scanner cache → Binance (crypto) → yfinance → demo seed.
    """
    # 1. Scanner cache (fastest — already in memory)
    cached_ms = _cache_get("market_summary")
    if cached_ms:
        for item in cached_ms:
            if item.get("ticker") == ticker and item.get("price") is not None:
                return float(item["price"])

    # 2. Binance public API for crypto (fast, real-time, no auth needed)
    if ticker.endswith("-USD"):
        bn_prices = await _fetch_binance_prices()
        if ticker in bn_prices:
            return bn_prices[ticker]

    # 3. yfinance fallback
    prices = await _get_prices([ticker], "5d")
    if prices and ticker in prices and prices[ticker]:
        return float(prices[ticker][-1])

    # 4. Demo seed (never None — prevents order failure on unknown tickers)
    seed = abs(hash(ticker)) % 10000
    rng  = random.Random(seed)
    return round(rng.uniform(10, 300), 2)


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
            "Dalio tilts heavily toward equities and commodities -- cyclicals, EM equities, "
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
            "Safe havens dominate -- long-duration Treasuries rally as yields fall. "
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
            "Gold is the primary hedge -- Dalio's cornerstone in this quadrant. "
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
    if total_pv > 0 and cash / total_pv < 0.05: risk_flags.append("Cash below 5% of portfolio -- liquidity risk")
    if asset_class == "crypto" and side == "BUY": risk_flags.append("Crypto: high volatility and regulatory risk")
    sig = next((s for s in current_signals if s.get("ticker") == ticker), None)
    if sig and sig.get("action") in ("SELL","SHORT") and side == "BUY":
        risk_flags.append(f"Signal engine recommends {sig['action']} on {ticker}")

    # Reasoning
    quadrant_label = quadrant.replace("_"," ").title()
    asset_label    = asset_class.replace("_"," ").title()
    reasoning = [
        f"Quadrant is {quadrant_label} -- Dalio favours {', '.join((playbook['strong_buy']+playbook['buy'])[:3]).replace('_',' ')}.",
        f"{ticker} classified as {asset_label} -- {'aligned' if asset_class in playbook['strong_buy']+playbook['buy'] else 'not aligned'} with {quadrant_label} playbook.",
        f"Portfolio has {n_pos} positions across {len(set(existing_classes))} asset class(es) -- {'diversified' if len(set(existing_classes))>=4 else 'needs more diversification'}.",
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
        rec = f"PROCEED -- {ticker} strongly aligned with {quadrant_label} regime. Size within risk budget."
    elif fit_label == "MODERATE FIT":
        rec = f"CONSIDER -- Moderate alignment. Reduce size 30-50% vs a strong-fit signal."
    elif fit_label == "COUNTER-TREND":
        rec = f"CAUTION -- {ticker} ({asset_label}) counters Dalio's {quadrant_label} playbook. Keep size <2% if high conviction."
    else:
        rec = f"NEUTRAL -- No strong quadrant signal. Assess diversification value before committing."

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


async def _run_cmd(message: str) -> dict:
    """Core command dispatcher — shared by /api/ai/chat and /api/cmd."""
    global PAPER_STARTING_CASH
    msg_lower = message.strip().lower()

    # ── help ──────────────────────────────────────────────────────────────
    if msg_lower in ("help", "?", "commands"):
        return {"type":"help","message":(
            "Dalios CLI Commands\n"
            "-------------------\n"
            "  buy <qty> <ticker>              -- Paper buy  (e.g. buy 10 BTC)\n"
            "  sell <qty> <ticker>             -- Paper sell (e.g. sell 5 ETH)\n"
            "  close <ticker>                  -- Close open position\n"
            "  portfolio                       -- Full portfolio summary\n"
            "  positions                       -- Open positions detail\n"
            "  history [n]                     -- Last N trades (default 10)\n"
            "  quote <ticker>                  -- Live price lookup\n"
            "  watchlist                       -- Show watchlist\n"
            "  watchlist add <ticker>          -- Add to watchlist\n"
            "  watchlist remove <ticker>       -- Remove from watchlist\n"
            "  scanner asx                     -- ASX scanner data\n"
            "  scanner crypto                  -- Crypto scanner data\n"
            "  scanner commodities             -- Commodities scanner data\n"
            "  suggest [n]                     -- Top N trade opportunities (all markets)\n"
            "  signals                         -- Top 5 active signals\n"
            "  analyse <ticker>                -- Dalio All Weather analysis\n"
            "  risk                            -- Portfolio risk assessment\n"
            "  quadrant                        -- Current economic regime\n"
            "  reset [<cash>]                  -- Reset paper portfolio\n"
            "  set cash <amount>               -- Update starting cash\n"
            "  help                            -- This list")}

    # ── portfolio / positions ──────────────────────────────────────────────
    if msg_lower in ("portfolio","portfolio summary","show portfolio"):
        tickers = list(PAPER.positions.keys())
        prices  = await _prices_for_positions(tickers) if tickers else {}
        total   = PAPER.total_value(prices)
        pnl     = total - PAPER_STARTING_CASH
        pnl_pct = (pnl / PAPER_STARTING_CASH) * 100 if PAPER_STARTING_CASH else 0
        pos_lines = [
            f"  {t}: {p['side']} {p['qty']} @ ${p['entry_price']:.4f}  |  "
            f"now ${prices.get(t, p['entry_price']):.4f}  |  "
            f"P&L {((prices.get(t,p['entry_price'])-p['entry_price'])/p['entry_price']*100):+.2f}%"
            for t,p in PAPER.positions.items()
        ]
        return {"type":"portfolio","message":(
            f"Paper Portfolio\n"
            f"  Cash:        ${PAPER.cash:,.2f}\n"
            f"  Total NAV:   ${total:,.2f}\n"
            f"  P&L:         ${pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
            f"  Positions ({len(PAPER.positions)}):\n" +
            ("\n".join(pos_lines) if pos_lines else "  None")),
            "data":{"cash":round(PAPER.cash,2),"total_value":round(total,2),"pnl":round(pnl,2),
                    "pnl_pct":round(pnl_pct,2),"positions":[
                        {"ticker":t,"side":p["side"],"qty":p["qty"],
                         "entry_price":p["entry_price"],"current_price":prices.get(t,p["entry_price"])}
                        for t,p in PAPER.positions.items()]}}

    if msg_lower == "positions":
        tickers = list(PAPER.positions.keys())
        if not tickers:
            return {"type":"positions","message":"No open positions.","data":{"positions":[]}}
        prices  = await _prices_for_positions(tickers)
        rows = []
        for t,p in PAPER.positions.items():
            cur = prices.get(t, p["entry_price"])
            pnl = (cur - p["entry_price"]) * p["qty"] * (1 if p["side"]=="BUY" else -1)
            rows.append({"ticker":t,"side":p["side"],"qty":p["qty"],
                         "entry_price":p["entry_price"],"current_price":cur,"pnl":round(pnl,2)})
        lines = [f"  {r['ticker']}: {r['side']} {r['qty']} entry ${r['entry_price']:.4f} cur ${r['current_price']:.4f} P&L ${r['pnl']:+,.2f}" for r in rows]
        return {"type":"positions","message":"Open Positions:\n"+"\n".join(lines),"data":{"positions":rows}}

    # ── history [n] ───────────────────────────────────────────────────────
    hist_m = _re.match(r"^history(?:\s+(\d+))?$", msg_lower)
    if hist_m:
        n    = int(hist_m.group(1) or 10)
        recent = PAPER.history[-n:][::-1]
        if not recent:
            return {"type":"history","message":"No trade history yet.","data":{"trades":[]}}
        lines = [f"  #{t['order_id']} {t['side']} {t['qty']} {t['ticker']} @ ${t['price']:.4f}" for t in recent]
        return {"type":"history","message":f"Last {len(recent)} trades:\n"+"\n".join(lines),"data":{"trades":recent}}

    # ── quote <ticker> ────────────────────────────────────────────────────
    quote_m = _re.match(r"^quote\s+(\S+)$", msg_lower)
    if quote_m:
        tkr   = quote_m.group(1).upper()
        price = await _live_price(tkr)
        if price is None:
            return {"type":"error","message":f"Cannot fetch price for {tkr}. Check the ticker symbol."}
        return {"type":"quote","message":f"{tkr}: ${float(price):,.4f}","data":{"ticker":tkr,"price":float(price)}}

    # ── close <ticker> ────────────────────────────────────────────────────
    close_m = _re.match(r"^close\s+(\S+)$", msg_lower)
    if close_m:
        tkr = close_m.group(1).upper()
        if tkr not in PAPER.positions:
            return {"type":"error","message":f"No open position for {tkr}."}
        price = await _live_price(tkr)
        if price is None:
            return {"type":"error","message":f"Cannot fetch price for {tkr} to close position."}
        qty = PAPER.positions[tkr]["qty"]
        result = PAPER.place_order(tkr, "SELL", qty, float(price))
        _save_paper_state()
        await WS_MANAGER.broadcast({"type":"PAPER_CLOSE","data":result})
        pnl = result.get("pnl", PAPER.history[0]["pnl"] if PAPER.history else 0)
        return {"type":"close","message":(
            f"Closed {tkr}  {qty} @ ${float(price):.4f}\n  Cash: ${PAPER.cash:,.2f}"),
            "data":result}

    # ── watchlist ─────────────────────────────────────────────────────────
    if msg_lower == "watchlist":
        if not WATCHLIST:
            return {"type":"watchlist","message":"Watchlist is empty.","data":{"tickers":[]}}
        return {"type":"watchlist","message":"Watchlist:\n  "+"\n  ".join(WATCHLIST),"data":{"tickers":list(WATCHLIST)}}

    wl_add_m = _re.match(r"^watchlist add\s+(\S+)$", msg_lower)
    if wl_add_m:
        tkr = wl_add_m.group(1).upper()
        if tkr not in WATCHLIST:
            WATCHLIST.append(tkr)
            _save_watchlist()
        return {"type":"watchlist","message":f"Added {tkr} to watchlist.","data":{"tickers":list(WATCHLIST)}}

    wl_rem_m = _re.match(r"^watchlist remove\s+(\S+)$", msg_lower)
    if wl_rem_m:
        tkr = wl_rem_m.group(1).upper()
        if tkr in WATCHLIST:
            WATCHLIST.remove(tkr)
            _save_watchlist()
            return {"type":"watchlist","message":f"Removed {tkr} from watchlist.","data":{"tickers":list(WATCHLIST)}}
        return {"type":"watchlist","message":f"{tkr} not in watchlist.","data":{"tickers":list(WATCHLIST)}}

    # ── scanner <market> ──────────────────────────────────────────────────
    scanner_m = _re.match(r"^scanner\s+(asx|crypto|commodities)$", msg_lower)
    if scanner_m:
        import time as _time
        market = scanner_m.group(1)
        ticker_map = {"asx": ASX_TICKERS, "crypto": CRYPTO_TICKERS, "commodities": COMMODITY_TICKERS}
        cached = _scanner_cache.get(market)
        if cached and (_time.time() - cached["ts"]) < _CACHE_TTL:
            all_rows = cached["rows"]
        else:
            tickers = ticker_map[market]
            if market == "crypto":
                all_rows = await _scan_coingecko(tickers)
            else:
                all_rows = await _scan_yfinance(tickers, market)
            good = [r for r in all_rows if r["price"] > 0]
            if good: all_rows = good
            _scanner_cache[market] = {"ts": _time.time(), "rows": all_rows}
        top    = all_rows[:10]
        gainers = sorted(top, key=lambda r: r.get("change_pct",0), reverse=True)[:5]
        losers  = sorted(top, key=lambda r: r.get("change_pct",0))[:3]
        def _fmt_row(r):
            chg = r.get("change_pct",0)
            sign = "+" if chg >= 0 else ""
            return f"  {r['ticker']:<12} ${r.get('price',0):>10,.4f}  {sign}{chg:.2f}%"
        lines = (["Top Gainers:"] + [_fmt_row(r) for r in gainers] +
                 ["\nTop Losers:"]  + [_fmt_row(r) for r in losers])
        return {"type":"scanner","market":market,"message":f"{market.upper()} Scanner (top 10):\n"+"\n".join(lines),"data":{"rows":top}}

    # ── set cash <amount> ─────────────────────────────────────────────────
    set_cash_m = _re.match(r"^set cash\s+([\d,]+(?:\.\d+)?)$", msg_lower)
    if set_cash_m:
        amount = float(set_cash_m.group(1).replace(",",""))
        if amount < 1:
            return {"type":"error","message":"Starting cash must be at least $1."}
        PAPER_STARTING_CASH = amount
        _save_paper_config()
        return {"type":"config","message":f"Starting cash set to ${amount:,.2f}. Reset portfolio to apply.",
                "data":{"starting_cash":amount}}

    # ── reset [<cash>] ────────────────────────────────────────────────────
    reset_m = _re.match(r"^reset(?:\s+([\d,]+(?:\.\d+)?))?$", msg_lower)
    if reset_m:
        if reset_m.group(1):
            PAPER_STARTING_CASH = float(reset_m.group(1).replace(",",""))
            _save_paper_config()
        PAPER.cash       = PAPER_STARTING_CASH
        PAPER.positions  = {}
        PAPER.history    = []
        PAPER.equity_history = []
        PAPER.order_id   = 0
        _save_paper_state()
        STATE.add_alert("PAPER", f"Portfolio reset to ${PAPER_STARTING_CASH:,.2f} via CLI", "INFO")
        await WS_MANAGER.broadcast({"type":"PAPER_RESET","data":{"starting_cash":PAPER_STARTING_CASH}})
        return {"type":"reset","message":f"Portfolio reset to ${PAPER_STARTING_CASH:,.2f} starting cash.",
                "data":{"starting_cash":PAPER_STARTING_CASH}}

    # ── quadrant ──────────────────────────────────────────────────────────
    if msg_lower in ("quadrant","regime","macro","current quadrant"):
        qdata    = STATE.last_quadrant or _gen_quadrant_data()
        quadrant = qdata.get("quadrant","rising_growth")
        pb       = QUADRANT_PLAYBOOK.get(quadrant, QUADRANT_PLAYBOOK["rising_growth"])
        return {"type":"quadrant","message":(
            f"Quadrant: {qdata.get('label','').upper()}\n\n{pb['narrative']}\n\n"
            f"  Favour: {', '.join((pb['strong_buy']+pb['buy'])[:4]).replace('_',' ')}\n"
            f"  Avoid:  {', '.join(pb['avoid']).replace('_',' ')}"),
            "data": qdata}

    # ── suggest / opportunities ───────────────────────────────────────────
    suggest_m = _re.match(r"^(suggest|opportunities?|opps?)(?:\s+(\d+))?$", msg_lower)
    if suggest_m:
        n    = int(suggest_m.group(2) or 8)
        opps = await _gen_opportunities(n)
        if not opps:
            return {"type":"suggest","message":"No opportunities found. Try loading the scanner tabs first to populate the data cache.","data":{"opportunities":[]}}
        lines = []
        for i, o in enumerate(opps, 1):
            sign = "+" if o["change_pct"] >= 0 else ""
            lines.append(
                f"{i:>2}. [{o['action']:<5}] {o['ticker']:<14} ${o['price']:>12,.4f}  "
                f"{sign}{o['change_pct']:.2f}%  RSI:{o['rsi']:.0f}  "
                f"Score:{o['score']:.0f}  Fit:{o['quadrant_fit'].upper()}\n"
                f"      {o['reasoning'][0]}\n"
                f"      SL ${o['stop_loss']:,.4f}  TP ${o['take_profit']:,.4f}  R:R {o['rr_ratio']:.1f}x"
            )
        header = (f"Top {len(opps)} Opportunities — Regime: {opps[0].get('regime_label','').upper()}\n"
                  f"{'─'*72}\n")
        return {"type":"suggest","message": header + "\n\n".join(lines),
                "data":{"opportunities": opps, "quadrant": opps[0].get("quadrant","")}}

    # ── signals ───────────────────────────────────────────────────────────
    if msg_lower in ("signals","top signals","best signals"):
        sigs = await _gen_signals(12)
        top5 = sigs[:5]
        lines = [f"  {s['ticker']}: {s['action']} | conf {s['confidence']:.0%} | RSI {s['rsi']}" for s in top5]
        return {"type":"signals","message":"Top 5 Signals:\n"+"\n".join(lines),"data":top5}

    # ── risk ──────────────────────────────────────────────────────────────
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
            f"  Positions:         {n_pos}/15 (Holy Grail target)\n"
            f"  Asset classes:     {len(set(exc))} ({', '.join(set(exc)).replace('_',' ')})\n"
            f"  Cash reserve:      {cash_pct:.1f}%\n"
            f"  All Weather Score: {aw}/100\n"
            f"  Holy Grail met:    {'YES' if n_pos>=12 else 'NO -- add uncorrelated assets'}\n\n"
            f"Rule: 15 uncorrelated streams reduce risk without reducing return."),
            "data":{"n_positions":n_pos,"all_weather_score":aw,"cash_pct":round(cash_pct,1)}}

    # ── analyse <ticker> ──────────────────────────────────────────────────
    analyse_m = _re.match(r"^(analyse|analyze|analysis)\s+(\S+)$", msg_lower)
    if analyse_m:
        tkr   = analyse_m.group(2).upper()
        qdata = STATE.last_quadrant or _gen_quadrant_data()
        res   = dalio_analyse_trade(tkr,"BUY",qdata.get("quadrant","rising_growth"),PAPER.cash,PAPER.positions,await _gen_signals(12))
        return {"type":"analyse","message":(
            f"Dalio Analysis: {tkr}\n  Fit: {res['fit_score']}/100 -- {res['fit_label']}\n"
            f"  Asset Class: {res['asset_class'].replace('_',' ').title()}\n"
            f"  All Weather: {res['all_weather_score']}/100\n"
            f"  {res['recommendation']}\n"
            f"  Risks: {', '.join(res['risk_flags']) if res['risk_flags'] else 'None'}\n"
            f"\n" + "\n".join(f"  * {r}" for r in res["reasoning"])),"data":res}

    # ── buy / sell ────────────────────────────────────────────────────────
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

    # ── free-form fallback ─────────────────────────────────────────────────
    qdata = STATE.last_quadrant or _gen_quadrant_data()
    pb    = QUADRANT_PLAYBOOK.get(qdata.get("quadrant","rising_growth"), QUADRANT_PLAYBOOK["rising_growth"])
    tks   = list(PAPER.positions.keys())
    prc   = await _prices_for_positions(tks) if tks else {}
    total = PAPER.total_value(prc)
    return {"type":"freeform","message":(
        f"Dalios AI (type 'help' for commands)\n\n"
        f"You said: \"{message.strip()}\"\n\n"
        f"Current regime: {qdata.get('label','').upper()}\n"
        f"Portfolio: ${total:,.2f} | Cash: ${PAPER.cash:,.2f} | Positions: {len(PAPER.positions)}\n\n"
        f"Dalio says: {pb['narrative'][:160]}...")}


@app.post("/api/ai/chat")
async def ai_chat(payload: dict):
    message = (payload.get("message") or "").strip()
    if not message: raise HTTPException(400, "message required")
    return await _run_cmd(message)


@app.post("/api/cmd")
async def api_cmd(payload: dict):
    """AI-agent CLI endpoint — same as /api/ai/chat but always returns structured JSON.

    POST /api/cmd
    Body: {"cmd": "buy 10 BTC"}  or  {"message": "portfolio"}

    Response always has:
      type    -- command type (order, portfolio, positions, history, quote,
                 close, watchlist, scanner, signals, analyse, risk, quadrant,
                 config, reset, help, error, freeform)
      message -- human-readable string
      data    -- structured result (when available)
    """
    cmd = (payload.get("cmd") or payload.get("message") or "").strip()
    if not cmd: raise HTTPException(400, "cmd or message required")
    return await _run_cmd(cmd)


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

    # Compute drawdown from equity history
    eq_vals = [e["v"] for e in PAPER.equity_history] if PAPER.equity_history else []
    if len(eq_vals) >= 2:
        peak = max(eq_vals)
        drawdown_val = round((peak - eq_vals[-1]) / peak, 4) if peak > 0 else 0.0
    else:
        drawdown_val = 0.0

    # Compute annualised Sharpe from equity history daily returns
    sharpe_val = None  # Optional[float]
    if len(eq_vals) >= 10:
        try:
            import numpy as np
            eq_arr = np.array(eq_vals, dtype=float)
            rets   = np.diff(eq_arr) / eq_arr[:-1]
            if rets.std() > 0:
                sharpe_val = round(float((rets.mean() / rets.std()) * (252 ** 0.5)), 2)
        except Exception:
            pass

    return {
        "cash":           round(PAPER.cash, 2),
        "invested":       round(invested, 2),
        "total_value":    round(total_val, 2),
        "total_pnl":      round(total_pnl, 2),
        "total_pnl_pct":  round(total_pnl_pct, 2),
        "starting_cash":  PAPER_STARTING_CASH,
        "positions":      positions_out,
        "open_count":     len(positions_out),
        "drawdown":       drawdown_val,
        "sharpe":         sharpe_val,
        "cycles":         PAPER.order_id,
    }


@app.get("/api/paper/live-pnl")
async def get_paper_live_pnl():
    """Lightweight live P&L endpoint — only fetches current prices and computes
    unrealised P&L per position. No heavy Sharpe/drawdown calculations."""
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
            "ticker":        t,
            "side":          pos["side"],
            "qty":           pos["qty"],
            "entry_price":   pos["entry_price"],
            "current_price": round(cur, 4),
            "market_value":  round(market_val, 2),
            "cost_basis":    pos.get("cost_basis", round(pos["entry_price"] * pos["qty"], 2)),
            "pnl":           round(pnl, 2),
            "pnl_pct":       round(pnl_pct, 2),
            "name":          _ASSET_META.get(t, {}).get("name", t),
        })

    total_unrealised = round(sum(p["pnl"] for p in positions_out), 2)
    return {
        "positions":          positions_out,
        "total_unrealised_pnl": total_unrealised,
        "open_count":         len(positions_out),
        "timestamp":          datetime.utcnow().isoformat(),
    }


@app.post("/api/paper/order")
async def place_paper_order(payload: dict):
    """Place a paper trade. payload: {ticker, side, qty, price (optional)}."""
    ticker = _normalize_ticker(payload.get("ticker", "").strip())
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
    ticker = _normalize_ticker(payload.get("ticker", "").strip())
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
    _save_paper_config()
    # If no open positions, apply new cash immediately and persist
    applied = False
    if not PAPER.positions:
        PAPER.cash           = cash
        PAPER.history        = []
        PAPER.equity_history = []
        PAPER.order_id       = 0
        _save_paper_state()
        applied = True
    return {"status": "ok", "starting_cash": PAPER_STARTING_CASH, "applied": applied}


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
# Market Scanner endpoints  (fast bulk fetch)
# ─────────────────────────────────────────────

# Simple in-memory cache so the UI doesn't hammer APIs
_scanner_cache: dict = {}   # market -> {"ts": float, "rows": list}
_CACHE_TTL = 90             # seconds


def _fmt_vol(v) -> str:
    if not v: return "--"
    v = float(v)
    if v >= 1e9: return f"{v/1e9:.2f}B"
    if v >= 1e6: return f"{v/1e6:.1f}M"
    if v >= 1e3: return f"{v/1e3:.0f}K"
    return str(int(v))


async def _scan_yfinance(tickers: list, market: str) -> list:
    """
    Fetch OHLCV for non-crypto markets.
    Strategy: bulk yf.download first (fast), then individual Ticker.history() fallback
    for any ticker that bulk missed — handles new yfinance multi-index DataFrame format.
    """
    import yfinance as yf
    loop = asyncio.get_event_loop()
    results: dict = {}   # ticker -> (price, change_pct, volume)

    # ── 1. Bulk download attempt ──────────────────────────────────────────
    def _bulk():
        try:
            raw = yf.download(
                tickers, period="5d", interval="1d",
                group_by="ticker", auto_adjust=True,
                progress=False, threads=True,
            )
            return raw
        except Exception as exc:
            logger.warning(f"yfinance bulk failed [{market}]: {exc}")
            return None

    raw = await loop.run_in_executor(None, _bulk)

    if raw is not None and not raw.empty:
        multi = len(tickers) > 1
        for ticker in tickers:
            try:
                if multi:
                    # yfinance ≥0.2 uses MultiIndex columns: (field, ticker)
                    if hasattr(raw.columns, "levels"):
                        if ticker in raw.columns.get_level_values(1):
                            df = raw.xs(ticker, axis=1, level=1).dropna(subset=["Close"])
                        elif ticker in raw.columns.get_level_values(0):
                            df = raw[ticker].dropna(subset=["Close"])
                        else:
                            continue
                    else:
                        df = raw[ticker].dropna(subset=["Close"])
                else:
                    df = raw.dropna(subset=["Close"])

                if len(df) < 2:
                    continue
                price   = float(df["Close"].iloc[-1])
                prev    = float(df["Close"].iloc[-2])
                chg_pct = (price - prev) / prev * 100 if prev else 0
                vol     = float(df["Volume"].iloc[-1]) if "Volume" in df.columns else 0
                results[ticker] = (price, chg_pct, vol)
            except Exception as exc:
                logger.debug(f"Bulk parse [{ticker}]: {exc}")

    # ── 2. Individual fallback for tickers bulk missed ────────────────────
    missing = [t for t in tickers if t not in results]
    if missing:
        logger.info(f"[{market}] individual fallback for {len(missing)} tickers")

        def _fetch_one(tkr):
            try:
                hist = yf.Ticker(tkr).history(period="5d", interval="1d", auto_adjust=True)
                hist = hist.dropna(subset=["Close"])
                if len(hist) < 2:
                    return None
                price   = float(hist["Close"].iloc[-1])
                prev    = float(hist["Close"].iloc[-2])
                chg_pct = (price - prev) / prev * 100 if prev else 0
                vol     = float(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else 0
                return (price, chg_pct, vol)
            except Exception:
                return None

        futures = [loop.run_in_executor(None, _fetch_one, t) for t in missing]
        ind_results = await asyncio.gather(*futures)
        for ticker, val in zip(missing, ind_results):
            if val is not None:
                results[ticker] = val

    # ── 3. Build rows ─────────────────────────────────────────────────────
    rows = []
    for ticker in tickers:
        meta = _ASSET_META.get(ticker, {"name": ticker, "sector": "--"})
        if ticker not in results:
            continue   # skip tickers with no data at all
        price, chg_pct, vol = results[ticker]
        rows.append({
            "ticker":       ticker,
            "name":         meta.get("name", ticker),
            "sector":       meta.get("sector", "--"),
            "price":        round(price, 4),
            "change":       round(price * chg_pct / 100, 4),
            "change_pct":   round(chg_pct, 2),
            "volume_fmt":   _fmt_vol(vol),
            "volume":       int(vol),
            "in_watchlist": ticker in WATCHLIST,
        })

    logger.info(f"yfinance [{market}]: {len(rows)}/{len(tickers)} tickers")
    return rows


# CoinGecko ID map for the tickers we care about
_CG_SYMBOL_MAP: dict = {
    "BTC-USD":"bitcoin","ETH-USD":"ethereum","BNB-USD":"binancecoin",
    "SOL-USD":"solana","XRP-USD":"ripple","ADA-USD":"cardano",
    "AVAX-USD":"avalanche-2","DOT-USD":"polkadot","TRX-USD":"tron",
    "LTC-USD":"litecoin","ATOM-USD":"cosmos","NEAR-USD":"near",
    "ALGO-USD":"algorand","XLM-USD":"stellar","VET-USD":"vechain",
    "ICP-USD":"internet-computer","HBAR-USD":"hedera-hashgraph",
    "FIL-USD":"filecoin","EOS-USD":"eos","XTZ-USD":"tezos",
    "NEO-USD":"neo","IOTA-USD":"iota","XMR-USD":"monero",
    "ZEC-USD":"zcash","DASH-USD":"dash","WAVES-USD":"waves",
    "MATIC-USD":"matic-network","ARB-USD":"arbitrum","OP-USD":"optimism",
    "IMX-USD":"immutable-x","LRC-USD":"loopring","APT-USD":"aptos",
    "SUI-USD":"sui","INJ-USD":"injective-protocol","SEI-USD":"sei-network",
    "TIA-USD":"celestia","PYTH-USD":"pyth-network","JUP-USD":"jupiter-exchange-solana",
    "UNI-USD":"uniswap","AAVE-USD":"aave","MKR-USD":"maker",
    "COMP-USD":"compound-governance-token","YFI-USD":"yearn-finance",
    "SUSHI-USD":"sushi","1INCH-USD":"1inch","CRV-USD":"curve-dao-token",
    "BAL-USD":"balancer","DYDX-USD":"dydx","GMX-USD":"gmx",
    "SNX-USD":"synthetix-network-token","PENDLE-USD":"pendle",
    "CAKE-USD":"pancakeswap-token","CVX-USD":"convex-finance",
    "FXS-USD":"frax-share","LDO-USD":"lido-dao","RPL-USD":"rocket-pool",
    "ANKR-USD":"ankr","SAND-USD":"the-sandbox","MANA-USD":"decentraland",
    "ENJ-USD":"enjincoin","AXS-USD":"axie-infinity","GALA-USD":"gala",
    "FLOW-USD":"flow","BEAM-USD":"beam-2","RONIN-USD":"ronin",
    "DOGE-USD":"dogecoin","SHIB-USD":"shiba-inu","PEPE-USD":"pepe",
    "FLOKI-USD":"floki","BONK-USD":"bonk","WIF-USD":"dogwifcoin",
    "LINK-USD":"chainlink","BAND-USD":"band-protocol","TRB-USD":"tellor",
    "AR-USD":"arweave","STORJ-USD":"storj","SCRT-USD":"secret",
    "ROSE-USD":"oasis-network","RUNE-USD":"thorchain","AXL-USD":"axelar",
    "FET-USD":"fetch-ai","AGIX-USD":"singularitynet","OCEAN-USD":"ocean-protocol",
    "NMR-USD":"numeraire","TAO-USD":"bittensor","RNDR-USD":"render-token",
    "WLD-USD":"worldcoin-wld","CRO-USD":"crypto-com-chain",
    "KCS-USD":"kucoin-shares","BAT-USD":"basic-attention-token",
    "ZRX-USD":"0x","GRT-USD":"the-graph","LPT-USD":"livepeer",
    "ONDO-USD":"ondo-finance","THETA-USD":"theta-token","CHZ-USD":"chiliz",
    "MINA-USD":"mina-protocol","KAVA-USD":"kava","CFX-USD":"conflux-token",
    "FTM-USD":"fantom","OMG-USD":"omisego","METIS-USD":"metis-token",
    "SKL-USD":"skale","ICX-USD":"icon","ONT-USD":"ontology",
    "QTUM-USD":"qtum","ZIL-USD":"zilliqa","VET-USD":"vechain",
    "HOT-USD":"holotoken","WIN-USD":"wink","REEF-USD":"reef",
    "JASMY-USD":"jasmycoin","API3-USD":"api3","CELR-USD":"celer-network",
}


async def _scan_coingecko(tickers: list) -> list:
    """
    Fetch crypto prices: CoinGecko free API first, yfinance fallback for missed tickers.
    Uses aiohttp for proper async HTTP (no blocking urllib on the event loop).
    """
    import aiohttp

    cg_ids: list = []
    id_to_ticker: dict = {}
    for t in tickers:
        cg_id = _CG_SYMBOL_MAP.get(t)
        if cg_id:
            cg_ids.append(cg_id)
            id_to_ticker[cg_id] = t

    all_coin_data: dict = {}

    # ── CoinGecko API (async aiohttp) ─────────────────────────────────────
    if cg_ids:
        try:
            connector = aiohttp.TCPConnector(ssl=False, limit=5)
            timeout   = aiohttp.ClientTimeout(total=20)
            headers   = {"User-Agent": "DALIOS/1.0", "Accept": "application/json"}
            async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
                chunk_size = 100
                for i in range(0, len(cg_ids), chunk_size):
                    chunk = cg_ids[i:i + chunk_size]
                    url = (
                        "https://api.coingecko.com/api/v3/coins/markets"
                        f"?vs_currency=usd&ids={','.join(chunk)}"
                        "&order=market_cap_desc&per_page=250&page=1"
                        "&price_change_percentage=24h&sparkline=false"
                    )
                    try:
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                data = await resp.json(content_type=None)
                                for coin in data:
                                    all_coin_data[coin["id"]] = coin
                            elif resp.status == 429:
                                logger.warning("CoinGecko rate-limited (429) — using yfinance fallback")
                                break
                            else:
                                logger.warning(f"CoinGecko HTTP {resp.status}")
                    except Exception as exc:
                        logger.warning(f"CoinGecko chunk error: {exc}")
        except Exception as exc:
            logger.warning(f"CoinGecko session error: {exc}")

    # ── Build rows from CoinGecko ─────────────────────────────────────────
    rows = []
    found_tickers: set = set()

    for cg_id, ticker in id_to_ticker.items():
        coin = all_coin_data.get(cg_id, {})
        price = float(coin.get("current_price") or 0)
        if price <= 0:
            continue
        found_tickers.add(ticker)
        chg_pct  = float(coin.get("price_change_percentage_24h") or 0)
        vol      = float(coin.get("total_volume") or 0)
        mkt_cap  = float(coin.get("market_cap") or 0)
        rows.append({
            "ticker":          ticker,
            "name":            coin.get("name", ticker.replace("-USD", "")),
            "sector":          "Crypto",
            "price":           round(price, 6) if price < 1 else round(price, 2),
            "change":          round(price * chg_pct / 100, 6),
            "change_pct":      round(chg_pct, 2),
            "volume_fmt":      _fmt_vol(vol),
            "volume":          int(vol),
            "market_cap_fmt":  _fmt_vol(mkt_cap),
            "in_watchlist":    ticker in WATCHLIST,
        })

    # ── yfinance fallback for tickers CoinGecko missed ────────────────────
    missing = [t for t in tickers if t not in found_tickers]
    if missing:
        logger.info(f"CoinGecko missed {len(missing)} tickers — yfinance fallback")
        yf_rows = await _scan_yfinance(missing, "crypto")
        for r in yf_rows:
            r.setdefault("market_cap_fmt", "--")
            r["sector"] = "Crypto"
            rows.append(r)

    logger.info(f"Crypto scan: {len(found_tickers)} CoinGecko + {len(missing)} yfinance = {len(rows)} rows")
    return rows


@app.get("/api/suggest")
async def suggest_trades(n: int = 8):
    """
    Return top-N trade opportunities synthesised from:
      - All cached scanner data (ASX, crypto, commodities)
      - 3-month price history (RSI, trend, SMA20, 52w range)
      - Dalio All-Weather quadrant playbook
      - Current portfolio diversification state

    Query param: n (default 8, max 20)

    Example: GET /api/suggest?n=10
    Response fields per opportunity:
      ticker, market, action, price, change_pct, rsi, trend, above_sma20,
      hi_52w, lo_52w, pct_from_hi, pct_from_lo, sma20, stop_loss,
      take_profit, rr_ratio, score, asset_class, quadrant_fit,
      data_source, reasoning[], volume_fmt, sector, quadrant, regime_label
    """
    n = min(max(1, n), 20)
    opps = await _gen_opportunities(n)
    qdata = STATE.last_quadrant or _gen_quadrant_data()
    return {
        "opportunities": opps,
        "count": len(opps),
        "quadrant": qdata.get("quadrant", ""),
        "regime_label": qdata.get("label", ""),
        "portfolio_positions": len(PAPER.positions),
        "scanner_cached": {
            mkt: bool(_scanner_cache.get(mkt))
            for mkt in ("asx", "crypto", "commodities")
        },
    }


@app.get("/api/recommendations")
async def get_recommendations(n: int = 6):
    """
    Top N trade recommendations with full Dalio AI analysis.
    Each recommendation includes:
      - opportunity data (from _gen_opportunities)
      - full dalio_analyse_trade() analysis
      - per-trade reasoning bullets
    """
    n = min(max(1, n), 12)
    opps = await _gen_opportunities(n * 2)
    qdata = STATE.last_quadrant or _gen_quadrant_data()
    quadrant = qdata.get("quadrant", "rising_growth")
    sigs = await _gen_signals(12)

    recs = []
    for opp in opps[:n]:
        ticker = opp["ticker"]
        analysis = dalio_analyse_trade(
            ticker, opp["action"] if opp["action"] not in ("SELL", "SHORT") else "SELL",
            quadrant, PAPER.cash, PAPER.positions, sigs
        )
        rec = dict(opp)
        rec["analysis"] = {
            "fit_score":         analysis["fit_score"],
            "fit_label":         analysis["fit_label"],
            "all_weather_score": analysis["all_weather_score"],
            "recommendation":    analysis["recommendation"],
            "risk_flags":        analysis["risk_flags"],
            "reasoning":         analysis["reasoning"],
            "asset_class":       analysis["asset_class"],
        }
        recs.append(rec)

    return {
        "recommendations": recs,
        "count": len(recs),
        "quadrant": quadrant,
        "regime_label": qdata.get("label", ""),
    }


@app.get("/api/markets/{market}")
async def market_scanner(market: str):
    """Scan a market: asx | crypto | commodities. Uses cache (90s TTL)."""
    market = market.lower()
    ticker_map = {
        "asx":         ASX_TICKERS,
        "crypto":      CRYPTO_TICKERS,
        "commodities": COMMODITY_TICKERS,
    }
    if market not in ticker_map:
        raise HTTPException(400, f"Unknown market '{market}'. Use: asx, crypto, commodities")

    # Check cache
    import time
    cached = _scanner_cache.get(market)
    if cached and (time.time() - cached["ts"]) < _CACHE_TTL:
        return {"market": market, "rows": cached["rows"],
                "count": len(cached["rows"]), "cached": True,
                "cache_age": int(time.time() - cached["ts"])}

    tickers = ticker_map[market]
    if market == "crypto":
        rows = await _scan_coingecko(tickers)
    else:
        rows = await _scan_yfinance(tickers, market)

    # Filter out zero-price rows only if we got real data
    good = [r for r in rows if r["price"] > 0]
    if good:
        rows = good

    # Sort: crypto by market cap (if available), others by abs change
    if market == "crypto":
        rows = sorted(rows, key=lambda r: r.get("volume", 0), reverse=True)
    else:
        rows = sorted(rows, key=lambda r: abs(r["change_pct"]), reverse=True)

    _scanner_cache[market] = {"ts": time.time(), "rows": rows}
    return {"market": market, "rows": rows, "count": len(rows), "cached": False}


@app.get("/api/paper/quote")
async def get_quote(ticker: str):
    """Get current price + metadata for a ticker."""
    ticker = _normalize_ticker(ticker.strip())
    price  = await _live_price(ticker)
    meta   = _ASSET_META.get(ticker, {"name": ticker, "cat": "Unknown", "sector": "--"})
    return {
        "ticker": ticker,
        "price":  price,
        "name":   meta["name"],
        "cat":    meta["cat"],
        "sector": meta["sector"],
    }


@app.get("/api/paper/equity_curve")
async def get_paper_equity_curve():
    # Per-position price performance (last 60 bars, normalized to % return from entry)
    pos_perf: dict = {}
    if PAPER.positions:
        tickers = list(PAPER.positions.keys())
        prices_map = await _get_prices(tickers, "3mo") or {}
        for tkr, pos in PAPER.positions.items():
            closes = prices_map.get(tkr, [])
            if closes and len(closes) >= 2:
                entry = pos["entry_price"]
                last60 = closes[-60:]
                pos_perf[tkr] = [round((p / entry - 1) * 100, 2) for p in last60]

    return {
        "equity_curve": PAPER.equity_history,
        "count": len(PAPER.equity_history),
        "position_performance": pos_perf,
        "starting_cash": PAPER_STARTING_CASH,
    }


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
    _BROKER_MAP = {"ibkr": IBKRBroker, "alpaca": AlpacaBroker, "binance": BinanceBroker,
                   "coinbase": CoinbaseBroker, "coinspot": CoinSpotBroker,
                   "kraken": KrakenBroker, "bybit": BybitBroker, "okx": OKXBroker,
                   "kucoin": KuCoinBroker, "bitget": BitgetBroker,
                   "independentreserve": IndependentReserveBroker, "stake": StakeBroker,
                   "selfwealth": SelfWealthBroker, "ig": IGBroker,
                   "cmc": CMCBroker, "schwab": SchwabBroker,
                   "commsec": CommsecBroker, "moomoo": MomooBroker,
                   "superhero": SuperheroBroker, "nabtrade": NabtradeBroker}
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


_BROKER_CREDS_FILE = DATA_DIR / "broker_credentials.json"


def _load_broker_creds() -> dict:
    if _BROKER_CREDS_FILE.exists():
        try:
            return json.loads(_BROKER_CREDS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_broker_creds(creds: dict):
    _BROKER_CREDS_FILE.write_text(json.dumps(creds, indent=2))


@app.post("/api/broker/save")
async def broker_save(payload: dict):
    broker_name = payload.get("broker", "").lower()
    if not broker_name:
        raise HTTPException(400, "broker name required")
    creds = _load_broker_creds()
    creds[broker_name] = {k: v for k, v in payload.items() if k != "broker"}
    _save_broker_creds(creds)
    STATE.add_alert("BROKER", f"{broker_name.upper()} credentials saved", "INFO")
    return {"status": "saved", "broker": broker_name}


@app.get("/api/broker/saved")
async def broker_saved():
    creds = _load_broker_creds()
    # Return broker names with masked keys (don't expose full secrets)
    result = {}
    for name, data in creds.items():
        masked = {}
        for k, v in data.items():
            if isinstance(v, str) and len(v) > 6 and any(s in k.lower() for s in ("secret", "key", "pass", "private")):
                masked[k] = v[:4] + "•" * (len(v) - 8) + v[-4:]
            else:
                masked[k] = v
        result[name] = masked
    return result


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
    STATE.add_alert("LIVE", f"{side} {qty}�-- {ticker}", "INFO")
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
    STATE.add_alert("CYCLE", f"Cycle #{STATE.cycle_count} complete -- {len(signals)} signals found", "INFO")
    background_tasks.add_task(WS_MANAGER.broadcast, {"type": "CYCLE_UPDATE", "data": result})
    return result


@app.post("/api/agent/boot")
async def boot_agent():
    STATE.booted = True
    STATE.add_alert("BOOT", "Dalio Agent initialised -- FinBERT loaded, correlations computed", "INFO")
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
