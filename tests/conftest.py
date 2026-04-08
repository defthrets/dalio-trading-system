"""
Shared fixtures for the Dalio Trading System test suite.

Patches heavy imports (torch, transformers, yfinance, aiohttp, etc.)
so tests never make network calls and load fast.
"""

import sys
import types
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Stub out heavy / network-dependent modules BEFORE any project imports
# ---------------------------------------------------------------------------
_STUB_MODULES = [
    "torch", "transformers", "ta", "riskfolio_lib", "riskfolio",
    "yfinance", "feedparser", "apscheduler", "apscheduler.schedulers",
    "apscheduler.schedulers.asyncio", "apscheduler.triggers",
    "apscheduler.triggers.interval",
    "ib_insync",
]

for mod_name in _STUB_MODULES:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# 2. Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def paper_portfolio():
    """Fresh PaperPortfolio instance for each test (bypasses module-level STATE)."""
    from api.server import PaperPortfolio
    return PaperPortfolio()


@pytest.fixture
def sample_signals():
    """Sample signal data for testing."""
    return [
        {
            "ticker": "BHP.AX", "action": "BUY", "confidence": 75,
            "price": 45.00, "stop_loss": 42.75, "take_profit": 49.50,
        },
    ]
