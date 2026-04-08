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
echo "  Building DALIOS Desktop Application (Linux)..."
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python3 not found. Install Python 3.10+ and ensure it's on PATH."
    exit 1
fi

# Check system dependencies for pywebview (GTK)
echo "[0/3] Checking system dependencies..."
if ! python3 -c "import gi; gi.require_version('Gtk','3.0')" 2>/dev/null; then
    echo ""
    echo "  pywebview on Linux requires GTK3 and WebKit2."
    echo "  Install them with:"
    echo ""
    echo "    Ubuntu/Debian:  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1"
    echo "    Fedora:         sudo dnf install python3-gobject gtk3 webkit2gtk4.1"
    echo "    Arch:           sudo pacman -S python-gobject gtk3 webkit2gtk-4.1"
    echo ""
    echo "  Install the above, then re-run this script."
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

# Build executable
echo "[3/3] Building executable..."
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
    --noconfirm \
    --clean \
    desktop.py

# Copy database if it exists (user data — not bundled, lives alongside exe)
if [ -f "data/storage/trading.db" ]; then
    echo "Copying existing database to dist..."
    mkdir -p "dist/DALIOS/data/storage"
    cp "data/storage/trading.db" "dist/DALIOS/data/storage/trading.db"
fi

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  BUILD COMPLETE"
echo ""
echo "  Executable:  dist/DALIOS/DALIOS"
echo "  To run:      ./dist/DALIOS/DALIOS"
echo "  To share:    tar -czf DALIOS-linux.tar.gz -C dist DALIOS"
echo "══════════════════════════════════════════════════════════════"
echo ""
