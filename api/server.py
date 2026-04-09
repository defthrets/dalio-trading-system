"""
Dalios -- Automated Trading Framework
FastAPI Backend Server (Thin Orchestrator)

Imports all engines from submodules:
  api.utils       -- caching, encryption, indicators, yfinance helpers
  api.state       -- global state, persistence, DB helpers, circuit breaker
  api.scanners    -- ticker universes, market scanning, live prices
  api.portfolio   -- paper portfolio, position sizing, fees
  api.signals     -- signal engine, opportunities, sentiment, correlation, analysis
  api.brokers     -- broker classes, credentials
  api.agent       -- autonomous agent loop, SL/TP monitoring
  api.websocket   -- WebSocket manager, CLI command parser

Exposes all trading system engines via REST + WebSocket endpoints.
Serves the military/hacker UI from /ui/index.html.
"""

import asyncio
import json
import os
import random
import time
from datetime import datetime
from typing import Optional

import numpy as np
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

# ── Module imports ─────────────────────────────────────────
from api.utils import (
    _cache_get, _cache_set, _cache_get_with_age, _get_prices, _EXECUTOR, _fmt_vol,
    YF_AVAILABLE, _normalize_ticker,
    RateLimiter,
)
from api.state import (
    ROOT, STATE, SETTINGS_AVAILABLE,
    CIRCUIT_BREAKER, NOTIFIER,
    WATCHLIST, _save_watchlist, _WATCHLIST_LOCK,
    _save_trading_mode, _MODE_LOCK,
    REAL_EQUITY_CURVE, _load_real_equity, _save_real_equity,
    _db_save_trade, _db_save_equity_snapshot, _db_get_trades, _db_get_equity_curve,
    _db_save_real_equity_snapshot, _db_get_real_equity_curve,
    _PAPER_LOCK,
)
from api.scanners import (
    ASX_TICKERS, COMMODITY_TICKERS, ALL_TICKERS, CORR_TICKERS,
    _ASSET_META, _scanner_cache, _CACHE_TTL,
    _live_price, _prices_for_positions,
    _scan_yfinance, _MARKET_DEMO,
)
from api.portfolio import (
    PAPER, PAPER_STARTING_CASH, _save_paper_state, _load_paper_state,
    _save_paper_config, _calculate_position_size, _RISK_MAX_POS_SIZE_PCT,
)
from api.signals import (
    QUADRANT_META, ASSET_CLASS_MAP, QUADRANT_PLAYBOOK,
    _gen_signals, _gen_opportunities, _gen_quadrant_data,
    _gen_sentiment_data, _gen_correlation_matrix_demo, _real_correlation_matrix,
    _gen_portfolio_health, _gen_backtest_results, dalio_analyse_trade,
)
from api.brokers import (
    BrokerBase, _load_broker_creds, _save_broker_creds, BROKER_MAP,
)
import api.brokers as _brokers_mod_ref

def _get_broker():
    """Always read _get_broker() from the module to get the current value."""
    return _brokers_mod_ref.ACTIVE_BROKER
from api.agent import (
    AGENT_CONFIG, _save_agent_config,
    _autonomous_agent_loop, _sl_tp_monitor_loop,
    _agent_last_cycle_time, _agent_next_cycle_time,
)
from api.websocket import WS_MANAGER, _run_cmd
from api.auth import (
    AUTH_ENABLED, auth_middleware,
    register_user, login_user, get_current_user,
)
import api.state as _state_ref  # module ref for mutable TRADING_MODE

def _current_mode() -> str:
    """Read current trading mode from the state module (avoids stale import binding)."""
    return _state_ref.TRADING_MODE or "paper"

# ── Settings / DB init ─────────────────────────────────────
if SETTINGS_AVAILABLE:
    from config.settings import get_settings
    from data.storage.models import init_db

# yfinance import (for chart data route)
if YF_AVAILABLE:
    import yfinance as yf


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
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_rate_limiter = RateLimiter()




# ── JWT Auth middleware (disabled by default, enable via DALIOS_AUTH_ENABLED=true) ──
@app.middleware("http")
async def _auth_mw(request: Request, call_next):
    return await auth_middleware(request, call_next)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Enforce per-IP rate limits. Static files and WebSocket upgrades are exempt."""
    path = request.url.path
    if path.startswith("/static") or path.startswith("/ws"):
        return await call_next(request)
    ip = request.client.host if request.client else "unknown"
    if not _rate_limiter.is_allowed(ip, path):
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Please try again later."},
        )
    return await call_next(request)


# ── Optional API key authentication ─────────────────────
_DALIOS_API_KEY = os.environ.get("DALIOS_API_KEY")


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """If DALIOS_API_KEY env var is set, require X-API-Key header on /api/ routes."""
    if _DALIOS_API_KEY:
        path = request.url.path
        if path.startswith("/api/"):
            provided = request.headers.get("X-API-Key")
            if provided != _DALIOS_API_KEY:
                return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})
    return await call_next(request)


# ── Static files & UI ──────────────────────────────────────
UI_DIR = ROOT / "ui"
STATIC_DIR = UI_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─────────────────────────────────────────────
# Startup / Shutdown
# ─────────────────────────────────────────────

@app.on_event("startup")
async def _on_startup():
    global REAL_EQUITY_CURVE

    # Initialise database
    if SETTINGS_AVAILABLE:
        try:
            init_db()
            logger.info("Database initialised successfully")
        except Exception as exc:
            logger.warning(f"Database init skipped: {exc}")

    _load_paper_state()

    import api.state as _state_mod
    _state_mod.REAL_EQUITY_CURVE = _load_real_equity()

    # Launch SL/TP monitoring background task
    asyncio.get_event_loop().create_task(_sl_tp_monitor_loop())
    logger.info("SL/TP monitor started (30s interval)")

    # Launch autonomous agent loop background task
    asyncio.get_event_loop().create_task(_autonomous_agent_loop())
    auto_status = "ENABLED" if AGENT_CONFIG.get("enabled", False) else "DISABLED"
    logger.info(f"Autonomous agent loop started ({auto_status}, interval {AGENT_CONFIG.get('interval_seconds', 300)}s)")

    # Fetch full ASX universe (1,900+ companies) in background
    from api.scanners import _fetch_asx_listed_companies
    asyncio.get_event_loop().create_task(_fetch_asx_listed_companies())

    # Auto-reconnect last active broker from saved credentials
    await _auto_reconnect_saved_broker()

    logger.info(f"Startup complete -- trading mode: {_current_mode()}")


async def _auto_reconnect_saved_broker():
    """Try to reconnect the last active broker using saved credentials."""
    import api.brokers as _brokers_mod
    try:
        creds = _load_broker_creds()
        last_active = creds.get("_last_active")
        if not last_active or last_active not in BROKER_MAP:
            return
        broker_creds = creds.get(last_active)
        if not broker_creds:
            return
        logger.info(f"Auto-reconnecting to {last_active}...")
        broker = BROKER_MAP[last_active]()
        await broker.connect(**broker_creds)
        _brokers_mod.ACTIVE_BROKER = broker
        STATE.add_alert("BROKER", f"{last_active.upper()} auto-reconnected", "INFO")
        _ensure_broker_heartbeat()
        logger.info(f"Auto-reconnected to {last_active} successfully")
    except Exception as exc:
        logger.warning(f"Auto-reconnect failed for {creds.get('_last_active', '?')}: {exc}")


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
# Routes -- Health Check (public, no auth)
# ─────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    """Lightweight health check for Docker/load balancers."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ─────────────────────────────────────────────
# Routes -- Authentication
# ─────────────────────────────────────────────

@app.post("/api/auth/register")
async def auth_register(request: Request):
    """Register a new user account (when auth is enabled)."""
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or len(password) < 8:
        raise HTTPException(400, "Username required, password must be 8+ characters")
    result = register_user(username, password)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/auth/login")
async def auth_login(request: Request):
    """Authenticate and receive a JWT token."""
    body = await request.json()
    result = login_user(body.get("username", ""), body.get("password", ""))
    if "error" in result:
        raise HTTPException(401, result["error"])
    return result


@app.get("/api/auth/me")
async def auth_me(request: Request):
    """Get current authenticated user info."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {"username": user.get("username"), "admin": user.get("admin", False)}


# ─────────────────────────────────────────────
# Routes -- System Status
# ─────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    mode = _current_mode().upper()
    return {
        "status": "PAUSED" if getattr(STATE, 'paused', False) else "OPERATIONAL",
        "mode": mode,
        "agent_booted": STATE.booted,
        "cycle_count": STATE.cycle_count,
        "uptime_seconds": STATE.uptime_seconds(),
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "paused": getattr(STATE, 'paused', False),
    }


@app.post("/api/system/pause")
async def toggle_pause(body: dict):
    STATE.paused = body.get("paused", False)
    return {"paused": STATE.paused, "status": "PAUSED" if STATE.paused else "OPERATIONAL"}


# ─────────────────────────────────────────────
# Routes -- Portfolio Health & Equity
# ─────────────────────────────────────────────

@app.get("/api/portfolio/health")
async def portfolio_health():
    if _current_mode() == "live":
        if _get_broker() and _get_broker().is_connected():
            try:
                data = await _gen_live_portfolio_health()
            except Exception:
                data = _gen_live_placeholder()
        else:
            data = _gen_live_placeholder()
    else:
        data = _gen_portfolio_health()
    STATE.last_health = data
    STATE.equity_history.append({"t": datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "v": data["equity"]})
    STATE.equity_history = STATE.equity_history[-500:]
    if data.get("source") == "live":
        import api.state as _state_mod
        _state_mod.REAL_EQUITY_CURVE.append({"t": datetime.utcnow().isoformat(), "v": data["equity"]})
        _state_mod.REAL_EQUITY_CURVE = _state_mod.REAL_EQUITY_CURVE[-2000:]
        _db_save_real_equity_snapshot(data["equity"])
    return data


async def _gen_live_portfolio_health() -> dict:
    """Portfolio health from connected broker."""
    acct = await _get_broker().get_account()
    positions = await _get_broker().get_positions()
    equity = float(acct.get("account_value") or acct.get("equity") or 0)
    cash = float(acct.get("cash") or acct.get("buying_power") or 0)
    initial = float(acct.get("initial_equity") or equity)
    daily_pnl = float(acct.get("daily_pnl") or 0)
    open_count = len(positions) if positions else 0
    roi = round((equity / initial - 1) * 100, 2) if initial > 0 else 0.0
    positions_list = [
        {"ticker": p.get("symbol", p.get("ticker", "?")),
         "side": p.get("side", "LONG"),
         "size_pct": round(abs(float(p.get("market_value", 0))) / max(equity, 1) * 100, 1),
         "unrealised_pnl_pct": round(float(p.get("unrealized_plpc", p.get("unrealised_pnl_pct", 0))) * 100, 2)}
        for p in (positions or [])
    ]
    # Drawdown from live equity curve
    import api.state as _state_mod
    drawdown_pct = 0.0
    peak_equity = equity
    if _state_mod.REAL_EQUITY_CURVE:
        all_vals = [pt["v"] for pt in _state_mod.REAL_EQUITY_CURVE if pt.get("v", 0) > 0]
        if all_vals:
            peak_equity = max(all_vals)
            if peak_equity > 0 and equity < peak_equity:
                drawdown_pct = round((peak_equity - equity) / peak_equity * 100, 2)
    # Circuit breaker
    cb_active = drawdown_pct > 9.5
    if CIRCUIT_BREAKER is not None and CIRCUIT_BREAKER._trading_halted:
        cb_active = True
    # Sharpe from live equity history
    sharpe = 0.0
    if len(_state_mod.REAL_EQUITY_CURVE) >= 10:
        try:
            eq_arr = np.array([e["v"] for e in _state_mod.REAL_EQUITY_CURVE], dtype=float)
            rets = np.diff(eq_arr) / eq_arr[:-1]
            if rets.std() > 0:
                sharpe = round(float((rets.mean() / rets.std()) * (252 ** 0.5)), 2)
        except Exception:
            pass
    return {
        "timestamp": datetime.utcnow().isoformat(), "equity": round(equity, 2),
        "initial_equity": round(initial, 2), "cash": round(cash, 2),
        "total_return_pct": roi,
        "daily_pnl": round(daily_pnl, 2),
        "daily_pnl_pct": round(daily_pnl / equity * 100, 3) if equity else 0.0,
        "drawdown_pct": drawdown_pct, "open_positions": open_count,
        "dalio_diversification_met": open_count >= 3,
        "selected_portfolio_size": open_count,
        "circuit_breaker_active": cb_active,
        "daily_limit_pct": 2.0, "max_drawdown_pct": 10.0,
        "sharpe_ratio": sharpe, "positions": positions_list,
        "peak_equity": round(peak_equity, 2),
        "source": "live",
    }


def _gen_live_placeholder() -> dict:
    """Return zeroed live-mode stats when no broker is connected."""
    return {
        "timestamp": datetime.utcnow().isoformat(), "equity": 0,
        "initial_equity": 0, "cash": 0,
        "total_return_pct": 0, "daily_pnl": 0, "daily_pnl_pct": 0,
        "drawdown_pct": 0, "open_positions": 0,
        "dalio_diversification_met": False,
        "selected_portfolio_size": 0,
        "circuit_breaker_active": False,
        "daily_limit_pct": 2.0, "max_drawdown_pct": 10.0,
        "sharpe_ratio": 0, "positions": [],
        "peak_equity": 0,
        "source": "live", "broker_connected": False,
    }


@app.get("/api/portfolio/equity_history")
async def equity_history():
    db_curve = _db_get_equity_curve(limit=2000)
    if db_curve:
        return {"history": db_curve, "source": "db"}
    return {"history": STATE.equity_history, "source": "json"}


# ─────────────────────────────────────────────
# Routes -- Signals, Quadrant, Sentiment, Correlation
# ─────────────────────────────────────────────

@app.get("/api/signals")
async def get_signals():
    # Check cache freshness before generating
    cached_val, cache_age = _cache_get_with_age("signals_17")
    all_sigs = await _gen_signals(17)
    return {
        "signals": all_sigs[:12],
        "new_opportunities": all_sigs[12:17] or all_sigs[:5],
        "timestamp": datetime.utcnow().isoformat(),
        "cached": cached_val is not None,
        "cache_age": cache_age,
    }


@app.get("/api/quadrant")
async def get_quadrant():
    data = _gen_quadrant_data()
    STATE.last_quadrant = data
    return data


_SENTIMENT_CACHE: dict = {}
_SENTIMENT_TTL = 300


@app.get("/api/sentiment")
async def get_sentiment():
    cached = _SENTIMENT_CACHE.get("data")
    if cached and (time.time() - _SENTIMENT_CACHE.get("ts", 0)) < _SENTIMENT_TTL:
        return cached
    data = await _gen_sentiment_data()
    STATE.last_sentiment = data
    _SENTIMENT_CACHE["data"] = data
    _SENTIMENT_CACHE["ts"] = time.time()
    return data


@app.get("/api/correlation")
async def get_correlation():
    # Pass mode-appropriate positions to correlation engine
    mode = _current_mode()
    if mode == "live" and _get_broker() and _get_broker().is_connected():
        try:
            live_positions = await _get_broker().get_positions()
            live_tickers = [p.get("symbol", p.get("ticker", "")) for p in (live_positions or [])]
            real = await _real_correlation_matrix(override_tickers=live_tickers)
            return real if real else _gen_correlation_matrix_demo(override_tickers=live_tickers)
        except Exception:
            pass
    real = await _real_correlation_matrix()
    return real if real else _gen_correlation_matrix_demo()


# ─────────────────────────────────────────────
# Routes -- Market Summary & Scanner
# ─────────────────────────────────────────────

@app.get("/api/market_summary")
async def market_summary():
    """Live prices for the market ticker strip -- falls back to demo when offline."""
    key = "market_summary"
    cached = _cache_get(key)
    if cached:
        return cached

    watchlist = [
        # ASX
        ("^AXJO",    "ASX 200",        "index"),
        ("CBA.AX",   "CommBank",       "asx"),
        ("BHP.AX",   "BHP Group",      "asx"),
        ("CSL.AX",   "CSL Ltd",        "asx"),
        ("NAB.AX",   "NAB",            "asx"),
        ("WBC.AX",   "Westpac",        "asx"),
        ("ANZ.AX",   "ANZ Bank",       "asx"),
        ("FMG.AX",   "Fortescue",      "asx"),
        ("RIO.AX",   "Rio Tinto",      "asx"),
        ("WDS.AX",   "Woodside",       "asx"),
        ("WES.AX",   "Wesfarmers",     "asx"),
        ("MQG.AX",   "Macquarie",      "asx"),
        ("TLS.AX",   "Telstra",        "asx"),
        # Indices
        ("^GSPC",    "S&P 500",        "index"),
        ("^DJI",     "Dow Jones",      "index"),
        ("^IXIC",    "Nasdaq",         "index"),
        ("^N225",    "Nikkei 225",     "index"),
        ("^FTSE",    "FTSE 100",       "index"),
        ("^VIX",     "VIX Fear",       "index"),
        # FX
        ("AUD=X",    "AUD/USD",        "fx"),
        ("EURUSD=X", "EUR/USD",        "fx"),
        # Commodities
        ("GC=F",     "Gold Futures",   "commodity"),
        ("SI=F",     "Silver Futures", "commodity"),
        ("CL=F",     "Crude Oil WTI",  "commodity"),
        ("NG=F",     "Natural Gas",    "commodity"),
        ("GLD",      "Gold ETF",       "commodity"),
        ("SLV",      "Silver ETF",     "commodity"),
        ("USO",      "Oil ETF",        "commodity"),
        ("PPLT",     "Platinum ETF",   "commodity"),
        ("COPX",     "Copper Miners",  "commodity"),
        ("URA",      "Uranium ETF",    "commodity"),
        ("WEAT",     "Wheat ETF",      "commodity"),
        ("DBA",      "Agriculture ETF","commodity"),
    ]
    tickers = [t for t, _, _ in watchlist]
    yf_data = await _get_prices(tickers, "5d")

    demo_map = {row[0]: (row[3], row[4]) for row in _MARKET_DEMO}

    result = []
    for ticker, name, category in watchlist:
        price = chg_pct = None
        source = "DEMO"

        if isinstance(yf_data, dict):
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


@app.get("/api/suggest")
async def suggest_trades(n: int = 8):
    """Return top-N trade opportunities."""
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
            for mkt in ("asx", "commodities")
        },
    }


@app.get("/api/recommendations")
async def get_recommendations(n: int = 6):
    """Top N trade recommendations with full Dalio AI analysis."""
    n = min(max(1, n), 12)
    opps = await _gen_opportunities(n * 4)
    qdata = STATE.last_quadrant or _gen_quadrant_data()
    quadrant = qdata.get("quadrant", "rising_growth")
    sigs = await _gen_signals(12)

    recs = []
    for opp in opps:
        if opp["score"] < 25:
            continue
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
        if len(recs) >= n:
            break

    return {
        "recommendations": recs,
        "count": len(recs),
        "quadrant": quadrant,
        "regime_label": qdata.get("label", ""),
    }


@app.get("/api/chart/{ticker}")
async def chart_data(ticker: str, period: str = "6mo", interval: str = "1d"):
    """OHLCV candlestick data for a single ticker via yfinance."""
    if not YF_AVAILABLE:
        raise HTTPException(503, "yfinance not available")

    allowed_periods = {"1mo", "3mo", "6mo", "1y", "2y", "5y", "max"}
    allowed_intervals = {"1m", "5m", "15m", "1h", "1d", "1wk", "1mo"}
    if period not in allowed_periods:
        period = "6mo"
    if interval not in allowed_intervals:
        interval = "1d"

    cache_key = f"chart_{ticker}_{period}_{interval}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()

    def _fetch():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period=period, interval=interval, auto_adjust=True)
            if hist is None or hist.empty:
                return None
            hist = hist.dropna(subset=["Close"])
            candles = []
            for idx, row in hist.iterrows():
                ts = idx.isoformat() if hasattr(idx, 'isoformat') else str(idx)
                candles.append({
                    "t": ts,
                    "o": round(float(row["Open"]), 4),
                    "h": round(float(row["High"]), 4),
                    "l": round(float(row["Low"]), 4),
                    "c": round(float(row["Close"]), 4),
                    "v": int(row.get("Volume", 0)),
                })
            closes = [c["c"] for c in candles]
            sma20 = []
            sma50 = []
            for i in range(len(closes)):
                sma20.append(round(sum(closes[max(0,i-19):i+1]) / min(i+1, 20), 4) if i >= 19 else None)
                sma50.append(round(sum(closes[max(0,i-49):i+1]) / min(i+1, 50), 4) if i >= 49 else None)

            rsi_vals = [None] * len(closes)
            if len(closes) >= 15:
                gains, losses = [], []
                for j in range(1, len(closes)):
                    d = closes[j] - closes[j-1]
                    gains.append(max(d, 0))
                    losses.append(max(-d, 0))
                avg_gain = sum(gains[:14]) / 14
                avg_loss = sum(losses[:14]) / 14
                for j in range(14, len(closes)):
                    if j > 14:
                        avg_gain = (avg_gain * 13 + gains[j-1]) / 14
                        avg_loss = (avg_loss * 13 + losses[j-1]) / 14
                    rs = avg_gain / avg_loss if avg_loss > 0 else 100
                    rsi_vals[j] = round(100 - 100 / (1 + rs), 1)

            prediction = {"dates": [], "mid": [], "upper": [], "lower": []}
            if len(closes) >= 10:
                rets = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
                mean_ret = sum(rets) / len(rets)
                std_ret = (sum((r - mean_ret)**2 for r in rets) / (len(rets) - 1)) ** 0.5 if len(rets) > 1 else 0
                last_price = closes[-1]
                pred = last_price
                for i in range(1, 31):
                    pred *= (1 + mean_ret)
                    spread = last_price * std_ret * 1.96 * (i ** 0.5)
                    prediction["dates"].append(f"+{i}")
                    prediction["mid"].append(round(pred, 4))
                    prediction["upper"].append(round(pred + spread, 4))
                    prediction["lower"].append(round(pred - spread, 4))

            info = {}
            try:
                ti = t.info
                info = {"name": ti.get("shortName", ticker), "sector": ti.get("sector", ""),
                        "marketCap": ti.get("marketCap"), "currency": ti.get("currency", "USD")}
            except Exception:
                info = {"name": ticker, "sector": "", "marketCap": None, "currency": "USD"}

            return {
                "ticker": ticker, "period": period, "interval": interval,
                "candles": candles, "sma20": sma20, "sma50": sma50, "rsi": rsi_vals,
                "prediction": prediction, "info": info, "count": len(candles),
            }
        except Exception as exc:
            logger.warning(f"Chart data error for {ticker}: {exc}")
            return None

    result = await asyncio.wait_for(loop.run_in_executor(None, _fetch), timeout=15)
    if result is None:
        raise HTTPException(404, f"No chart data for {ticker}")
    _cache_set(cache_key, result)
    return result


@app.get("/api/markets/{market}")
async def market_scanner(market: str, full: bool = False):
    """Scan a market: asx | commodities. Uses cache (90s TTL).
    Pass ?full=true to scan the entire ASX universe (~1,900 tickers)."""
    market = market.lower()
    from api.scanners import get_asx_universe
    cache_key = f"{market}_full" if (market == "asx" and full) else market
    ticker_map = {
        "asx":         get_asx_universe() if full else ASX_TICKERS,
        "commodities": COMMODITY_TICKERS,
    }
    if market not in ticker_map:
        raise HTTPException(400, f"Unknown market '{market}'. Use: asx, commodities")

    cached = _scanner_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _CACHE_TTL:
        return {"market": market, "rows": cached["rows"],
                "count": len(cached["rows"]), "cached": True,
                "cache_age": int(time.time() - cached["ts"]),
                "full": full}

    tickers = ticker_map[market]
    rows = await _scan_yfinance(tickers, market)

    good = [r for r in rows if r["price"] > 0]
    if good:
        rows = good

    # Uniform sorting: biggest movers first (consistent across all markets)
    rows = sorted(rows, key=lambda r: abs(r.get("change_pct", 0)), reverse=True)

    _scanner_cache[cache_key] = {"ts": time.time(), "rows": rows}
    return {"market": market, "rows": rows, "count": len(rows), "cached": False, "full": full}


@app.get("/api/asx/universe")
async def asx_universe():
    """Return the full ASX listed company universe for search/autocomplete."""
    from api.scanners import get_asx_universe
    universe = get_asx_universe()
    return {"tickers": universe, "count": len(universe)}


# ── Broker-asset compatibility (cached, fetched once) ────
_BROKER_COMPAT = {
    "ibkr":        {"asx": True, "commodities": True, "us_etf": True, "fx": True, "options": True, "futures": True},
    "ig":          {"asx": True, "commodities": True, "us_etf": False, "fx": True, "options": False, "futures": False},
    "cmc":         {"asx": True, "commodities": True, "us_etf": False, "fx": True, "options": False, "futures": False},
    "saxo":        {"asx": True, "commodities": True, "us_etf": True, "fx": True, "options": True, "futures": True},
    "tiger":       {"asx": True, "commodities": False, "us_etf": True, "fx": False, "options": True, "futures": False},
    "moomoo":      {"asx": True, "commodities": False, "us_etf": True, "fx": False, "options": True, "futures": False},
    "pepperstone": {"asx": True, "commodities": True, "us_etf": False, "fx": True, "options": False, "futures": False},
    "finclear":    {"asx": True, "commodities": False, "us_etf": False, "fx": False, "options": False, "futures": False},
    "openmarkets": {"asx": True, "commodities": False, "us_etf": False, "fx": False, "options": False, "futures": False},
    "marketech":   {"asx": True, "commodities": False, "us_etf": False, "fx": False, "options": False, "futures": False},
    "opentrader":  {"asx": True, "commodities": False, "us_etf": False, "fx": False, "options": False, "futures": False},
    "iress":       {"asx": True, "commodities": True, "us_etf": False, "fx": True, "options": True, "futures": True},
    "cqg":         {"asx": False, "commodities": True, "us_etf": False, "fx": True, "options": True, "futures": True},
    "flextrade":   {"asx": True, "commodities": True, "us_etf": True, "fx": True, "options": True, "futures": True},
    "tradingview": {"asx": True, "commodities": True, "us_etf": True, "fx": True, "options": False, "futures": False},
    "eodhd":       {"asx": False, "commodities": False, "us_etf": False, "fx": False, "options": False, "futures": False},
}

def _get_asset_type(ticker: str) -> str:
    """Determine asset type from ticker symbol."""
    t = ticker.upper()
    if t.endswith(".AX"):
        return "asx"
    if "=F" in t:
        return "commodities" if t not in ("AUDUSD=X","GBPUSD=X","EURUSD=X") else "fx"
    if t in ("GLD","TLT","IEF","TIP","DBC","SPY","QQQ","IVV","VTI"):
        return "us_etf"
    if "=X" in t:
        return "fx"
    return "us_etf"  # default for unlisted

@app.get("/api/broker/compatible")
async def broker_compatible(ticker: str = ""):
    """Return which brokers can trade a given ticker."""
    asset_type = _get_asset_type(ticker)
    compatible = [name for name, caps in _BROKER_COMPAT.items() if caps.get(asset_type, False)]
    return {
        "ticker": ticker, "asset_type": asset_type,
        "brokers": compatible,
        "all_compat": _BROKER_COMPAT,
    }


# ─────────────────────────────────────────────
# Routes -- AI Chat & CLI
# ─────────────────────────────────────────────

@app.post("/api/ai/chat")
async def ai_chat(payload: dict):
    message = (payload.get("message") or "").strip()
    if not message: raise HTTPException(400, "message required")
    return await _run_cmd(message)


@app.post("/api/cmd")
async def api_cmd(payload: dict):
    """AI-agent CLI endpoint -- same as /api/ai/chat but always returns structured JSON."""
    cmd = (payload.get("cmd") or payload.get("message") or "").strip()
    if not cmd: raise HTTPException(400, "cmd or message required")
    return await _run_cmd(cmd)


# ─────────────────────────────────────────────
# Routes -- Paper Trading
# ─────────────────────────────────────────────

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

    eq_vals = [e["v"] for e in PAPER.equity_history] if PAPER.equity_history else []
    if len(eq_vals) >= 2:
        peak = max(eq_vals)
        drawdown_val = round((peak - eq_vals[-1]) / peak, 4) if peak > 0 else 0.0
    else:
        drawdown_val = 0.0

    sharpe_val = None
    if len(eq_vals) >= 10:
        try:
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
    """Lightweight live P&L endpoint."""
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
    """Place a paper trade."""
    if CIRCUIT_BREAKER is not None and CIRCUIT_BREAKER._trading_halted:
        raise HTTPException(403, f"Trading halted by circuit breaker: {CIRCUIT_BREAKER._halt_reason}")

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

    price = payload.get("price")
    if price is None:
        price = await _live_price(ticker)
    if price is None:
        raise HTTPException(400, f"Cannot determine price for {ticker}")
    price = float(price)

    sl = payload.get("stop_loss")
    tp = payload.get("take_profit")
    if sl is not None:
        sl = float(sl)
    if tp is not None:
        tp = float(tp)

    async with _PAPER_LOCK:
        _tickers_pre = list(set(list(PAPER.positions.keys()) + [ticker]))
        _prices_pre  = await _prices_for_positions(_tickers_pre) if _tickers_pre else {}
        _prices_pre[ticker] = price

        try:
            sizing = _calculate_position_size(ticker, price, side, PAPER, _prices_pre)
        except ValueError as e:
            raise HTTPException(400, f"Position sizing blocked: {e}")

        max_allowed_qty = sizing["max_allowed_qty"]
        position_pct    = sizing["position_pct"]

        original_qty = qty
        if side == "BUY" and qty > max_allowed_qty:
            qty = max_allowed_qty
            if qty <= 0:
                raise HTTPException(400,
                    f"Position sizing: requested {original_qty} but max allowed is 0 "
                    f"(max {_RISK_MAX_POS_SIZE_PCT}% of portfolio per position)")

        try:
            result = PAPER.place_order(ticker, side, qty, price,
                                       stop_loss=sl, take_profit=tp)
        except ValueError as e:
            raise HTTPException(400, str(e))

        result["max_allowed_qty"] = max_allowed_qty
        result["position_pct"]    = position_pct
        if side == "BUY" and original_qty > max_allowed_qty:
            result["qty_capped"]     = True
            result["requested_qty"]  = original_qty
            result["cap_reason"]     = (
                f"Capped from {original_qty} to {qty:.8g} "
                f"(max {_RISK_MAX_POS_SIZE_PCT}% of portfolio per position)"
            )

        _tickers = list(PAPER.positions.keys())
        _prices  = await _prices_for_positions(_tickers) if _tickers else {}
        current_equity = PAPER.total_value(_prices)
        PAPER.equity_history.append({"t": datetime.utcnow().isoformat(), "v": current_equity})
        PAPER.equity_history = PAPER.equity_history[-2000:]
        _save_paper_state()
        if side == "SELL" and PAPER.history:
            _db_save_trade(PAPER.history[0])
        _db_save_equity_snapshot(current_equity)

    if CIRCUIT_BREAKER is not None:
        CIRCUIT_BREAKER.state.current_equity = current_equity
        if current_equity > CIRCUIT_BREAKER.state.peak_equity:
            CIRCUIT_BREAKER.state.peak_equity = current_equity

    if NOTIFIER is not None:
        try:
            NOTIFIER.send({"type": "TRADE", "ticker": ticker, "side": side,
                           "qty": qty, "price": price, "mode": "paper"})
        except Exception:
            pass

    await WS_MANAGER.broadcast({"type": "PAPER_ORDER", "data": result})
    return result


@app.get("/api/paper/history")
async def get_paper_history():
    db_trades = _db_get_trades(limit=100)
    if db_trades:
        return {"trades": db_trades, "total": len(db_trades), "source": "db"}
    return {"trades": PAPER.history[:100], "total": len(PAPER.history), "source": "json"}


@app.get("/api/paper/analytics")
async def get_paper_analytics():
    """Compute trade performance metrics from actual closed-trade history."""
    trades = PAPER.history
    total_trades = len(trades)

    if total_trades == 0:
        return {
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "profit_factor": 0.0, "avg_holding_period_hours": 0.0,
            "largest_win": 0.0, "largest_loss": 0.0,
            "total_pnl": 0.0, "total_fees": 0.0, "expectancy": 0.0,
            "max_consecutive_wins": 0, "max_consecutive_losses": 0,
        }

    wins  = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]

    winning_trades = len(wins)
    losing_trades  = len(losses)
    win_rate       = round(winning_trades / total_trades * 100, 2)

    avg_win  = round(sum(t["pnl"] for t in wins) / winning_trades, 2) if winning_trades else 0.0
    avg_loss = round(sum(t["pnl"] for t in losses) / losing_trades, 2) if losing_trades else 0.0

    sum_wins   = sum(t["pnl"] for t in wins)
    sum_losses = abs(sum(t["pnl"] for t in losses))
    if sum_losses > 0:
        profit_factor = round(sum_wins / sum_losses, 2)
    else:
        profit_factor = 999.0 if sum_wins > 0 else 0.0

    holding_hours = []
    for t in trades:
        entry_t = t.get("entry_time")
        exit_t  = t.get("timestamp")
        if entry_t and exit_t:
            try:
                dt_entry = datetime.fromisoformat(entry_t)
                dt_exit  = datetime.fromisoformat(exit_t)
                holding_hours.append((dt_exit - dt_entry).total_seconds() / 3600)
            except (ValueError, TypeError):
                pass
    avg_holding_period_hours = round(sum(holding_hours) / len(holding_hours), 2) if holding_hours else 0.0

    largest_win  = round(max((t["pnl"] for t in trades), default=0.0), 2)
    largest_loss = round(min((t["pnl"] for t in trades), default=0.0), 2)

    total_pnl  = round(sum(t["pnl"] for t in trades), 2)
    total_fees = round(sum(t.get("fees", 0) for t in trades), 2)

    wr_frac    = winning_trades / total_trades
    expectancy = round((wr_frac * avg_win) - ((1 - wr_frac) * abs(avg_loss)), 2)

    sorted_trades = list(reversed(trades))
    max_con_wins = max_con_losses = cur_wins = cur_losses = 0
    for t in sorted_trades:
        if t["pnl"] > 0:
            cur_wins += 1
            cur_losses = 0
        elif t["pnl"] < 0:
            cur_losses += 1
            cur_wins = 0
        else:
            cur_wins = cur_losses = 0
        max_con_wins   = max(max_con_wins, cur_wins)
        max_con_losses = max(max_con_losses, cur_losses)

    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "avg_holding_period_hours": avg_holding_period_hours,
        "largest_win": largest_win,
        "largest_loss": largest_loss,
        "total_pnl": total_pnl,
        "total_fees": total_fees,
        "expectancy": expectancy,
        "max_consecutive_wins": max_con_wins,
        "max_consecutive_losses": max_con_losses,
    }


@app.post("/api/paper/close")
async def close_paper_position(payload: dict):
    """Close entire position in a ticker at market price."""
    ticker = _normalize_ticker(payload.get("ticker", "").strip())
    async with _PAPER_LOCK:
        if ticker not in PAPER.positions:
            raise HTTPException(404, f"No open position in {ticker}")
        qty   = PAPER.positions[ticker]["qty"]
        price = await _live_price(ticker)
        if price is None:
            raise HTTPException(400, f"Cannot determine price for {ticker}")
        result = PAPER.place_order(ticker, "SELL", qty, float(price))

        _tickers = list(PAPER.positions.keys())
        _prices  = await _prices_for_positions(_tickers) if _tickers else {}
        _eq_val = PAPER.total_value(_prices)
        PAPER.equity_history.append({"t": datetime.utcnow().isoformat(), "v": _eq_val})
        PAPER.equity_history = PAPER.equity_history[-2000:]
        _save_paper_state()
        if PAPER.history:
            _db_save_trade(PAPER.history[0])
        _db_save_equity_snapshot(_eq_val)

    await WS_MANAGER.broadcast({"type": "PAPER_CLOSE", "data": result})
    return result


@app.post("/api/paper/reset")
async def reset_paper_portfolio():
    async with _PAPER_LOCK:
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
    import api.portfolio as _portfolio_mod
    cash = float(payload.get("starting_cash", PAPER_STARTING_CASH))
    if cash < 1:
        raise HTTPException(400, "starting_cash must be >= 1")
    _portfolio_mod.PAPER_STARTING_CASH = cash
    _save_paper_config()
    applied = False
    async with _PAPER_LOCK:
        if not PAPER.positions:
            PAPER.cash           = cash
            PAPER.history        = []
            PAPER.equity_history = []
            PAPER.order_id       = 0
            _save_paper_state()
            applied = True
    return {"status": "ok", "starting_cash": _portfolio_mod.PAPER_STARTING_CASH, "applied": applied}


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

    db_curve = _db_get_equity_curve(limit=2000)
    curve = db_curve if db_curve else PAPER.equity_history
    return {
        "equity_curve": curve,
        "count": len(curve),
        "position_performance": pos_perf,
        "starting_cash": PAPER_STARTING_CASH,
        "source": "db" if db_curve else "json",
    }


# ─────────────────────────────────────────────
# Routes -- Watchlist
# ─────────────────────────────────────────────

@app.get("/api/watchlist")
async def get_watchlist():
    return {"watchlist": WATCHLIST}


@app.post("/api/watchlist/add")
async def watchlist_add(payload: dict):
    ticker = payload.get("ticker", "").upper().strip()
    if not ticker:
        raise HTTPException(400, "ticker required")
    async with _WATCHLIST_LOCK:
        if ticker not in WATCHLIST:
            WATCHLIST.append(ticker)
            _save_watchlist(WATCHLIST)
    return {"watchlist": WATCHLIST}


@app.post("/api/watchlist/remove")
async def watchlist_remove(payload: dict):
    ticker = payload.get("ticker", "").upper().strip()
    async with _WATCHLIST_LOCK:
        if ticker in WATCHLIST:
            WATCHLIST.remove(ticker)
            _save_watchlist(WATCHLIST)
    return {"watchlist": WATCHLIST}


# ─────────────────────────────────────────────
# Routes -- Trading Mode & Broker
# ─────────────────────────────────────────────

@app.get("/api/mode")
async def get_trading_mode():
    return {
        "mode":      _current_mode(),
        "broker":    _get_broker().name if _get_broker() else None,
        "connected": _get_broker().is_connected() if _get_broker() else False,
    }


@app.post("/api/mode")
async def set_trading_mode(payload: dict):
    import api.state as _state_mod
    new_mode = payload.get("mode", "").lower()
    if new_mode not in ("paper", "live"):
        raise HTTPException(400, "mode must be 'paper' or 'live'")
    broker_connected = _get_broker() is not None and _get_broker().is_connected()
    async with _MODE_LOCK:
        _state_mod.TRADING_MODE = new_mode
        _save_trading_mode(new_mode)
    if new_mode == "live" and not broker_connected:
        STATE.add_alert("SYSTEM", "LIVE MODE -- No broker configured. Trading halted until broker connected.", "WARNING")
    else:
        STATE.add_alert("SYSTEM", f"Trading mode -> {new_mode.upper()}", "INFO")
    await WS_MANAGER.broadcast({"type": "MODE_CHANGE", "data": {"mode": new_mode, "broker_connected": broker_connected}})
    return {"mode": new_mode, "broker_connected": broker_connected}


@app.post("/api/broker/connect")
async def broker_connect(payload: dict):
    import api.brokers as _brokers_mod
    broker_name = payload.get("broker", "").lower().strip()
    if not broker_name:
        raise HTTPException(400, "Missing 'broker' field. Send {\"broker\": \"ibkr\", \"host\": \"...\", \"port\": ..., \"client_id\": ...}")
    if broker_name not in BROKER_MAP:
        raise HTTPException(400, f"Unknown broker '{broker_name}'. Available: {', '.join(sorted(BROKER_MAP))}")

    # Collect credentials from payload or saved creds
    kwargs = {k: v for k, v in payload.items() if k != "broker" and v}
    if not kwargs:
        try:
            creds = _load_broker_creds()
            if broker_name in creds:
                kwargs = creds[broker_name]
                logger.info(f"Auto-loaded saved credentials for {broker_name}")
        except Exception as e:
            logger.warning(f"Failed to load saved credentials: {e}")

    if not kwargs:
        # Return helpful error with required fields per broker
        required = _broker_required_fields(broker_name)
        raise HTTPException(400, f"No credentials provided. {broker_name.upper()} requires: {', '.join(required)}")

    # Attempt connection
    broker: BrokerBase = BROKER_MAP[broker_name]()
    try:
        logger.info(f"Connecting to {broker_name} with fields: {list(kwargs.keys())}")
        await broker.connect(**kwargs)
    except ImportError as e:
        raise HTTPException(422, f"Missing dependency: {e}. Install with pip.")
    except ValueError as e:
        raise HTTPException(400, f"Invalid configuration: {e}")
    except ConnectionError as e:
        raise HTTPException(502, f"Cannot reach {broker_name.upper()} servers: {e}")
    except RuntimeError as e:
        raise HTTPException(401, f"Authentication failed: {e}")
    except Exception as e:
        error_msg = str(e)
        # Detect common auth failures
        if any(s in error_msg.lower() for s in ("invalid", "unauthorized", "forbidden", "api key", "signature")):
            raise HTTPException(401, f"Authentication failed for {broker_name.upper()}: {error_msg}")
        elif any(s in error_msg.lower() for s in ("timeout", "connect", "unreachable", "refused")):
            raise HTTPException(502, f"Cannot reach {broker_name.upper()} servers: {error_msg}")
        else:
            raise HTTPException(500, f"Broker connection error: {error_msg}")

    _brokers_mod.ACTIVE_BROKER = broker
    STATE.add_alert("BROKER", f"{broker_name.upper()} connected", "INFO")
    _ensure_broker_heartbeat()

    # Auto-save credentials + last active broker on successful connect
    try:
        creds = _load_broker_creds()
        creds[broker_name] = {k: v for k, v in kwargs.items() if v}
        creds["_last_active"] = broker_name
        _save_broker_creds(creds)
        logger.info(f"Auto-saved credentials for {broker_name}")
    except Exception as e:
        logger.warning(f"Failed to auto-save credentials: {e}")

    # Fetch account info to return with connect response
    acct_info = {}
    try:
        acct_info = await broker.get_account()
    except Exception as e:
        acct_info = {"error": str(e)}

    return {"status": "connected", "broker": broker_name, **acct_info}


_heartbeat_task_started = False


def _ensure_broker_heartbeat():
    global _heartbeat_task_started
    if _heartbeat_task_started:
        return
    _heartbeat_task_started = True
    asyncio.get_event_loop().create_task(_broker_heartbeat_loop())


async def _broker_heartbeat_loop():
    """Check broker health every 60s, auto-reconnect on failure."""
    global _heartbeat_task_started
    while True:
        await asyncio.sleep(60)
        import api.brokers as _b
        broker = _b.ACTIVE_BROKER
        if broker is None:
            continue
        if not broker.is_connected():
            if broker._last_credentials and broker._reconnect_attempts < broker._max_reconnect:
                logger.warning(f"Broker {broker.name} disconnected, attempting reconnect...")
                success = await broker._auto_reconnect()
                if success:
                    STATE.add_alert("BROKER", f"{broker.name.upper()} reconnected", "INFO")
                else:
                    STATE.add_alert("BROKER", f"{broker.name.upper()} reconnect failed (attempt {broker._reconnect_attempts})", "WARNING")
            continue
        ok = await broker._heartbeat()
        if not ok:
            STATE.add_alert("BROKER", f"{broker.name.upper()} heartbeat failed, will retry", "WARNING")


def _broker_required_fields(broker_name: str) -> list[str]:
    """Return required credential fields for a broker."""
    FIELDS = {
        "ibkr": ["host", "port", "client_id"],
    }
    return FIELDS.get(broker_name, ["api_key", "api_secret"])


@app.get("/api/broker/status")
async def broker_status():
    if _get_broker() is None:
        return {"broker": None, "connected": False}
    if not _get_broker().is_connected():
        return {"broker": _get_broker().name, "connected": False}
    try:
        acct = await _get_broker().get_account()
        return {"broker": _get_broker().name, "connected": True, **acct}
    except Exception as e:
        return {"broker": _get_broker().name, "connected": True, "error": str(e)}


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
    result = {}
    for name, data in creds.items():
        masked = {}
        for k, v in data.items():
            if isinstance(v, str) and len(v) > 6 and any(s in k.lower() for s in ("secret", "key", "pass", "private")):
                masked[k] = v[:4] + "\u2022" * (len(v) - 8) + v[-4:]
            else:
                masked[k] = v
        result[name] = masked
    return result


# ─────────────────────────────────────────────
# Routes -- Real Trading
# ─────────────────────────────────────────────

def _require_live():
    if _get_broker() is None or not _get_broker().is_connected():
        raise HTTPException(503, "No broker connected")
    if _current_mode() != "live":
        raise HTTPException(403, "Switch to live mode first")


@app.get("/api/real/portfolio")
async def get_real_portfolio():
    if _get_broker() is None or not _get_broker().is_connected():
        raise HTTPException(503, "No broker connected")
    try:
        positions = await _get_broker().get_positions()
        acct      = await _get_broker().get_account()
        return {"broker": _get_broker().name, "positions": positions,
                "account_value": acct.get("account_value"), "buying_power": acct.get("buying_power"),
                "cash": acct.get("cash"), "timestamp": datetime.utcnow().isoformat()}
    except Exception as e:
        raise HTTPException(502, f"Broker error: {e}")


@app.get("/api/real/suggested_qty")
async def get_suggested_qty(ticker: str = "", confidence: float = 50):
    """Suggest position size based on broker buying power and signal confidence."""
    if _get_broker() is None or not _get_broker().is_connected():
        raise HTTPException(503, "No broker connected")
    ticker = ticker.upper().strip()
    if not ticker:
        raise HTTPException(400, "ticker required")
    confidence = max(0, min(100, confidence))
    try:
        acct = await _get_broker().get_account()
        buying_power = float(acct.get("buying_power") or acct.get("cash") or 0)
    except Exception as e:
        raise HTTPException(502, f"Broker error: {e}")
    # Scale: 1% at confidence 50 → 5% at confidence 90+
    pct = 1.0 + (min(confidence, 90) - 50) * (4.0 / 40.0)  # 1% to 5%
    pct = max(0.5, min(5.0, pct))
    alloc = buying_power * (pct / 100.0)
    # Get current price
    prices = await _get_prices([ticker])
    price = prices.get(ticker, 0)
    if price <= 0:
        return {"ticker": ticker, "suggested_qty": 0, "price": 0,
                "allocation_pct": round(pct, 2), "buying_power": buying_power}
    qty = alloc / price
    # Round to whole shares for stocks
    qty = max(1, int(qty))
    return {"ticker": ticker, "suggested_qty": qty, "price": round(price, 4),
            "allocation_pct": round(pct, 2), "buying_power": round(buying_power, 2)}


@app.post("/api/real/order")
async def place_real_order(payload: dict):
    _require_live()
    if CIRCUIT_BREAKER is not None and CIRCUIT_BREAKER._trading_halted:
        raise HTTPException(403, f"Trading halted by circuit breaker: {CIRCUIT_BREAKER._halt_reason}")

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
        result = await _get_broker().place_order(ticker, side, qty, price)
    except Exception as e:
        raise HTTPException(502, f"Broker order failed: {e}")

    import api.state as _state_mod
    try:
        acct = await _get_broker().get_account()
        current_equity = acct.get("account_value", 0)
        _state_mod.REAL_EQUITY_CURVE.append({"t": datetime.utcnow().isoformat(), "v": current_equity})
        _save_real_equity(_state_mod.REAL_EQUITY_CURVE[-2000:])
        _db_save_real_equity_snapshot(current_equity)
        if CIRCUIT_BREAKER is not None:
            CIRCUIT_BREAKER.state.current_equity = current_equity
            if current_equity > CIRCUIT_BREAKER.state.peak_equity:
                CIRCUIT_BREAKER.state.peak_equity = current_equity
    except Exception:
        pass

    if NOTIFIER is not None:
        try:
            NOTIFIER.send({"type": "TRADE", "ticker": ticker, "side": side,
                           "qty": qty, "price": price, "mode": "live"})
        except Exception:
            pass

    STATE.add_alert("LIVE", f"{side} {qty} -- {ticker}", "INFO")
    await WS_MANAGER.broadcast({"type": "REAL_ORDER", "data": result})
    return result


@app.get("/api/real/history")
async def get_real_history():
    if _get_broker() is None or not _get_broker().is_connected():
        raise HTTPException(503, "No broker connected")
    try:
        return {"history": await _get_broker().get_history(), "broker": _get_broker().name}
    except Exception as e:
        raise HTTPException(502, f"Broker error: {e}")


@app.post("/api/real/close")
async def close_real_position(payload: dict):
    _require_live()
    ticker = payload.get("ticker", "").upper().strip()
    if not ticker: raise HTTPException(400, "ticker required")
    try:
        result = await _get_broker().close_position(ticker)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, f"Broker error: {e}")

    import api.state as _state_mod
    try:
        acct = await _get_broker().get_account()
        eq_val = acct.get("account_value", 0)
        _state_mod.REAL_EQUITY_CURVE.append({"t": datetime.utcnow().isoformat(), "v": eq_val})
        _save_real_equity(_state_mod.REAL_EQUITY_CURVE[-2000:])
        _db_save_real_equity_snapshot(eq_val)
    except Exception:
        pass
    STATE.add_alert("LIVE", f"Closed {ticker}", "INFO")
    await WS_MANAGER.broadcast({"type": "REAL_CLOSE", "data": result})
    return result


@app.get("/api/real/equity_curve")
async def get_real_equity_curve():
    import api.state as _state_mod
    curve = _state_mod.REAL_EQUITY_CURVE
    if not curve:
        curve = _db_get_real_equity_curve(2000)
    return {"equity_curve": curve, "count": len(curve)}


# ─────────────────────────────────────────────
# Routes -- Agent
# ─────────────────────────────────────────────

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


@app.get("/api/agent/status")
async def agent_status():
    """Return autonomous agent status."""
    import api.agent as _agent_mod
    interval = AGENT_CONFIG.get("interval_seconds", 300)
    return {
        "enabled": AGENT_CONFIG.get("enabled", False),
        "interval_seconds": interval,
        "min_confidence": AGENT_CONFIG.get("min_confidence", 60),
        "last_cycle_time": _agent_mod._agent_last_cycle_time,
        "next_cycle_time": _agent_mod._agent_next_cycle_time,
        "cycle_count": STATE.cycle_count,
        "trading_mode": _current_mode(),
        "auto_execute": _current_mode().upper() != "LIVE",
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.post("/api/agent/toggle")
async def agent_toggle(payload: dict = None):
    """Enable or disable autonomous auto-trading."""
    if payload and "enabled" in payload:
        new_state = bool(payload["enabled"])
    else:
        new_state = not AGENT_CONFIG.get("enabled", False)

    AGENT_CONFIG["enabled"] = new_state
    _save_agent_config(AGENT_CONFIG)

    status_str = "ENABLED" if new_state else "DISABLED"
    logger.info(f"Autonomous agent {status_str} by user")
    STATE.add_alert("AGENT", f"Autonomous trading {status_str}", "WARNING" if new_state else "INFO")
    await WS_MANAGER.broadcast({
        "type": "AGENT_CONFIG",
        "data": {"enabled": new_state, "interval_seconds": AGENT_CONFIG.get("interval_seconds", 300)},
    })

    return {
        "enabled": new_state,
        "interval_seconds": AGENT_CONFIG.get("interval_seconds", 300),
        "message": f"Autonomous trading {status_str}",
    }


@app.post("/api/agent/interval")
async def agent_interval(payload: dict):
    """Change the autonomous cycle interval."""
    try:
        new_interval = int(payload.get("interval_seconds", 300))
    except (TypeError, ValueError):
        raise HTTPException(400, "interval_seconds must be an integer")

    if new_interval < 60:
        raise HTTPException(400, "Minimum interval is 60 seconds")
    if new_interval > 3600:
        raise HTTPException(400, "Maximum interval is 3600 seconds (1 hour)")

    AGENT_CONFIG["interval_seconds"] = new_interval
    _save_agent_config(AGENT_CONFIG)

    logger.info(f"Autonomous agent interval changed to {new_interval}s")
    STATE.add_alert("AGENT", f"Cycle interval changed to {new_interval}s ({new_interval // 60}m {new_interval % 60}s)", "INFO")

    return {
        "interval_seconds": new_interval,
        "enabled": AGENT_CONFIG.get("enabled", False),
        "message": f"Interval set to {new_interval}s",
    }


# ─────────────────────────────────────────────
# Routes -- Notifications, Risk, FX
# ─────────────────────────────────────────────

@app.post("/api/notifications/test")
async def test_notification(payload: dict):
    STATE.add_alert("TEST", f"Test notification sent to {payload.get('channel', 'unknown')}", "INFO")
    return {"status": "sent", "channel": payload.get("channel")}


@app.get("/api/risk/status")
async def risk_status():
    """Return circuit breaker and risk management status."""
    if CIRCUIT_BREAKER is None:
        return {"available": False, "reason": "CircuitBreaker not initialised"}
    s = CIRCUIT_BREAKER.state
    return {
        "available": True,
        "trading_halted": CIRCUIT_BREAKER._trading_halted,
        "halt_reason": CIRCUIT_BREAKER._halt_reason or None,
        "current_equity": round(s.current_equity, 2),
        "peak_equity": round(s.peak_equity, 2),
        "daily_pnl_pct": round(s.daily_pnl_pct, 2),
        "drawdown_pct": round(s.drawdown_pct, 2),
        "max_daily_loss_pct": CIRCUIT_BREAKER.settings.max_daily_loss_pct if SETTINGS_AVAILABLE else 2.0,
        "max_drawdown_pct": CIRCUIT_BREAKER.settings.max_drawdown_pct if SETTINGS_AVAILABLE else 10.0,
    }


@app.post("/api/risk/reset")
async def risk_reset():
    """Reset circuit breaker halt (manual override)."""
    if CIRCUIT_BREAKER is None:
        raise HTTPException(503, "CircuitBreaker not available")
    CIRCUIT_BREAKER._trading_halted = False
    CIRCUIT_BREAKER._halt_reason = ""
    STATE.add_alert("RISK", "Circuit breaker manually reset", "WARNING")
    return {"status": "reset", "trading_halted": False}


@app.get("/api/fx/audusd")
async def fx_audusd():
    """Return current AUD/USD exchange rate."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("AUDUSD=X")
        rate = ticker.fast_info.get("lastPrice", 0.65)
    except Exception:
        rate = 0.65
    return {"rate": round(rate, 5), "pair": "AUD/USD", "source": "yfinance"}


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
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=0.1)
            except asyncio.TimeoutError:
                pass
            heartbeat += 1
            await ws.send_json({
                "type": "HEARTBEAT",
                "seq": heartbeat,
                "status": "NOMINAL",
                "uptime": STATE.uptime_seconds(),
                "timestamp": datetime.utcnow().isoformat(),
            })
            if heartbeat % 4 == 0:
                health = _gen_portfolio_health()
                await ws.send_json({"type": "HEALTH_UPDATE", "data": health})
    except WebSocketDisconnect:
        WS_MANAGER.disconnect(ws)
