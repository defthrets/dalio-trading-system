#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo ""
echo "  ██████   █████  ██      ██  ██████  ████████"
echo "  ██   ██ ██   ██ ██      ██ ██    ██ ██      "
echo "  ██   ██ ███████ ██      ██ ██    ██ ████████"
echo "  ██   ██ ██   ██ ██      ██ ██    ██       ██"
echo "  ██████  ██   ██ ███████ ██  ██████  ████████"
echo ""
echo "  Building DALIOS Desktop Application (macOS)..."
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python3 not found. Install Python 3.10+ from python.org or via Homebrew:"
    echo "       brew install python@3.12"
    exit 1
fi

# Install build dependencies
echo "[1/3] Installing build dependencies..."
pip3 install pywebview pyinstaller --quiet

# Install app dependencies
echo "[2/3] Installing app dependencies..."
pip3 install -r requirements.txt --quiet

# Build optional data args
ENV_ARG=""
if [ -f ".env" ]; then
    ENV_ARG="--add-data .env:."
fi

# Build .app bundle
echo "[3/3] Building application..."
pyinstaller \
    --name "DALIOS" \
    --onedir \
    --windowed \
    --icon "ui/static/favicon.ico" \
    --add-data "ui:ui" \
    --add-data "config:config" \
    --add-data "api:api" \
    --add-data "agents:agents" \
    --add-data "data:data" \
    --add-data "notifications:notifications" \
    --add-data "engines:engines" \
    --add-data "trading:trading" \
    --add-data "backtesting:backtesting" \
    $ENV_ARG \
    --hidden-import "uvicorn.logging" \
    --hidden-import "uvicorn.loops" \
    --hidden-import "uvicorn.loops.auto" \
    --hidden-import "uvicorn.protocols" \
    --hidden-import "uvicorn.protocols.http" \
    --hidden-import "uvicorn.protocols.http.auto" \
    --hidden-import "uvicorn.protocols.websockets" \
    --hidden-import "uvicorn.protocols.websockets.auto" \
    --hidden-import "uvicorn.lifespan" \
    --hidden-import "uvicorn.lifespan.on" \
    --hidden-import "uvicorn.lifespan.off" \
    --hidden-import "api" \
    --hidden-import "api.server" \
    --hidden-import "api.state" \
    --hidden-import "api.brokers" \
    --hidden-import "api.scanners" \
    --hidden-import "api.signals" \
    --hidden-import "api.portfolio" \
    --hidden-import "api.websocket" \
    --hidden-import "api.auth" \
    --hidden-import "api.utils" \
    --hidden-import "api.agent" \
    --hidden-import "agents" \
    --hidden-import "agents.dalio_agent" \
    --hidden-import "data" \
    --hidden-import "data.storage" \
    --hidden-import "data.storage.models" \
    --hidden-import "config" \
    --hidden-import "config.settings" \
    --hidden-import "config.assets" \
    --hidden-import "notifications" \
    --hidden-import "engines" \
    --hidden-import "trading" \
    --hidden-import "backtesting" \
    --hidden-import "sqlalchemy.dialects.sqlite" \
    --hidden-import "multiprocessing" \
    --collect-submodules "webview" \
    --collect-submodules "uvicorn" \
    --osx-bundle-identifier "com.dalios.trading" \
    --noconfirm \
    --clean \
    desktop.py

# Copy database if it exists (user data — not bundled, lives alongside app)
if [ -f "data/storage/trading.db" ]; then
    echo "Copying existing database to dist..."
    mkdir -p "dist/DALIOS.app/Contents/MacOS/data/storage"
    cp "data/storage/trading.db" "dist/DALIOS.app/Contents/MacOS/data/storage/trading.db"
fi

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  BUILD COMPLETE"
echo ""
echo "  Application:  dist/DALIOS.app"
echo "  To run:       open dist/DALIOS.app"
echo "  To share:     zip -r DALIOS-mac.zip dist/DALIOS.app"
echo ""
echo "  NOTE: If macOS blocks the app, right-click → Open,"
echo "  or run: xattr -cr dist/DALIOS.app"
echo "══════════════════════════════════════════════════════════════"
echo ""
