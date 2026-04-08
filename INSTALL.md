# Dalio Trading System — Install & Run

## Requirements
- **Python 3.10+** (download from https://python.org)
- **Internet connection** (for live market data)

## Quick Start (Windows)

### Step 1 — Configure
Copy the environment template and add your API keys:
```
copy .env.template .env
```
Edit `.env` with your API keys (optional — system works without them using free data sources).

### Step 2 — Install
Double-click `setup.bat` or run in terminal:
```
setup.bat
```

### Step 3 — Broker Credentials (if trading live)
Copy the credential template:
```
copy data\broker_credentials.json.template data\broker_credentials.json
```
Edit with your ASX broker API keys. **Never commit this file to git.**

### Step 4 — Start the server
Double-click `start.bat` or run:
```
start.bat
```

### Step 5 — Open the UI
Navigate to: **http://localhost:8000**

---

## Manual Install (Linux/Mac)

```bash
pip install -r requirements.txt
cp .env.template .env
cp data/broker_credentials.json.template data/broker_credentials.json
python main.py
```

---

## Security Notes

**IMPORTANT:** Never commit secrets to git.

- `.env` — Contains API keys. Gitignored by default.
- `data/broker_credentials.json` — Contains broker API keys/secrets. Gitignored.
- If you accidentally commit credentials, **revoke and rotate them immediately**.
- The `.env.template` and `broker_credentials.json.template` files are safe to commit.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| fastapi | Web framework / API server |
| uvicorn | ASGI server (runs FastAPI) |
| numpy / pandas | Numerical + data frame ops |
| yfinance | Live ASX / commodities prices |
| aiohttp | Async HTTP client |
| loguru | Logging |
| feedparser | Financial news RSS feeds |
| pydantic-settings | Configuration management |
| sqlalchemy | Database ORM (trade/signal logging) |
| ta | Technical analysis indicators (RSI, MACD, BB) |
| transformers + torch | Sentiment analysis (optional, ~2GB) |

---

## Troubleshooting

### Scanners show no data
1. Re-run `setup.bat` to ensure dependencies are installed
2. Restart server — wait 10–30s for first scan
3. If ASX is empty: yfinance rate-limit — wait 60s and refresh

### FinBERT errors
`transformers` + `torch` are large. System works without them — logs a warning and skips sentiment.

### Port 8000 in use
`start.bat` auto-kills existing processes on port 8000.

---

## Architecture
```
dalio-trading-system/
├── api/server.py              # FastAPI backend
├── config/                    # Settings + asset universe
├── engines/                   # Quadrant, sentiment, correlation, risk parity
├── trading/                   # Signal gen, circuit breaker, execution
├── notifications/             # Discord/Telegram alerts
├── backtesting/               # Walk-forward optimisation
├── data/                      # Storage, ingestion, portfolios
├── ui/                        # Single-page frontend
├── .env.template              # Environment config template
└── requirements.txt
```
