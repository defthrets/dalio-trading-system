@echo off
title DALIOS — Automated Trading Framework
color 0A

echo.
echo  ██████╗  █████╗ ██╗     ██╗ ██████╗ ███████╗
echo  ██╔══██╗██╔══██╗██║     ██║██╔═══██╗██╔════╝
echo  ██║  ██║███████║██║     ██║██║   ██║███████╗
echo  ██║  ██║██╔══██║██║     ██║██║   ██║╚════██║
echo  ██████╔╝██║  ██║███████╗██║╚██████╔╝███████║
echo  ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝ ╚═════╝ ╚══════╝
echo.
echo  AUTOMATED TRADING FRAMEWORK — DALIOS PRINCIPLES
echo  ════════════════════════════════════════════════════
echo.

cd /d "%~dp0"

echo [*] Checking Python environment...
python --version || (echo ERROR: Python not found && pause && exit /b 1)

echo [*] Installing dependencies...
pip install fastapi uvicorn[standard] numpy python-dotenv loguru --quiet

echo.
echo [*] Starting DALIOS server on http://localhost:8000
echo [*] Press Ctrl+C to stop
echo.

python -m uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload

pause
