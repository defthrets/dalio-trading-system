# DALIOS — Autonomous Trading Framework

> Free, autonomous AI trading system for ASX equities and Australian commodities. Built on Ray Dalio's All Weather strategy.

![Status](https://img.shields.io/badge/status-operational-brightgreen)
![Price](https://img.shields.io/badge/price-FREE-22c55e)
![Open Source](https://img.shields.io/badge/open%20source-yes-22c55e)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20Linux%20%7C%20macOS-orange)
![Stack](https://img.shields.io/badge/stack-Python%20%2B%20FastAPI%20%2B%20Chart.js-cyan)

---

## What Is It?

DALIOS is a free, open-source autonomous trading system built around Ray Dalio's core investment principles — the **All Weather** portfolio strategy and his **Economic Machine** framework for understanding how economies work.

The All Weather strategy is designed to perform in any economic environment by balancing assets across four regimes: rising growth, falling growth, rising inflation, and falling inflation. DALIOS implements this by classifying the current macro regime in real time and scoring every trade signal against it — so the system naturally shifts toward the right assets for the current conditions, just like Dalio's Bridgewater does at scale.

It scans 300+ ASX equities and Australian commodities in real time, generates trade signals, executes trades, and manages risk — all autonomously from a single terminal UI. Toggle "SCAN FULL ASX" to scan all ~1,900 listed companies.

**100% free and open source. No subscriptions. No hidden fees. No paywalls.**

---

## Download

Grab the latest build from [**Releases**](https://github.com/defthrets/dalios/releases/latest) — Windows, Linux, and macOS. No Python needed.

Or run from source:

**Linux / WSL**
```bash
git clone https://github.com/defthrets/dalios.git
cd dalios
pip install -r requirements.txt
python -m uvicorn api.server:app --port 8000
# Open http://localhost:8000
```

**macOS**
```bash
git clone https://github.com/defthrets/dalios.git
cd dalios
pip3 install -r requirements.txt
python3 -m uvicorn api.server:app --port 8000
# Open http://localhost:8000
```

**Windows (PowerShell)**
```powershell
git clone https://github.com/defthrets/dalios.git
cd dalios
pip install -r requirements.txt
python -m uvicorn api.server:app --port 8000
# Open http://localhost:8000
```

---

## 11 Modules

| Module | What It Does |
|--------|-------------|
| **Command Center** | Portfolio overview, equity curve, AI recommendations, activity feed |
| **Live Trading** | Real broker integration, live positions, P&L tracking |
| **Signal Ops** | AI signals with confidence scores, stop-loss, take-profit, justification |
| **Intel Center** | 12+ RSS news feeds with sentiment scoring, geopolitical risk monitor |
| **Holy Grail** | Correlation matrix, diversification meter (Dalio's uncorrelated streams) |
| **Risk Matrix** | Sharpe, Sortino, max drawdown, circuit breaker, VaR |
| **Backtest Lab** | Walk-forward backtesting across historical periods |
| **ASX 300 Scanner** | Default 300 tickers, toggle for full ~1,900 ASX universe |
| **Commodities** | Australian commodity stocks, ETFs, gold, lithium, uranium, agriculture |
| **Paper Trading** | Risk-free simulated trading with full analytics |
| **Settings** | 16 broker connections, risk config, notifications, display options |

---

## 16 Supported Brokers

Interactive Brokers, IG Markets, CMC Invest, Saxo Markets, Tiger Brokers, moomoo, Pepperstone, FinClear, Open Markets, Marketech, OpenTrader, IRESS, COG Financial, FlexTrade, TradingView, EODHD

---

## Dalio's Principles In Action

Ray Dalio's insight is that all economic shifts boil down to two forces — **growth** and **inflation** — each either rising or falling. That gives four quadrants, and each one favours different assets:

| Quadrant | Condition | DALIOS Favours |
|----------|-----------|---------------|
| Q1 | Rising growth, falling inflation | Equities, corporate bonds |
| Q2 | Rising growth, rising inflation | Commodities, inflation-linked assets |
| Q3 | Falling growth, falling inflation | Bonds, defensive equities |
| Q4 | Falling growth, rising inflation | Gold, cash, short positions |

DALIOS detects the current quadrant from live macro data and scores every signal against it. A buy signal on a mining stock scores higher in Q2 (commodity-friendly) than in Q3 (risk-off). This is the same logic Bridgewater uses — DALIOS just makes it accessible to everyone, for free.

## How It Works

1. **Quadrant Engine** detects the current economic regime from live indicators
2. **Signal Engine** generates RSI + trend signals scored against the regime
3. **Risk Parity** calculates position weights based on risk contribution, not dollar amount
4. **Circuit Breaker** halts trading if daily loss or drawdown limits are hit
5. **Autonomous Agent** runs the full cycle on a loop — scan, signal, size, execute, notify

---

## Tech Stack

Python, FastAPI, Chart.js, yfinance, NumPy, Pandas, SQLAlchemy, WebSocket, pywebview (desktop)

---

## Build From Source

```bash
# Windows exe
python -m PyInstaller --noconfirm --onedir --windowed --name DALIOS desktop.py

# Or use the build scripts
build.bat       # Windows
./build.sh      # Linux
./build-mac.sh  # macOS
```

CI builds all platforms automatically when you push a version tag (`git tag v2.4.0 && git push --tags`).

---

## Disclaimer

Educational and research purposes only. Not financial advice. Algorithmic signals are not guarantees. You can lose money trading. Always do your own research. Past performance means nothing.

---

**Website:** [defthrets.github.io/dalios-website](https://defthrets.github.io/dalios-website)
