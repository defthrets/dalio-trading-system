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
echo "  Building DALIOS iOS App..."
echo ""
echo "  NOTE: This must be run on macOS with Xcode installed."
echo ""

# Check macOS
if [[ "$(uname)" != "Darwin" ]]; then
    echo "ERROR: iOS builds require macOS with Xcode."
    exit 1
fi

# Check Xcode
if ! command -v xcodebuild &>/dev/null; then
    echo "ERROR: Xcode not found. Install from the App Store."
    exit 1
fi

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python3 not found."
    echo "       brew install python@3.12"
    exit 1
fi

# Install toolchain
echo "[1/4] Installing kivy-ios toolchain..."
pip3 install kivy-ios --quiet

# Install app dependencies
echo "[2/4] Installing app dependencies..."
pip3 install -r requirements.txt --quiet

# Build Python recipes for iOS
echo "[3/4] Building Python recipes (first run takes 20-30 min)..."
toolchain build python3 kivy

# Build additional recipes needed by FastAPI
toolchain build openssl libffi

# Create Xcode project
echo "[4/4] Creating Xcode project..."
toolchain create DALIOS ios_main.py

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  XCODE PROJECT CREATED"
echo ""
echo "  Next steps:"
echo "    1. Open:  dalios-ios/DALIOS.xcodeproj"
echo "    2. Set your Apple Developer Team in Xcode signing settings"
echo "    3. Connect your iPhone via USB"
echo "    4. Click Run (▶) in Xcode to install on your device"
echo ""
echo "  To distribute via TestFlight:"
echo "    1. Product → Archive in Xcode"
echo "    2. Distribute App → App Store Connect"
echo "    3. Invite testers in App Store Connect → TestFlight"
echo ""
echo "  NOTE: You need an Apple Developer account (\$99/year)"
echo "  Free accounts can sideload to your own device only."
echo "══════════════════════════════════════════════════════════════"
echo ""
