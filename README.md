# DALIOS — Automated Trading Framework

> An All-Weather strategy trading system with live market scanning, signal generation, paper trading, and real-time portfolio tracking.

![Status](https://img.shields.io/badge/status-operational-brightgreen)
![Mode](https://img.shields.io/badge/mode-paper%20%7C%20live-blue)
![Stack](https://img.shields.io/badge/stack-FastAPI%20%2B%20Chart.js-cyan)

---

## What It Does

DALIOS is a self-contained trading intelligence platform built around All-Weather portfolio principles. It scans ASX equities and commodities in real time, generates RSI + trend-based signals, tracks paper and live trades, and monitors portfolio risk — all from a single military-style web UI.

- **Signal scanner** across 200+ tickers (ASX, Commodities)
- **Economic quadrant engine** — detects the current macro regime (rising/falling growth + inflation) and scores every signal against it
- **Paper trading** with live P&L tracking, position management, and equity curve
- **Intel Center** — live RSS news from 12 financial feeds, keyword sentiment scoring, geopolitical risk monitor
- **Risk Matrix** — Sharpe ratio, max drawdown, circuit breaker, position sizing
- **Backtest Lab** — walk-forward backtesting across 8 historical periods

---

## Quick Start

### Requirements
- Python 3.10+
- Internet connection (live market data)

### Install & Run (Windows)

```bat
setup.bat     # installs all Python dependencies
start.bat     # kills port 8000, starts the server
```

Then open **http://localhost:8000** in your browser.

> Hold **Shift + Refresh** after any update to clear the JS cache.

### Manual Install

```bash
pip install -r requirements.txt
python -m uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload
```

---

## UI Tabs

| Tab | What It Does |
|-----|-------------|
| **Command Centre** | Main hub — equity curve, live positions with P&L, AI trade recommendations, recent trade history |
| **Signal Ops** | Live signal scanner — RSI + trend signals with confidence scores, stop/target levels, AI justification |
| **Intel Center** | Real-time news feed from 12 RSS sources with sentiment scoring and geopolitical risk monitor |
| **Holy Grail** | Correlation matrix and portfolio allocation weights across asset classes |
| **Risk Matrix** | Sharpe ratio, max drawdown, circuit breaker, win rate, per-position risk table |
| **Backtest Lab** | Walk-forward backtest across 8 periods with total return, Sharpe, and drawdown metrics |
| **ASX Scanner** | Live scanner for 93 ASX stocks across banking, mining, healthcare, tech, and REITs |
| **Commodities Scanner** | Gold, silver, oil, gas, wheat, copper and more via yfinance |
| **Paper Trading** | Simulated trading with live P&L, portfolio history, equity curve, and position management |
| **Live Trading** | Broker integration for real money trading (requires broker connection) |
| **Settings** | Starting cash, API keys, notification config |

---

## Architecture

```
dalio-trading-system/
├── api/
│   └── server.py           # FastAPI backend (~3500 lines)
│                           # Endpoints: signals, scanners, paper trading,
│                           # sentiment, quadrant, health, live P&L, backtest
├── ui/
│   ├── index.html          # Single-page app
│   └── static/
│       ├── app.js          # All frontend logic
│       └── style.css       # Military/hacker UI theme
├── data/
│   ├── paper_portfolio.json    # Paper trading state (auto-saved)
│   └── paper_config.json       # Starting cash and settings
├── requirements.txt
├── setup.bat               # Install script
├── start.bat               # Launch script (kills old process first)
└── INSTALL.md              # Detailed install and troubleshooting guide
```

---

## Data Sources

| Feature | Source | Refresh Rate |
|---------|--------|-------------|
| ASX Scanner | Yahoo Finance (yfinance) | 90s cache |
| Commodities | Yahoo Finance (yfinance) | 90s cache |
| Signal Ops | yfinance 3-month price history | On demand |
| Live P&L | yfinance per-position price fetch | Every 15s |
| Intel Center | 12 RSS feeds (Reuters, Yahoo, CNBC, AFR, FT, MarketWatch, etc.) | 30 min cache |
| Economic Quadrant | yfinance macro indicators | 5 min cache |

---

## Key Features

### All-Weather Signal Engine
Signals are generated using RSI extremes and trend direction, then scored against the current Dalio economic quadrant. Each signal includes entry price, stop loss (1.5× ATR), take profit (2.5× ATR), confidence score (50–95%), and an AI-written justification.

### Live P&L Tracking
Open positions update every 15 seconds globally — no matter which tab you're on. P&L cells flash green (▲ improved) or red (▼ declined) when values change. Both the Paper Trading table and Command Centre tiles stay in sync.

### Economic Quadrant
Detects the current macro regime from live indicator data:
- **Rising Growth + Falling Inflation** → equities, credit
- **Rising Growth + Rising Inflation** → commodities, TIPS
- **Falling Growth + Falling Inflation** → bonds, gold
- **Falling Growth + Rising Inflation** → gold, cash, short equities

### Intel Center
Pulls articles from 12 RSS feeds every 30 minutes. Each headline is scored for sentiment (bullish/bearish/neutral) using financial keyword sets and mapped to a Dalio quadrant. Conflict keywords trigger a geopolitical risk flag.

### Paper Trading
Start with configurable cash (default $1,000). Place BUY/LONG and SELL/SHORT paper trades. Track unrealised P&L live, view equity curve with per-asset performance lines, and review full closed trade history.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` | Web framework / API server |
| `uvicorn` | ASGI server |
| `python-multipart` | Form data parsing |
| `numpy` | RSI, Sharpe, statistical calculations |
| `pandas` | Data frames (required by yfinance) |
| `yfinance` | Live ASX / commodities prices |
| `requests` | HTTP client |
| `aiohttp` | Async HTTP client |
| `loguru` | Structured logging |
| `feedparser` | RSS feed parsing (Intel Center) |

---

## Troubleshooting

See **[INSTALL.md](INSTALL.md)** for full troubleshooting steps.

**Scanners show no data** — Run `setup.bat`, restart with `start.bat`, wait 30s for first yfinance fetch.

**Buttons do nothing** — Hard refresh with Shift+F5 to clear cached JS.

**Port 8000 in use** — `start.bat` auto-kills the old process. Or manually: `netstat -ano | findstr 8000`.

**ASX data empty** — yfinance rate-limits at ~100 req/min. Wait 60s and refresh.

---

## Disclaimer

This system is for **educational and research purposes only**. Nothing in DALIOS constitutes financial advice. Paper trading results do not guarantee future live trading performance. Always do your own research before making any investment decisions.
