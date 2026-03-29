# Dalio Trading System — Install & Run

## Requirements
- **Python 3.10+** (download from https://python.org)
- **Internet connection** (for live market data)

## Quick Start (Windows)

### Step 1 — Install
Double-click `setup.bat` or run in terminal:
```
setup.bat
```
This installs all Python packages automatically.

### Step 2 — Start the server
Double-click `start.bat` or run:
```
start.bat
```

### Step 3 — Open the UI
Navigate to: **http://localhost:8000**

---

## Manual Install

```bash
pip install -r requirements.txt
```

### Dependencies installed
| Package | Purpose |
|---------|---------|
| fastapi | Web framework / API server |
| uvicorn | ASGI server (runs FastAPI) |
| python-multipart | Form data parsing |
| numpy | Numerical calculations |
| pandas | Data frames (required by yfinance) |
| yfinance | Live ASX / commodities / crypto prices |
| requests | HTTP client |
| aiohttp | Async HTTP (CoinGecko API) |
| loguru | Logging |
| feedparser | Real financial news RSS feeds |

---

## Troubleshooting

### Scanners show no data
1. Run `setup.bat` again to ensure all dependencies are installed
2. Restart the server with `start.bat` (kills old process, starts fresh)
3. Wait 10–30 seconds for first scan (yfinance fetches live data)
4. If ASX shows empty: yfinance may be rate-limiting — wait 60s and refresh

### 404 errors on scanner tabs
- **Always restart the server** after updating code via `start.bat`
- The browser will cache old JS — hold Shift + click Refresh to hard-reload

### Port 8000 already in use
`start.bat` automatically kills any process on port 8000 before starting.

### Python not found
Make sure Python is added to PATH during installation (check "Add to PATH" in installer).

---

## Scanner Data Sources
| Tab | Source | Refresh |
|-----|--------|---------|
| ASX Scanner | Yahoo Finance (yfinance) | 90s cache |
| Crypto | CoinGecko free API → yfinance fallback | 90s cache |
| Commodities | Yahoo Finance (yfinance) | 90s cache |
| Signal Ops | yfinance 3-month price history | On demand |
| Intel Center | Live RSS feeds (Reuters, Yahoo, CNBC, AFR, etc.) | 30 min cache |
| Economic Quadrant | yfinance macro indicators | 5 min cache |

---

## Architecture
```
dalio-trading-system/
├── api/
│   └── server.py          # FastAPI backend (~3000 lines)
├── ui/
│   ├── index.html         # Single-page app
│   └── static/
│       ├── app.js         # All frontend logic
│       └── style.css      # Hacker/military UI theme
├── data/
│   ├── paper_portfolio.json   # Your paper trading state (auto-saved)
│   └── paper_config.json      # Settings (starting cash etc.)
├── requirements.txt
├── setup.bat              # Install script
└── start.bat              # Launch script
```
