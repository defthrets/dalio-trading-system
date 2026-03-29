@echo off
echo Installing Dalios Trading System dependencies...
python3 -m pip install fastapi "uvicorn[standard]" python-multipart numpy yfinance requests aiohttp loguru
echo.
echo Done! Start the server with:
echo   python3 -m uvicorn api.server:app --host 0.0.0.0 --port 8000
