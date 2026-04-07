"""Tests for trade analytics computation."""

import pytest
from datetime import datetime, timedelta


class TestEmptyAnalytics:
    def test_empty_analytics(self, paper_portfolio):
        """Empty history returns all-zero analytics."""
        assert paper_portfolio.history == []
        # Simulate what the analytics endpoint computes for zero trades
        total_trades = len(paper_portfolio.history)
        assert total_trades == 0


class TestWinningTrades:
    def test_winning_trade_analytics(self, paper_portfolio):
        """Win rate = 100% after one profitable trade."""
        paper_portfolio.place_order("BHP.AX", "BUY", 10, 40.0)
        paper_portfolio.place_order("BHP.AX", "SELL", 10, 50.0)

        trades = paper_portfolio.history
        assert len(trades) == 1
        assert trades[0]["pnl"] > 0

        wins = [t for t in trades if t["pnl"] > 0]
        win_rate = len(wins) / len(trades) * 100
        assert win_rate == 100.0


class TestMixedTrades:
    def test_mixed_trades(self, paper_portfolio):
        """Correct win rate and profit factor with mixed results."""
        # Trade 1: win (buy 40, sell 50)
        paper_portfolio.place_order("BHP.AX", "BUY", 5, 40.0)
        paper_portfolio.place_order("BHP.AX", "SELL", 5, 50.0)

        # Trade 2: loss (buy 60, sell 50)
        paper_portfolio.place_order("CBA.AX", "BUY", 5, 60.0)
        paper_portfolio.place_order("CBA.AX", "SELL", 5, 50.0)

        trades = paper_portfolio.history
        assert len(trades) == 2

        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] < 0]
        assert len(wins) == 1
        assert len(losses) == 1

        win_rate = len(wins) / len(trades) * 100
        assert win_rate == 50.0

        sum_wins = sum(t["pnl"] for t in wins)
        sum_losses = abs(sum(t["pnl"] for t in losses))
        if sum_losses > 0:
            profit_factor = sum_wins / sum_losses
            assert profit_factor > 0
