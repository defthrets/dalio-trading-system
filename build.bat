@echo off
cd /d "%~dp0"

echo.
echo  ██████   █████  ██      ██  ██████  ████████
echo  ██   ██ ██   ██ ██      ██ ██    ██ ██
echo  ██   ██ ███████ ██      ██ ██    ██ ████████
echo  ██   ██ ██   ██ ██      ██ ██    ██       ██
echo  ██████  ██   ██ ███████ ██  ██████  ████████
echo.
echo  Building DALIOS Desktop Application...
echo.

:: Check Python
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python not found. Install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)

:: Install build dependencies
echo [1/3] Installing build dependencies...
pip install pywebview pyinstaller --quiet

:: Install app dependencies
echo [2/3] Installing app dependencies...
pip install -r requirements.txt --quiet

:: Build optional data args
set ENV_ARG=
if exist ".env" set ENV_ARG=--add-data ".env;."

:: Build exe
echo [3/3] Building executable...
pyinstaller ^
    --name "DALIOS" ^
    --onedir ^
    --windowed ^
    --icon "ui/static/favicon.ico" ^
    --add-data "ui;ui" ^
    --add-data "config;config" ^
    --add-data "api;api" ^
    --add-data "agents;agents" ^
    --add-data "data;data" ^
    --add-data "notifications;notifications" ^
    --add-data "engines;engines" ^
    --add-data "trading;trading" ^
    --add-data "backtesting;backtesting" ^
    %ENV_ARG% ^
    --hidden-import "uvicorn.logging" ^
    --hidden-import "uvicorn.loops" ^
    --hidden-import "uvicorn.loops.auto" ^
    --hidden-import "uvicorn.protocols" ^
    --hidden-import "uvicorn.protocols.http" ^
    --hidden-import "uvicorn.protocols.http.auto" ^
    --hidden-import "uvicorn.protocols.websockets" ^
    --hidden-import "uvicorn.protocols.websockets.auto" ^
    --hidden-import "uvicorn.lifespan" ^
    --hidden-import "uvicorn.lifespan.on" ^
    --hidden-import "uvicorn.lifespan.off" ^
    --hidden-import "api" ^
    --hidden-import "api.server" ^
    --hidden-import "api.state" ^
    --hidden-import "api.brokers" ^
    --hidden-import "api.scanners" ^
    --hidden-import "api.signals" ^
    --hidden-import "api.portfolio" ^
    --hidden-import "api.websocket" ^
    --hidden-import "api.auth" ^
    --hidden-import "api.utils" ^
    --hidden-import "api.agent" ^
    --hidden-import "agents" ^
    --hidden-import "agents.dalio_agent" ^
    --hidden-import "data" ^
    --hidden-import "data.storage" ^
    --hidden-import "data.storage.models" ^
    --hidden-import "config" ^
    --hidden-import "config.settings" ^
    --hidden-import "config.assets" ^
    --hidden-import "notifications" ^
    --hidden-import "engines" ^
    --hidden-import "trading" ^
    --hidden-import "backtesting" ^
    --hidden-import "sqlalchemy.dialects.sqlite" ^
    --hidden-import "multiprocessing" ^
    --collect-submodules "webview" ^
    --collect-submodules "uvicorn" ^
    --noconfirm ^
    --clean ^
    desktop.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo BUILD FAILED. Check errors above.
    pause
    exit /b 1
)

:: Copy database if it exists (user data — not bundled, lives alongside exe)
if exist "data\storage\trading.db" (
    echo Copying existing database to dist...
    if not exist "dist\DALIOS\data\storage" mkdir "dist\DALIOS\data\storage"
    copy "data\storage\trading.db" "dist\DALIOS\data\storage\trading.db" >nul
)

echo.
echo ══════════════════════════════════════════════════════════════
echo  BUILD COMPLETE
echo.
echo  Executable:  dist\DALIOS\DALIOS.exe
echo  To run:      double-click DALIOS.exe in the dist\DALIOS folder
echo  To share:    zip the entire dist\DALIOS folder
echo ══════════════════════════════════════════════════════════════
echo.
pause
