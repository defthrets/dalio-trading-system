"""Tests for position sizing and risk management logic."""

import pytest
from unittest.mock import patch
from datetime import datetime


class TestPositionSizing:
    def test_max_position_size(self, paper_portfolio):
        """Cannot exceed max % of portfolio in a single position."""
        from api.server import _calculate_position_size, _RISK_MAX_POS_SIZE_PCT

        total = paper_portfolio.total_value({})
        max_value = total * (_RISK_MAX_POS_SIZE_PCT / 100.0)

        result = _calculate_position_size(
            "BHP.AX", 10.0, "BUY", paper_portfolio, {}
        )
        # max_allowed_qty * price should not exceed max position value
        assert result["max_allowed_qty"] * 10.0 <= max_value + 0.01

    def test_max_open_positions(self, paper_portfolio):
        """Blocked when at max open positions."""
        from api.server import _calculate_position_size, _RISK_MAX_OPEN

        # Fill up positions to the max
        for i in range(_RISK_MAX_OPEN):
            ticker = f"T{i:03d}.AX"
            paper_portfolio.positions[ticker] = {
                "qty": 1, "entry_price": 1.0, "entry_time": "2026-01-01",
                "side": "LONG", "cost_basis": 1.0,
                "stop_loss": None, "take_profit": None,
            }

        prices = {t: 1.0 for t in paper_portfolio.positions}
        with pytest.raises(ValueError, match="Max open positions"):
            _calculate_position_size("NEW.AX", 10.0, "BUY", paper_portfolio, prices)

    def test_daily_loss_limit(self, paper_portfolio):
        """Blocked when daily loss exceeds the limit."""
        from api.server import _calculate_position_size, _RISK_MAX_DAILY_LOSS

        # Simulate a large loss today
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        total = paper_portfolio.total_value({})
        big_loss = -(total * _RISK_MAX_DAILY_LOSS / 100.0) - 1  # exceed limit

        paper_portfolio.history.append({
            "pnl": big_loss,
            "timestamp": f"{today_str}T12:00:00",
            "ticker": "BHP.AX", "side": "SELL",
        })

        with pytest.raises(ValueError, match="Daily loss limit"):
            _calculate_position_size("CBA.AX", 10.0, "BUY", paper_portfolio, {})

    def test_position_size_calculation(self, paper_portfolio):
        """Correct qty returned for a normal trade."""
        from api.server import _calculate_position_size

        result = _calculate_position_size(
            "BHP.AX", 50.0, "BUY", paper_portfolio, {}
        )
        assert result["reason"] == "ok"
        assert result["max_allowed_qty"] > 0
        # Qty should be achievable within cash
        assert result["max_allowed_qty"] * 50.0 <= paper_portfolio.cash
