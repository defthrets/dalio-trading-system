@echo off
cd /d "%~dp0"
echo.
echo  ============================================================
echo   DALIO TRADING SYSTEM — SETUP
echo  ============================================================
echo.

REM Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    python3 --version >nul 2>&1
    if errorlevel 1 (
        echo  [ERROR] Python not found. Install from https://python.org
        pause
        exit /b 1
    )
    set PYTHON=python3
) else (
    set PYTHON=python
)

echo  [1/3] Upgrading pip...
%PYTHON% -m pip install --upgrade pip --quiet

echo  [2/3] Installing all dependencies from requirements.txt...
%PYTHON% -m pip install -r requirements.txt

echo  [3/3] Verifying key packages...
%PYTHON% -c "import fastapi; print('  fastapi:', fastapi.__version__)"
%PYTHON% -c "import uvicorn; print('  uvicorn:', uvicorn.__version__)"
%PYTHON% -c "import yfinance; print('  yfinance:', yfinance.__version__)"
%PYTHON% -c "import pandas; print('  pandas:', pandas.__version__)"
%PYTHON% -c "import numpy; print('  numpy:', numpy.__version__)"
%PYTHON% -c "import aiohttp; print('  aiohttp:', aiohttp.__version__)"
%PYTHON% -c "import feedparser; print('  feedparser:', feedparser.__version__)"
%PYTHON% -c "import loguru; print('  loguru: ok')"

echo.
echo  ============================================================
echo   SETUP COMPLETE
echo  ============================================================
echo.
echo   Run start.bat to launch the server at http://localhost:8000
echo.
pause
