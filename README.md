# DALIOS — Automated Trading Framework

> An All-Weather strategy trading system with autonomous AI agent, live market scanning, signal generation, paper & live trading, and real-time portfolio tracking across ASX equities, commodities, and global markets.

![Status](https://img.shields.io/badge/status-operational-brightgreen)
![Mode](https://img.shields.io/badge/mode-paper%20%7C%20live-blue)
![Stack](https://img.shields.io/badge/stack-FastAPI%20%2B%20Chart.js-cyan)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20Linux%20%7C%20macOS%20%7C%20Android%20%7C%20iOS-orange)

---

## What Is DALIOS?

DALIOS is a self-contained trading intelligence platform built around Ray Dalio's All-Weather portfolio principles. It scans 300+ ASX equities and commodities in real time, generates RSI + trend-based signals scored against the current economic regime, manages paper and live trades, and monitors portfolio risk — all from a single military-style terminal UI.

Meet **Rex** — your blue-tongue lizard trading assistant who guides you through the system with an interactive tutorial.

### Core Capabilities

- **Signal Scanner** — RSI + trend signals across 300+ tickers with confidence scores, stop-loss, take-profit, and AI justification
- **Economic Quadrant Engine** — detects the current macro regime (rising/falling growth + inflation) and scores every signal against it
- **Autonomous Agent** — AI-driven trading loop that runs cycles automatically using All-Weather principles
- **Paper Trading** — risk-free simulated trading with live P&L, equity curves, and full analytics
- **Live Trading** — real broker integration with 16 supported brokers
- **Intel Center** — live RSS news from 12+ financial feeds with sentiment scoring and geopolitical risk monitoring
- **Risk Matrix** — Sharpe ratio, max drawdown, circuit breaker, position sizing, and risk controls
- **Holy Grail Portfolio** — correlation matrix and diversification meter based on Dalio's 15+ uncorrelated return streams
- **Backtest Lab** — walk-forward backtesting across 8 historical periods
- **Cross-Platform** — runs on Windows, Linux, macOS, Android, and iOS

---

## Quick Start

### Requirements
- Python 3.10+
- Internet connection (live market data)

### Option 1: Run in Browser

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server
python -m uvicorn api.server:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** in your browser.

### Option 2: Windows Quick Start

```bat
setup.bat     :: installs all Python dependencies
start.bat     :: kills port 8000, starts the server
```

### Option 3: Desktop App (Windows .exe)

```bat
build.bat     :: builds standalone DALIOS.exe
```

Run `dist\DALIOS\DALIOS.exe` — no Python installation needed on the target machine.

> Hold **Shift + F5** after any update to clear the browser cache.

---

## Platform Builds

DALIOS runs natively on all major platforms. Each build bundles Python + FastAPI and opens the UI in a native window.

| Platform | Build Script | Output | How to Run |
|----------|-------------|--------|-----------|
| Windows | `build.bat` | `dist\DALIOS\DALIOS.exe` | Double-click the exe |
| Linux | `build.sh` | `dist/DALIOS/DALIOS` | `./dist/DALIOS/DALIOS` |
| macOS | `build-mac.sh` | `dist/DALIOS.app` | `open dist/DALIOS.app` |
| Android | `build-android.sh` | `bin/dalios-*-debug.apk` | Transfer APK to phone, tap to install |
| iOS | `build-ios.sh` | Xcode project | Open in Xcode, sign, and run |

### Automated Builds (GitHub Actions)

All platforms build automatically when you push a version tag:

```bash
git tag v1.0.0
git push --tags
```

This triggers GitHub Actions to build all 5 platforms in parallel and creates a **GitHub Release** with download links. Users just visit the Releases page and download the version for their platform — no building required.

You can also trigger a build manually from the **Actions** tab on GitHub.

---

## Running on Windows

```bat
:: Option A: Browser
setup.bat
start.bat
:: Open http://localhost:8000

:: Option B: Desktop app
build.bat
:: Run dist\DALIOS\DALIOS.exe
```

---

## Running on Linux

### Browser Mode
```bash
pip install -r requirements.txt
python -m uvicorn api.server:app --host 0.0.0.0 --port 8000
```

### Desktop App
```bash
# Install GTK/WebKit dependencies first:
# Ubuntu/Debian:
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1

# Fedora:
sudo dnf install python3-gobject gtk3 webkit2gtk4.1

# Arch:
sudo pacman -S python-gobject gtk3 webkit2gtk-4.1

# Build
chmod +x build.sh
./build.sh

# Run
./dist/DALIOS/DALIOS
```

---

## Running on macOS

### Browser Mode
```bash
pip install -r requirements.txt
python -m uvicorn api.server:app --host 0.0.0.0 --port 8000
```

### Desktop App
```bash
chmod +x build-mac.sh
./build-mac.sh

# Run
open dist/DALIOS.app

# If macOS blocks the app (Gatekeeper):
xattr -cr dist/DALIOS.app
```

---

## Running on Android

The Android version runs Python + FastAPI directly on the device using Kivy + Buildozer. The app starts a local server and displays the full DALIOS UI in a native WebView.

### Download Pre-Built APK
Go to the **Releases** page on GitHub and download `DALIOS-android.apk`. Transfer it to your phone and tap to install (enable "Install from unknown sources" in Android settings).

### Build From Source
The build must be done on Linux (or WSL on Windows):

```bash
# On Linux / WSL:
chmod +x build-android.sh
./build-android.sh

# First build takes 15-30 minutes (downloads Android SDK/NDK automatically)
# APK output: bin/dalios-*-debug.apk
```

**Install via ADB:**
```bash
adb install bin/dalios-*-debug.apk
```

**System requirements for building:**
- Linux (or WSL on Windows)
- Python 3.10+
- Java 17 (OpenJDK)
- ~10GB disk space (Android SDK/NDK)

---

## Running on iOS

The iOS version uses the same architecture as Android — Python/FastAPI runs natively on the device with the UI in a WKWebView. Due to Apple's code signing requirements, iOS builds need a few extra steps.

### Prerequisites
- A Mac with **Xcode** installed (free from App Store)
- An **Apple Developer account**:
  - **Free account**: can sideload to your own device only (expires after 7 days)
  - **Paid account ($99/year)**: distribute via TestFlight to up to 10,000 testers, or publish to App Store

### Build Steps

```bash
# 1. Install the kivy-ios toolchain
pip3 install kivy-ios cython

# 2. Build Python recipes for iOS (first run takes 20-30 minutes)
toolchain build python3 kivy openssl libffi

# 3. Create the Xcode project
toolchain create DALIOS ios_main.py

# Or use the build script:
chmod +x build-ios.sh
./build-ios.sh
```

### Install on Your iPhone

1. Open `dalios-ios/DALIOS.xcodeproj` in Xcode
2. In the project settings, select your **Team** under Signing & Capabilities
3. Connect your iPhone via USB cable
4. Select your iPhone as the build target in the top toolbar
5. Click the **Run** button (▶) — Xcode will compile, sign, and install the app
6. On your iPhone: go to **Settings → General → VPN & Device Management** and trust your developer certificate (first time only)

### Distribute via TestFlight (Paid Developer Account)

TestFlight lets you share the app with up to 10,000 testers — they install it from the TestFlight app, no building required on their end.

1. In Xcode: **Product → Archive**
2. In the Organizer window: click **Distribute App → App Store Connect**
3. Upload to App Store Connect
4. Log in to [App Store Connect](https://appstoreconnect.apple.com)
5. Go to your app → **TestFlight** tab
6. Add testers by email — they'll get an invite to install via the TestFlight app

### Sideload Without a Mac (AltStore)

If you don't have a Mac, you can sideload the IPA using **AltStore**:

1. Download the pre-built Xcode project from GitHub Releases (`DALIOS-ios-xcode.zip`)
2. Someone with a Mac builds the IPA: Xcode → Product → Archive → Export IPA
3. Install [AltStore](https://altstore.io) on your Windows/Mac computer
4. Connect iPhone via USB, open AltStore, and sideload the IPA
5. Note: free accounts require re-signing every 7 days

---

## UI Tabs

| Tab | What It Does |
|-----|-------------|
| **Command Centre** | Main hub — equity curve, live positions with P&L, AI trade recommendations, recent trade history |
| **Live Trading** | Broker connection panel, real portfolio with live positions and P&L (requires broker) |
| **Signal Ops** | Live signal scanner — RSI + trend signals with confidence scores, stop/target levels, AI justification |
| **Intel Center** | Real-time news feed from 12+ RSS sources with sentiment scoring, geopolitical risk monitor, news quadrant signal |
| **Holy Grail** | Correlation matrix and portfolio allocation weights — Dalio's 15+ uncorrelated return streams concept |
| **Risk Matrix** | Sharpe ratio, max drawdown, circuit breaker status, win rate, per-position risk table |
| **Backtest Lab** | Walk-forward backtest across 8 historical periods with total return, Sharpe, and drawdown metrics |
| **ASX Scanner** | Live scanner for 300+ ASX stocks across banking, mining, healthcare, tech, REITs, and more |
| **Commodities Scanner** | Gold, silver, oil, gas, wheat, copper, uranium, and more via yfinance futures data |
| **Paper Trading** | Simulated trading with live P&L, portfolio history, equity curve with per-asset lines, and full analytics |
| **Settings** | Starting cash, broker credentials, API keys, Discord/Telegram notifications, display preferences |

---

## Architecture

```
dalio-trading-system/
├── api/
│   ├── server.py          # FastAPI backend — 60+ endpoints
│   ├── brokers.py         # 16 broker integrations
│   ├── scanners.py        # ASX + commodity market scanners
│   ├── signals.py         # Signal generation, quadrant scoring, correlation
│   ├── portfolio.py       # Paper trading engine, position sizing
│   ├── state.py           # Global state, DB persistence
│   ├── utils.py           # Caching, encryption, technical indicators
│   ├── agent.py           # Autonomous agent loop, SL/TP monitoring
│   ├── websocket.py       # WebSocket manager, CLI command parser
│   └── auth.py            # JWT authentication (optional)
├── agents/
│   └── dalio_agent.py     # Autonomous All-Weather trading orchestrator
├── engines/
│   ├── quadrant_engine.py     # Economic regime detection (4 quadrants)
│   ├── correlation_engine.py  # Asset correlation analysis
│   ├── risk_parity_engine.py  # Risk-parity weight calculation
│   └── sentiment_engine.py    # Sentiment scoring (keyword + optional FinBERT)
├── trading/
│   ├── signal_generator.py    # RSI/trend signal generation
│   ├── execution.py           # Trade execution engine
│   └── circuit_breaker.py     # Risk controls (daily loss, drawdown limits)
├── data/
│   ├── ingestion/             # Macro, market, and news data fetchers
│   └── storage/
│       └── models.py          # SQLAlchemy ORM models
├── notifications/
│   └── notifier.py            # Discord + Telegram alert integration
├── config/
│   ├── settings.py            # Configuration management
│   └── assets.py              # Core asset definitions
├── backtesting/
│   └── walk_forward.py        # Walk-forward backtesting (8 periods)
├── ui/
│   ├── index.html             # Single-page app
│   ├── splash.html            # Desktop boot splash screen
│   └── static/
│       ├── app.js             # All frontend logic
│       ├── style.css          # Military/hacker terminal UI theme
│       └── img/               # Rex mascot images (7 variants)
├── desktop.py                 # Desktop app entry (pywebview)
├── android_main.py            # Android entry (Kivy + WebView)
├── ios_main.py                # iOS entry (Kivy + WKWebView)
├── build.bat                  # Windows build script
├── build.sh                   # Linux build script
├── build-mac.sh               # macOS build script
├── build-android.sh           # Android build script
├── build-ios.sh               # iOS build script
├── buildozer.spec             # Android build config
├── .github/workflows/
│   └── build-release.yml      # CI/CD — auto-build all 5 platforms
├── requirements.txt
├── .env.example               # Environment config template
├── setup.bat                  # Windows dependency installer
└── start.bat                  # Windows server launcher
```

---

## Supported Brokers

DALIOS supports 16 brokers with a focus on the Australian market:

| Broker | Market | Connection Type |
|--------|--------|----------------|
| **Interactive Brokers (IBKR)** | Global | ib_insync API |
| **IG Markets** | ASX CFDs | REST API |
| **CMC Markets** | ASX CFDs | REST API |
| **Moomoo (Futu)** | ASX Equities | OpenAPI |
| **Saxo Markets** | ASX, ETFs, Derivatives | OpenAPI |
| **Tiger Brokers** | ASX Equities | OpenAPI |
| **FinClear** | ASX Wholesale | FIX/REST |
| **OpenMarkets** | ASX Execution | REST API |
| **Pepperstone** | CFDs, Forex | cTrader/MT4/MT5 |
| **Marketech** | ASX | IRESS Integration |
| **OpenTrader** | ASX | FinClear Execution |
| **IRESS** | Professional | FIX API |
| **CQG** | Futures & Commodities | Platform API |
| **FlexTrade** | Institutional | EMS/OMS |
| **TradingView** | Webhook Routing | Webhooks |
| **EODHD** | Data Only | REST API |

---

## Market Scanners

### ASX Scanner (300+ Stocks)
Covers 19 sectors:
- Big 4 Banks (CBA, WBC, ANZ, NAB)
- Other Banks & Financials (20+)
- Mining & Resources Majors (BHP, RIO, FMG)
- Mining Juniors, Gold, Lithium, Uranium, Rare Earths
- Energy (WDS, STO, ORG)
- Healthcare & Biotech (CSL, COH, RMD)
- Technology & Fintech
- Consumer, Retail, REITs, Industrials, Telecom

### Commodity Scanner
- Precious Metals: Gold, Silver, Platinum, Palladium
- Energy: Crude Oil (WTI, Brent), Natural Gas
- Base Metals: Copper
- Agricultural: Wheat, Corn, Soybeans
- ETFs: GLD, SLV, USO, PPLT, COPX, URA

---

## Autonomous Agent

The Dalio Agent (`agents/dalio_agent.py`) is an autonomous trading orchestrator that embodies All-Weather principles:

1. **Circuit Breaker Check** — halt trading if daily loss or drawdown limits are breached
2. **Quadrant Classification** — determine the current economic regime
3. **Correlation Analysis** — refresh asset correlations periodically
4. **Risk-Parity Weights** — calculate position weights based on risk contribution, not dollar allocation
5. **Signal Generation** — generate and filter RSI + trend signals, scored against the current quadrant
6. **Position Sizing** — size trades based on risk budget and portfolio heat
7. **Execution** — place orders through the connected broker
8. **Notifications** — push trade alerts to Discord/Telegram

The agent runs on a configurable loop interval and can be started/stopped from the UI or API.

---

## Economic Quadrant Engine

Detects the current macro regime from live indicator data and adjusts signal scoring:

| Quadrant | Growth | Inflation | Favoured Assets |
|----------|--------|-----------|----------------|
| **Q1** | Rising | Falling | Equities, Credit |
| **Q2** | Rising | Rising | Commodities, TIPS |
| **Q3** | Falling | Falling | Bonds, Gold |
| **Q4** | Falling | Rising | Gold, Cash, Short Equities |

---

## Signal Engine

Signals are generated using RSI extremes and trend direction, then scored against the current economic quadrant:

- **Entry Price** — current market price
- **Stop Loss** — 1.5x ATR below entry
- **Take Profit** — 2.5x ATR above entry
- **Confidence Score** — 50% to 95% based on signal strength + quadrant alignment
- **AI Justification** — plain-English explanation of why the signal was generated

---

## Data Sources

| Feature | Source | Refresh Rate |
|---------|--------|-------------|
| ASX Scanner | Yahoo Finance (yfinance) | 90s cache |
| Commodities | Yahoo Finance (yfinance) | 90s cache |
| Signal Ops | yfinance 3-month price history | On demand |
| Live P&L | yfinance per-position price fetch | Every 15s |
| Intel Center | 12+ RSS feeds (Reuters, Yahoo, CNBC, AFR, FT, MarketWatch) | 30 min cache |
| Economic Quadrant | yfinance macro indicators | 5 min cache |
| Backtest | yfinance historical data | On demand |

---

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Key settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `TRADING_MODE` | `paper` | `paper` or `live` |
| `DATABASE_URL` | `sqlite:///data/storage/trading.db` | Database connection string |
| `AUTH_ENABLED` | `false` | Enable JWT authentication |
| `DISCORD_WEBHOOK` | _(empty)_ | Discord notification webhook URL |
| `TELEGRAM_TOKEN` | _(empty)_ | Telegram bot token |
| `CIRCUIT_BREAKER_MAX_DAILY_LOSS` | `500` | Max daily loss before halting |
| `CIRCUIT_BREAKER_MAX_DRAWDOWN` | `0.10` | Max drawdown percentage (10%) |
| `MAX_POSITION_SIZE` | `0.05` | Max single position as fraction of portfolio |

---

## API Reference

DALIOS exposes 60+ REST endpoints and a WebSocket for real-time updates.

### Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Server health check |
| `GET` | `/api/status` | System status (mode, broker, uptime) |
| `GET` | `/api/signals` | RSI + trend signals with confidence scores |
| `GET` | `/api/quadrant` | Current economic quadrant classification |
| `GET` | `/api/sentiment` | News sentiment analysis |
| `GET` | `/api/correlation` | Asset correlation matrix |
| `GET` | `/api/portfolio/health` | Portfolio metrics (Sharpe, drawdown, P&L) |
| `GET` | `/api/markets/{market}` | Scanner data (asx, commodities) |
| `GET` | `/api/backtest/latest` | Walk-forward backtest results |
| `POST` | `/api/paper/order` | Place paper trade |
| `POST` | `/api/real/order` | Place live trade |
| `POST` | `/api/broker/connect` | Connect to broker |
| `POST` | `/api/agent/boot` | Start autonomous agent |
| `WS` | `/ws` | Real-time updates (signals, P&L, alerts) |

See the full API docs at **http://localhost:8000/docs** (auto-generated by FastAPI).

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` | Web framework / API server |
| `uvicorn` | ASGI server |
| `numpy` | RSI, Sharpe, statistical calculations |
| `pandas` | Data frames (yfinance dependency) |
| `yfinance` | Live ASX / commodities / forex prices |
| `sqlalchemy` | Database ORM |
| `aiohttp` | Async HTTP client |
| `apscheduler` | Scheduled task runner (agent loop) |
| `loguru` | Structured logging |
| `feedparser` | RSS feed parsing (Intel Center) |
| `ta` | Technical analysis indicators |
| `pydantic` | Data validation |
| `python-dotenv` | Environment config |

### Desktop App (additional)
| `pywebview` | Native window wrapper |
| `pyinstaller` | Executable bundler |

### Android (additional)
| `kivy` | Cross-platform UI framework |
| `buildozer` | Android APK builder |

### iOS (additional)
| `kivy-ios` | iOS toolchain |
| `cython` | Python-to-C compiler |

---

## Rex — Your Trading Assistant

Rex is a blue-tongue lizard mascot who guides you through DALIOS:

| Image | Role |
|-------|------|
| **rex-computer** | Tutorial guide — appears behind each tutorial bubble |
| **rex-money** | Welcome screen — greets you on first launch |
| **rex-thinking** | Boot splash — shown during desktop app startup |
| **rex-boss** | Tutorial complete — congratulates you with trading wisdom |
| **rex-panic** | Circuit breaker — when risk limits are hit |
| **rex-broke** | Heavy losses — portfolio drawdown warning |

The interactive tutorial auto-advances every 10 seconds through all tabs, with a countdown timer and progress bar. You can skip ahead, go back, or stop the tutorial permanently.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **Scanners show no data** | Run `setup.bat`, restart with `start.bat`, wait 30s for first yfinance fetch |
| **Buttons do nothing** | Hard refresh with Shift+F5 to clear cached JS |
| **Port 8000 in use** | `start.bat` auto-kills the old process. Manual: `netstat -ano \| findstr 8000` |
| **ASX data empty** | yfinance rate-limits at ~100 req/min. Wait 60s and refresh |
| **macOS blocks app** | Right-click → Open, or run `xattr -cr dist/DALIOS.app` |
| **Android APK won't install** | Enable "Install from unknown sources" in Android settings |
| **iOS app won't launch** | Trust the developer certificate: Settings → General → VPN & Device Management |
| **Desktop app slow to start** | Normal — the boot splash runs while FastAPI initialises (5-10 seconds) |

See **[INSTALL.md](INSTALL.md)** for detailed installation and troubleshooting steps.

---

## Testing

```bash
pytest tests/ -v
```

Covers API endpoints, paper trading, analytics, position sizing, fee models, and settings.

---

## Disclaimer

This system is for **educational and research purposes only**. Nothing in DALIOS constitutes financial advice. Paper trading results do not guarantee future live trading performance. The autonomous agent makes decisions based on algorithmic signals — it is not infallible. Always do your own research before making any investment decisions. Past performance means absolutely nothing, and if your trades go south, Rex takes zero responsibility. He's a lizard.

---

Built with FastAPI, Chart.js, and a healthy respect for risk management.
