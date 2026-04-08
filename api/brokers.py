"""
Dalios -- Broker Implementations
All broker classes: BrokerBase, IBKR, and ASX brokers.
"""

import asyncio
import json
from datetime import datetime
from typing import Optional

from loguru import logger

from api.utils import _EXECUTOR, _encrypt_creds, _decrypt_creds
from api.state import DATA_DIR


class BrokerBase:
    name: str = "base"
    _reconnect_attempts: int = 0
    _max_reconnect: int = 5
    _last_credentials: dict = {}

    def is_connected(self) -> bool: raise NotImplementedError
    async def connect(self, **kwargs) -> None: raise NotImplementedError
    async def get_account(self) -> dict: raise NotImplementedError
    async def place_order(self, ticker: str, side: str, qty: float, price: Optional[float]) -> dict: raise NotImplementedError
    async def get_positions(self) -> list: raise NotImplementedError
    async def get_history(self) -> list: raise NotImplementedError
    async def close_position(self, ticker: str) -> dict: raise NotImplementedError

    def _store_credentials(self, **kwargs):
        """Store credentials for auto-reconnect (strips None values)."""
        self._last_credentials = {k: v for k, v in kwargs.items() if v is not None}
        self._reconnect_attempts = 0

    async def _heartbeat(self) -> bool:
        """Check broker connectivity. Returns True if healthy."""
        try:
            await asyncio.wait_for(self.get_account(), timeout=10.0)
            self._reconnect_attempts = 0
            return True
        except Exception:
            self._connected = False
            return False

    async def _auto_reconnect(self) -> bool:
        """Attempt to reconnect with exponential backoff."""
        if not self._last_credentials or self._reconnect_attempts >= self._max_reconnect:
            return False
        delay = min(2 ** self._reconnect_attempts * 5, 120)
        logger.info(f"Broker {self.name}: reconnect attempt {self._reconnect_attempts + 1} in {delay}s")
        await asyncio.sleep(delay)
        try:
            await self.connect(**self._last_credentials)
            self._reconnect_attempts = 0
            logger.info(f"Broker {self.name}: reconnected successfully")
            return True
        except Exception as e:
            self._reconnect_attempts += 1
            logger.warning(f"Broker {self.name}: reconnect failed: {e}")
            return False


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
        await asyncio.get_running_loop().run_in_executor(_EXECUTOR, lambda: ib.connect(host, int(port), clientId=int(client_id), timeout=10))
        self._ib = ib
        self._connected = True
        self._store_credentials(host=host, port=port, client_id=client_id)
        logger.info(f"IBKR connected -- {host}:{port}")

    async def get_account(self) -> dict:
        if not self.is_connected(): raise RuntimeError("IBKR not connected")
        summary = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, self._ib.accountSummary)
        vals = {row.tag: row.value for row in summary}
        return {"broker": "ibkr", "account_value": float(vals.get("NetLiquidation", 0)),
                "buying_power": float(vals.get("BuyingPower", 0)), "cash": float(vals.get("TotalCashValue", 0)), "currency": "AUD"}

    async def place_order(self, ticker: str, side: str, qty: float, price: Optional[float] = None) -> dict:
        if not self.is_connected(): raise RuntimeError("IBKR not connected")
        from ib_insync import Stock, MarketOrder, LimitOrder
        contract = Stock(ticker, "SMART", "USD")
        order = LimitOrder(side.upper(), qty, price) if price else MarketOrder(side.upper(), qty)
        trade = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, self._ib.placeOrder, contract, order)
        return {"order_id": trade.order.orderId, "ticker": ticker, "side": side, "qty": qty,
                "price": price, "status": trade.orderStatus.status, "timestamp": datetime.utcnow().isoformat()}

    async def get_positions(self) -> list:
        if not self.is_connected(): raise RuntimeError("IBKR not connected")
        raw = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, self._ib.positions)
        return [{"ticker": p.contract.symbol, "qty": p.position, "avg_cost": round(p.avgCost, 4),
                 "market_val": None, "pnl": None, "side": "LONG" if p.position > 0 else "SHORT"} for p in raw]

    async def get_history(self) -> list:
        if not self.is_connected(): raise RuntimeError("IBKR not connected")
        fills = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, self._ib.fills)
        return [{"ticker": f.contract.symbol, "side": f.execution.side, "qty": f.execution.shares,
                 "price": f.execution.price, "timestamp": str(f.execution.time)} for f in fills]

    async def close_position(self, ticker: str) -> dict:
        positions = await self.get_positions()
        pos = next((p for p in positions if p["ticker"].upper() == ticker.upper()), None)
        if not pos: raise ValueError(f"No open IBKR position in {ticker}")
        side = "SELL" if pos["qty"] > 0 else "BUY"
        return await self.place_order(ticker, side, abs(pos["qty"]), None)


class GenericASXBroker(BrokerBase):
    """Generic ASX broker with HMAC-signed API access."""
    name: str = "generic"
    _BASE: str = ""

    def __init__(self):
        self._api_key: Optional[str] = None
        self._api_secret: Optional[str] = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _headers(self, body: str = "") -> dict:
        import time as _t, hmac as _hmac, hashlib as _hs
        ts = str(int(_t.time() * 1000))
        msg = ts + body
        sig = _hmac.new(self._api_secret.encode(), msg.encode(), _hs.sha256).hexdigest()
        return {"Content-Type": "application/json", "API-KEY": self._api_key,
                "API-SIGN": sig, "API-TIMESTAMP": ts}

    async def connect(self, api_key: str, api_secret: str, **kwargs) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._connected = True
        self._store_credentials(api_key=api_key, api_secret=api_secret)
        logger.info(f"{self.name.upper()} credentials saved (connection validated on first trade)")

    async def get_account(self) -> dict:
        return {"broker": self.name, "account_value": 0, "buying_power": 0,
                "cash": 0, "currency": "AUD", "note": "Connect and trade to populate"}

    async def place_order(self, ticker: str, side: str, qty: float, price: Optional[float] = None) -> dict:
        raise NotImplementedError(f"{self.name.upper()} order routing not yet implemented -- coming soon")

    async def get_positions(self) -> list:
        return []

    async def get_history(self) -> list:
        return []

    async def close_position(self, ticker: str) -> dict:
        raise NotImplementedError(f"{self.name.upper()} close not yet implemented")


class IGBroker(GenericASXBroker):
    """IG Markets — REST API for ASX CFDs and share trading."""
    name = "ig"
    _BASE = "https://api.ig.com/gateway/deal"

class CMCBroker(GenericASXBroker):
    """CMC Markets — REST API for ASX CFDs and spread betting."""
    name = "cmc"
    _BASE = "https://ciapi.cityindex.com/TradingAPI"

class MomooBroker(GenericASXBroker):
    """Moomoo (Futu) — OpenAPI SDK for ASX equities."""
    name = "moomoo"
    _BASE = "https://openapi.moomoo.com/v1"

class SaxoBroker(GenericASXBroker):
    """Saxo Markets — OpenAPI for ASX equities, ETFs, and derivatives."""
    name = "saxo"
    _BASE = "https://gateway.saxobank.com/openapi"

class TigerBroker(GenericASXBroker):
    """Tiger Brokers — Open API for ASX equities."""
    name = "tiger"
    _BASE = "https://openapi.tigerbrokers.com/gateway"

class FinClearBroker(GenericASXBroker):
    """FinClear — Australian wholesale execution via FIX/REST API.
    Provides direct ASX market access for authorised participants."""
    name = "finclear"
    _BASE = "https://api.finclear.com.au/v1"

class OpenMarketsBroker(GenericASXBroker):
    """Open Markets — ASX licensed execution venue with REST/FIX API.
    Specialises in ASX equities execution and clearing."""
    name = "openmarkets"
    _BASE = "https://api.openmarkets.com.au/v1"

class PepperstoneBroker(GenericASXBroker):
    """Pepperstone — cTrader Open API / MT4/MT5 for CFDs, FX, ASX."""
    name = "pepperstone"
    _BASE = "https://api.pepperstone.com/v1"

class MarketechBroker(GenericASXBroker):
    """Marketech — Australian broker with IRESS platform integration."""
    name = "marketech"
    _BASE = "https://api.marketech.com.au/v1"

class OpenTraderBroker(GenericASXBroker):
    """OpenTrader — Low-cost ASX broker powered by FinClear execution."""
    name = "opentrader"
    _BASE = "https://api.opentrader.com.au/v1"

class IRESSBroker(GenericASXBroker):
    """IRESS — Professional trading platform with FIX API for ASX."""
    name = "iress"
    _BASE = "https://api.iress.com/v1"

class CQGBroker(GenericASXBroker):
    """CQG — Professional futures & commodities platform with Web API."""
    name = "cqg"
    _BASE = "https://api.cqg.com/v2"

class FlexTradeBroker(GenericASXBroker):
    """FlexTrade — Institutional EMS/OMS with FIX connectivity."""
    name = "flextrade"
    _BASE = "https://api.flextrade.com/v1"

class TradingViewBroker(GenericASXBroker):
    """TradingView — Webhook-based order routing to connected brokers."""
    name = "tradingview"
    _BASE = "https://webhook.tradingview.com"

class EODHDBroker(GenericASXBroker):
    """EODHD — End-of-day & intraday market data API (data only, no trading)."""
    name = "eodhd"
    _BASE = "https://eodhd.com/api"


# ── Active broker global ────────────────────────────────
ACTIVE_BROKER: Optional[BrokerBase] = None


# ── Broker credential persistence ───────────────────────
_BROKER_CREDS_FILE = DATA_DIR / "broker_credentials.json"


def _load_broker_creds() -> dict:
    if _BROKER_CREDS_FILE.exists():
        try:
            raw = json.loads(_BROKER_CREDS_FILE.read_text())
            return _decrypt_creds(raw)
        except Exception:
            return {}
    return {}


def _save_broker_creds(creds: dict):
    encrypted = _encrypt_creds(creds)
    _BROKER_CREDS_FILE.write_text(json.dumps(encrypted, indent=2))


# ── Broker map for connection routing (IBKR first — most complete) ──
BROKER_MAP = {
    "ibkr": IBKRBroker,
    "ig": IGBroker, "cmc": CMCBroker,
    "saxo": SaxoBroker, "tiger": TigerBroker,
    "moomoo": MomooBroker, "pepperstone": PepperstoneBroker,
    "finclear": FinClearBroker, "openmarkets": OpenMarketsBroker,
    "marketech": MarketechBroker, "opentrader": OpenTraderBroker,
    "iress": IRESSBroker, "cqg": CQGBroker,
    "flextrade": FlexTradeBroker, "tradingview": TradingViewBroker,
    "eodhd": EODHDBroker,
}
