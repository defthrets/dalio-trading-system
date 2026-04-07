"""
Dalios -- Global State
SystemState, circuit breaker, watchlist, trading mode, persistence paths.
"""

import asyncio
import json
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

# ── Path setup ──────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Settings / DB availability ──────────────────────────
try:
    from config.settings import get_settings
    from data.storage.models import init_db, get_session, Trade, EquitySnapshot, PaperPosition, RealEquitySnapshot
    SETTINGS_AVAILABLE = True
except ImportError:
    SETTINGS_AVAILABLE = False


# ── Data directories and files ──────────────────────────
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
(DATA_DIR / "storage").mkdir(exist_ok=True)  # SQLite DB lives here

PAPER_STATE_FILE   = DATA_DIR / "paper_portfolio.json"
REAL_EQUITY_FILE   = DATA_DIR / "real_equity.json"
WATCHLIST_FILE     = DATA_DIR / "watchlist.json"
PAPER_CONFIG_FILE  = DATA_DIR / "paper_config.json"
AGENT_CONFIG_FILE  = DATA_DIR / "agent_config.json"


# ── SystemState class ──────────────────────────────────
class SystemState:
    def __init__(self):
        self.agent = None
        self.booted = False
        self.mode = "PAPER"
        self.paused = False
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


# ── Circuit Breaker ──────────────────────────────────────
try:
    from trading.circuit_breaker import CircuitBreaker
    CIRCUIT_BREAKER = CircuitBreaker(starting_equity=STATE.initial_equity)
    logger.info("CircuitBreaker initialised")
except Exception as _cb_err:
    CIRCUIT_BREAKER = None
    logger.warning(f"CircuitBreaker not available: {_cb_err}")

# ── Notification Manager ─────────────────────────────────
try:
    from notifications.notifier import NotificationManager
    NOTIFIER = NotificationManager()
    logger.info("NotificationManager initialised")
except Exception as _nm_err:
    NOTIFIER = None
    logger.warning(f"NotificationManager not available: {_nm_err}")


# ── Watchlist ────────────────────────────────────────────
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


# ── Trading mode persistence ────────────────────────────
_MODE_FILE = ROOT / "data" / "trading_mode.json"


def _load_trading_mode() -> str:
    """Load persisted trading mode, default to 'paper'."""
    try:
        if _MODE_FILE.exists():
            return json.loads(_MODE_FILE.read_text()).get("mode", "paper")
    except Exception:
        pass
    return "paper"


def _save_trading_mode(mode: str):
    """Persist trading mode to disk."""
    try:
        _MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MODE_FILE.write_text(json.dumps({"mode": mode}))
    except Exception:
        pass


TRADING_MODE: str = _load_trading_mode()


# ── Async locks for global mutable state ────────────────
_PAPER_LOCK = asyncio.Lock()
_MODE_LOCK = asyncio.Lock()
_WATCHLIST_LOCK = asyncio.Lock()


# ── Real equity curve ───────────────────────────────────
def _load_real_equity() -> list:
    """Load live equity from DB first, fall back to JSON file."""
    db_curve = _db_get_real_equity_curve(2000)
    if db_curve:
        return db_curve
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


REAL_EQUITY_CURVE: list = []


# ── Database persistence helpers (dual write) ───────────

def _db_save_trade(trade_dict: dict) -> None:
    """Insert a single closed-trade record into the DB."""
    if not SETTINGS_AVAILABLE:
        return
    try:
        session = get_session()
        ts = trade_dict.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                ts = datetime.utcnow()
        record = Trade(
            ticker=trade_dict.get("ticker", ""),
            side=trade_dict.get("side", "SELL"),
            qty=float(trade_dict.get("qty", 0)),
            price=float(trade_dict.get("exit_price", trade_dict.get("price", 0))),
            fees=float(trade_dict.get("fees", 0)),
            pnl=trade_dict.get("pnl"),
            pnl_pct=trade_dict.get("pnl_pct"),
            entry_price=trade_dict.get("entry_price"),
            exit_price=trade_dict.get("exit_price"),
            timestamp=ts,
        )
        session.add(record)
        session.commit()
        session.close()
    except Exception as exc:
        logger.warning(f"DB: failed to save trade: {exc}")


def _db_save_equity_snapshot(value: float) -> None:
    """Insert a single equity data-point into the DB."""
    if not SETTINGS_AVAILABLE:
        return
    try:
        session = get_session()
        record = EquitySnapshot(value=value, timestamp=datetime.utcnow())
        session.add(record)
        session.commit()
        session.close()
    except Exception as exc:
        logger.warning(f"DB: failed to save equity snapshot: {exc}")


def _db_get_trades(limit: int = 100) -> list[dict]:
    """Query recent closed trades from DB, newest first."""
    if not SETTINGS_AVAILABLE:
        return []
    try:
        session = get_session()
        rows = (
            session.query(Trade)
            .order_by(Trade.timestamp.desc())
            .limit(limit)
            .all()
        )
        result = []
        for r in rows:
            result.append({
                "id": r.id,
                "ticker": r.ticker,
                "side": r.side,
                "qty": r.qty,
                "price": r.price,
                "fees": r.fees,
                "pnl": r.pnl,
                "pnl_pct": r.pnl_pct,
                "entry_price": r.entry_price,
                "exit_price": r.exit_price,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            })
        session.close()
        return result
    except Exception as exc:
        logger.warning(f"DB: failed to get trades: {exc}")
        return []


def _db_get_equity_curve(limit: int = 2000) -> list[dict]:
    """Query equity history from DB, oldest first (for charting)."""
    if not SETTINGS_AVAILABLE:
        return []
    try:
        session = get_session()
        rows = (
            session.query(EquitySnapshot)
            .order_by(EquitySnapshot.timestamp.desc())
            .limit(limit)
            .all()
        )
        # Reverse so oldest is first (for chart rendering)
        result = [
            {"t": r.timestamp.isoformat() if r.timestamp else None, "v": r.value}
            for r in reversed(rows)
        ]
        session.close()
        return result
    except Exception as exc:
        logger.warning(f"DB: failed to get equity curve: {exc}")
        return []


def _db_save_real_equity_snapshot(value: float) -> None:
    """Insert a single live equity data-point into the DB."""
    if not SETTINGS_AVAILABLE:
        return
    try:
        session = get_session()
        record = RealEquitySnapshot(value=value, timestamp=datetime.utcnow())
        session.add(record)
        session.commit()
        session.close()
    except Exception as exc:
        logger.warning(f"DB: failed to save real equity snapshot: {exc}")


def _db_get_real_equity_curve(limit: int = 2000) -> list[dict]:
    """Query live equity history from DB, oldest first."""
    if not SETTINGS_AVAILABLE:
        return []
    try:
        session = get_session()
        rows = (
            session.query(RealEquitySnapshot)
            .order_by(RealEquitySnapshot.timestamp.desc())
            .limit(limit)
            .all()
        )
        result = [
            {"t": r.timestamp.isoformat() if r.timestamp else None, "v": r.value}
            for r in reversed(rows)
        ]
        session.close()
        return result
    except Exception as exc:
        logger.warning(f"DB: failed to get real equity curve: {exc}")
        return []


def _db_sync_positions(positions: dict) -> None:
    """Replace all paper_positions rows with current in-memory state."""
    if not SETTINGS_AVAILABLE:
        return
    try:
        session = get_session()
        session.query(PaperPosition).delete()
        for ticker, pos in positions.items():
            entry_time = pos.get("entry_time")
            if isinstance(entry_time, str):
                try:
                    entry_time = datetime.fromisoformat(entry_time)
                except (ValueError, TypeError):
                    entry_time = datetime.utcnow()
            record = PaperPosition(
                ticker=ticker,
                side=pos.get("side", "LONG"),
                qty=float(pos.get("qty", 0)),
                entry_price=float(pos.get("entry_price", 0)),
                stop_loss=pos.get("stop_loss"),
                take_profit=pos.get("take_profit"),
                entry_time=entry_time,
            )
            session.add(record)
        session.commit()
        session.close()
    except Exception as exc:
        logger.warning(f"DB: failed to sync positions: {exc}")
