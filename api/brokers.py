"""
Dalios -- Broker Implementations
All broker classes: BrokerBase, IBKR, Alpaca, Binance, CoinSpot, etc.
"""

import asyncio
import json
import aiohttp
from datetime import datetime
from typing import Optional

from loguru import logger

from api.utils import _EXECUTOR, _encrypt_creds, _decrypt_creds
from api.state import DATA_DIR


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
        await asyncio.get_running_loop().run_in_executor(_EXECUTOR, lambda: ib.connect(host, int(port), clientId=int(client_id), timeout=10))
        self._ib = ib
        self._connected = True
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
        await asyncio.get_running_loop().run_in_executor(_EXECUTOR, api.get_account)
        self._api = api
        self._connected = True
        logger.info(f"Alpaca connected -- {base_url}")

    async def get_account(self) -> dict:
        if not self.is_connected(): raise RuntimeError("Alpaca not connected")
        acct = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, self._api.get_account)
        return {"broker": "alpaca", "account_value": float(acct.portfolio_value),
                "buying_power": float(acct.buying_power), "cash": float(acct.cash),
                "currency": "USD", "status": acct.status}

    async def place_order(self, ticker: str, side: str, qty: float, price: Optional[float] = None) -> dict:
        if not self.is_connected(): raise RuntimeError("Alpaca not connected")
        order = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, lambda: self._api.submit_order(
            symbol=ticker, qty=qty, side=side.lower(),
            type="limit" if price else "market",
            time_in_force="gtc",
            limit_price=str(price) if price else None,
        ))
        return {"order_id": str(order.id), "ticker": ticker, "side": side, "qty": qty,
                "price": price, "status": order.status, "timestamp": datetime.utcnow().isoformat()}

    async def get_positions(self) -> list:
        if not self.is_connected(): raise RuntimeError("Alpaca not connected")
        raw = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, self._api.list_positions)
        return [{"ticker": p.symbol, "qty": float(p.qty), "avg_cost": float(p.avg_entry_price),
                 "market_val": float(p.market_value), "pnl": float(p.unrealized_pl),
                 "pnl_pct": round(float(p.unrealized_plpc) * 100, 2), "side": p.side} for p in raw]

    async def get_history(self) -> list:
        if not self.is_connected(): raise RuntimeError("Alpaca not connected")
        raw = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, lambda: self._api.list_orders(status="filled", limit=100))
        return [{"ticker": o.symbol, "side": o.side, "qty": float(o.filled_qty or 0),
                 "price": float(o.filled_avg_price) if o.filled_avg_price else None,
                 "timestamp": o.filled_at.isoformat() if o.filled_at else None} for o in raw]

    async def close_position(self, ticker: str) -> dict:
        if not self.is_connected(): raise RuntimeError("Alpaca not connected")
        order = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, lambda: self._api.close_position(ticker))
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
        await asyncio.get_running_loop().run_in_executor(_EXECUTOR, client.get_account)
        self._client = client
        self._connected = True
        logger.info(f"Binance connected -- {'testnet' if testnet else 'live'}")

    async def get_account(self) -> dict:
        if not self.is_connected(): raise RuntimeError("Binance not connected")
        acct = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, self._client.get_account)
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
        order = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, lambda: self._client.create_order(**params))
        return {"order_id": str(order["orderId"]), "ticker": ticker, "side": side, "qty": qty,
                "price": price, "status": order.get("status"), "timestamp": datetime.utcnow().isoformat()}

    async def get_positions(self) -> list:
        if not self.is_connected(): raise RuntimeError("Binance not connected")
        acct = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, self._client.get_account)
        return [{"ticker": b["asset"], "qty": float(b["free"]) + float(b["locked"]),
                 "avg_cost": None, "market_val": None, "pnl": None, "side": "LONG"}
                for b in acct.get("balances", [])
                if (float(b["free"]) + float(b["locked"])) > 0 and b["asset"] not in ("USDT", "BUSD")]

    async def get_history(self) -> list:
        if not self.is_connected(): raise RuntimeError("Binance not connected")
        orders = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, lambda: self._client.get_all_orders(symbol="BTCUSDT", limit=50))
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
        await asyncio.get_running_loop().run_in_executor(_EXECUTOR, client.get_accounts)
        self._client = client
        self._connected = True
        logger.info("Coinbase Advanced Trade connected")

    async def get_account(self) -> dict:
        if not self.is_connected(): raise RuntimeError("Coinbase not connected")
        accounts = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, self._client.get_accounts)
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
        order = await asyncio.get_running_loop().run_in_executor(
            _EXECUTOR, lambda: self._client.create_order(
                client_order_id=client_order_id, product_id=product_id, side=side.upper(), order_configuration=cfg))
        return {"order_id": getattr(order, "order_id", client_order_id), "ticker": ticker, "side": side,
                "qty": qty, "price": price, "status": "FILLED" if getattr(order, "success", False) else "PENDING",
                "timestamp": datetime.utcnow().isoformat()}

    async def get_positions(self) -> list:
        if not self.is_connected(): raise RuntimeError("Coinbase not connected")
        accounts = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, self._client.get_accounts)
        acct_list = accounts.accounts if hasattr(accounts, "accounts") else []
        return [{"ticker": getattr(a, "currency", ""), "qty": float(getattr(a, "available_balance", type("", (), {"value": "0"})).value),
                 "avg_cost": None, "market_val": None, "pnl": None, "side": "LONG"}
                for a in acct_list if getattr(a, "currency", "") not in ("USD", "USDC", "USDT")
                and float(getattr(a, "available_balance", type("", (), {"value": "0"})).value) > 0]

    async def get_history(self) -> list:
        if not self.is_connected(): raise RuntimeError("Coinbase not connected")
        orders = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, lambda: self._client.list_orders(order_status=["FILLED"], limit=50))
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
    """CoinSpot Australian crypto exchange -- REST API v2."""
    name = "coinspot"
    _BASE = "https://www.coinspot.com.au/api/v2"

    def __init__(self):
        self._api_key: Optional[str] = None
        self._api_secret: Optional[str] = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _sign_request(self, data: dict):
        """Sign a CoinSpot API request."""
        import time as _t, hmac as _hmac, hashlib as _hs

        nonce = int(_t.time() * 1000)
        data["nonce"] = nonce

        # Force all float values to fixed-point strings to avoid scientific notation
        cleaned = {}
        for k, v in data.items():
            if isinstance(v, float):
                cleaned[k] = f"{v:.8f}".rstrip("0").rstrip(".")
            else:
                cleaned[k] = v

        body = json.dumps(cleaned, separators=(",", ":"))
        sig = _hmac.new(self._api_secret.encode("utf-8"), body.encode("utf-8"), _hs.sha512).hexdigest()
        return body, sig

    async def _post(self, endpoint: str, payload: dict) -> dict:
        body, sig = self._sign_request(payload)
        headers = {"Content-Type": "application/json",
                   "key": self._api_key,
                   "sign": sig}
        logger.debug(f"CoinSpot POST {endpoint} body=[REDACTED]")
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(f"{self._BASE}{endpoint}", data=body, headers=headers) as resp:
                result = await resp.json(content_type=None)
                if result.get("status") == "error":
                    logger.warning(f"CoinSpot error on {endpoint}: {result.get('message','unknown')} | body=[REDACTED]")
                    raise RuntimeError(f"CoinSpot error: {result.get('message','unknown')}")
                return result

    async def connect(self, api_key: str, api_secret: str, **kwargs) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        # Verify credentials with a balances fetch
        await self.get_account()
        self._connected = True
        logger.info("CoinSpot connected")

    async def get_account(self) -> dict:
        result = await self._post("/ro/my/balances", {})
        bals = result.get("balances", [])
        total = sum(float(v.get("audbalance", 0))
                    for b in bals if isinstance(b, dict)
                    for v in b.values() if isinstance(v, dict))
        return {"broker": "coinspot", "account_value": round(total, 2),
                "buying_power": round(total, 2), "cash": round(total, 2), "currency": "AUD"}

    async def place_order(self, ticker: str, side: str, qty: float,
                          price: Optional[float] = None) -> dict:
        coin = ticker.replace("-AUD", "").replace("-USD", "").upper()
        endpoint = ("/my/buy/now"
                    if side.upper() in ("BUY", "LONG")
                    else "/my/sell/now")
        result = await self._post(endpoint, {"cointype": coin, "amount": qty, "amounttype": "coin"})
        return {"order_id": result.get("id", f"cs_{int(datetime.utcnow().timestamp())}"),
                "ticker": ticker, "side": side, "qty": qty, "price": price,
                "timestamp": datetime.utcnow().isoformat()}

    async def get_positions(self) -> list:
        result = await self._post("/ro/my/balances", {})
        out = []
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
        result = await self._post("/ro/my/orders/completed", {})
        rows = []
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
        coin = ticker.replace("-AUD", "").replace("-USD", "").upper()
        pos = next((p for p in positions if coin in p["ticker"].upper()), None)
        if not pos:
            raise ValueError(f"No CoinSpot balance for {ticker}")
        return await self.place_order(ticker, "SELL", pos["qty"])


class GenericCryptoBroker(BrokerBase):
    """Generic HMAC-signed crypto exchange broker."""
    name: str = "generic"
    _BASE: str = ""

    def __init__(self):
        self._api_key: Optional[str] = None
        self._api_secret: Optional[str] = None
        self._passphrase: Optional[str] = None
        self._connected = False

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
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase or None
        self._connected = True
        logger.info(f"{self.name.upper()} credentials saved (connection validated on first trade)")

    async def get_account(self) -> dict:
        return {"broker": self.name, "account_value": 0, "buying_power": 0,
                "cash": 0, "currency": "USD", "note": "Connect and trade to populate"}

    async def place_order(self, ticker: str, side: str, qty: float, price: Optional[float] = None) -> dict:
        raise NotImplementedError(f"{self.name.upper()} order routing not yet implemented -- coming soon")

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

class RobinhoodBroker(GenericCryptoBroker):
    name = "robinhood"
    _BASE = "https://trading.robinhood.com/api/v1"

class WebullBroker(GenericCryptoBroker):
    name = "webull"
    _BASE = "https://userapi.webull.com/api"


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


# ── Broker map for connection routing ───────────────────
BROKER_MAP = {
    "ibkr": IBKRBroker, "alpaca": AlpacaBroker, "binance": BinanceBroker,
    "coinbase": CoinbaseBroker, "coinspot": CoinSpotBroker,
    "kraken": KrakenBroker, "bybit": BybitBroker, "okx": OKXBroker,
    "kucoin": KuCoinBroker, "bitget": BitgetBroker,
    "independentreserve": IndependentReserveBroker, "stake": StakeBroker,
    "ig": IGBroker, "cmc": CMCBroker, "schwab": SchwabBroker,
    "moomoo": MomooBroker,
    "robinhood": RobinhoodBroker, "webull": WebullBroker,
}
