@echo off
cd /d "%~dp0"

echo Stopping any existing server instances...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq uvicorn*" >nul 2>&1
taskkill /F /IM python3.exe /FI "WINDOWTITLE eq uvicorn*" >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| find ":8000" ^| find "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo.
echo  ██████   █████  ██      ██  ██████      ████████ ██████   █████  ██████  ███████
echo  ██   ██ ██   ██ ██      ██ ██    ██        ██    ██   ██ ██   ██ ██   ██ ██
echo  ██   ██ ███████ ██      ██ ██    ██        ██    ██████  ███████ ██   ██ █████
echo  ██   ██ ██   ██ ██      ██ ██    ██        ██    ██   ██ ██   ██ ██   ██ ██
echo  ██████  ██   ██ ███████ ██  ██████         ██    ██   ██ ██   ██ ██████  ███████
echo.
echo  Dalio All Weather Trading System
echo  http://localhost:8000
echo  Press Ctrl+C to stop
echo.

python -m uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload
