@echo off
cd /d "%~dp0"
echo Starting Dalios Trading System on http://localhost:8000
python3 -m uvicorn api.server:app --host 0.0.0.0 --port 8000
