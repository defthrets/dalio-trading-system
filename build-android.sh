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
echo "  Building DALIOS Android APK..."
echo ""
echo "  NOTE: This must be run on Linux (or WSL on Windows)."
echo "  Buildozer does not support building on Windows natively."
echo ""

# Check we're on Linux
if [[ "$(uname)" != "Linux" ]]; then
    echo "ERROR: Android builds require Linux or WSL."
    echo ""
    echo "  On Windows, use WSL:"
    echo "    wsl --install"
    echo "    # then inside WSL:"
    echo "    cd /mnt/c/Users/YourName/dalio-trading-system"
    echo "    ./build-android.sh"
    echo ""
    exit 1
fi

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python3 not found."
    exit 1
fi

# Install system dependencies
echo "[1/4] Checking system dependencies..."
if command -v apt &>/dev/null; then
    echo "  Installing build tools (may require sudo)..."
    sudo apt update -qq
    sudo apt install -y -qq \
        build-essential \
        git \
        python3-pip \
        python3-venv \
        openjdk-17-jdk \
        autoconf \
        libtool \
        pkg-config \
        zlib1g-dev \
        libncurses5-dev \
        libncursesw5-dev \
        libtinfo5 \
        cmake \
        libffi-dev \
        libssl-dev \
        zip \
        unzip
elif command -v dnf &>/dev/null; then
    sudo dnf install -y \
        gcc gcc-c++ make git \
        python3-pip python3-devel \
        java-17-openjdk-devel \
        autoconf libtool \
        zlib-devel ncurses-devel \
        cmake libffi-devel openssl-devel \
        zip unzip
fi

# Install buildozer
echo "[2/4] Installing Buildozer..."
pip3 install --upgrade buildozer cython --quiet

# Install Kivy
echo "[3/4] Installing Kivy..."
pip3 install kivy --quiet

# Build APK
echo "[4/4] Building APK (this may take 15-30 minutes on first run)..."
echo "  Buildozer will download Android SDK/NDK automatically."
echo ""
buildozer android debug

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  BUILD COMPLETE"
echo ""
echo "  APK location:  bin/dalios-*-debug.apk"
echo ""
echo "  To install on your phone:"
echo "    1. Enable 'Install from unknown sources' in Android settings"
echo "    2. Transfer the APK to your phone"
echo "    3. Tap the APK to install"
echo ""
echo "  Or install via ADB:"
echo "    adb install bin/dalios-*-debug.apk"
echo "══════════════════════════════════════════════════════════════"
echo ""
