#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  DALIOS — Automated Trading Framework
#  Start the web UI server
# ═══════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")"

GREEN='\033[0;32m'
AMBER='\033[0;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

echo -e "${GREEN}"
echo " ██████╗  █████╗ ██╗     ██╗ ██████╗ ███████╗"
echo " ██╔══██╗██╔══██╗██║     ██║██╔═══██╗██╔════╝"
echo " ██║  ██║███████║██║     ██║██║   ██║███████╗"
echo " ██║  ██║██╔══██║██║     ██║██║   ██║╚════██║"
echo " ██████╔╝██║  ██║███████╗██║╚██████╔╝███████║"
echo " ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝ ╚═════╝ ╚══════╝"
echo -e "${RESET}"
echo -e "${AMBER} AUTOMATED TRADING FRAMEWORK — DALIOS PRINCIPLES${RESET}"
echo " ═══════════════════════════════════════════════════"
echo ""
echo -e "${CYAN}[*] Starting DALIOS server → http://localhost:8000${RESET}"
echo ""

python -m uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload
