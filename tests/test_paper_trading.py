"""Tests for PaperPortfolio — the core paper-trading engine."""

import pytest


class TestPaperPortfolioInitial:
    def test_initial_state(self, paper_portfolio):
        """Fresh portfolio starts with correct defaults."""
        from api.server import PAPER_STARTING_CASH
        assert paper_portfolio.cash == PAPER_STARTING_CASH
        assert paper_portfolio.positions == {}
        assert paper_portfolio.history == []
        assert paper_portfolio.equity_history == []

    def test_total_value_no_positions(self, paper_portfolio):
        """total_value with no positions equals cash."""
        assert paper_portfolio.total_value({}) == paper_portfolio.cash


class TestBuyOrders:
    def test_buy_order(self, paper_portfolio):
        """BUY reduces cash and creates a position."""
        initial_cash = paper_portfolio.cash
        result = paper_portfolio.place_order("BHP.AX", "BUY", 10, 45.0)

        assert result["side"] == "BUY"
        assert result["ticker"] == "BHP.AX"
        assert "BHP.AX" in paper_portfolio.positions
        assert paper_portfolio.positions["BHP.AX"]["qty"] == 10
        assert paper_portfolio.cash < initial_cash

    def test_insufficient_cash(self, paper_portfolio):
        """Buying more than available cash raises ValueError."""
        huge_qty = paper_portfolio.cash / 0.01 + 1  # way too much
        with pytest.raises(ValueError, match="Insufficient cash"):
            paper_portfolio.place_order("BHP.AX", "BUY", huge_qty, 100.0)

    def test_multiple_positions(self, paper_portfolio):
        """Can hold positions in multiple tickers simultaneously."""
        paper_portfolio.place_order("BHP.AX", "BUY", 5, 45.0)
        paper_portfolio.place_order("CBA.AX", "BUY", 3, 100.0)

        assert "BHP.AX" in paper_portfolio.positions
        assert "CBA.AX" in paper_portfolio.positions
        assert len(paper_portfolio.positions) == 2


class TestSellOrders:
    def test_sell_order(self, paper_portfolio):
        """SELL closes position and records P&L in history."""
        paper_portfolio.place_order("BHP.AX", "BUY", 10, 45.0)
        paper_portfolio.place_order("BHP.AX", "SELL", 10, 50.0)

        assert "BHP.AX" not in paper_portfolio.positions
        assert len(paper_portfolio.history) == 1
        assert "pnl" in paper_portfolio.history[0]

    def test_sell_nonexistent(self, paper_portfolio):
        """Selling a ticker not held raises ValueError."""
        with pytest.raises(ValueError, match="No open position"):
            paper_portfolio.place_order("FAKE.AX", "SELL", 1, 10.0)

    def test_buy_then_sell_profit(self, paper_portfolio):
        """Buy low, sell high => positive P&L."""
        paper_portfolio.place_order("BHP.AX", "BUY", 10, 40.0)
        paper_portfolio.place_order("BHP.AX", "SELL", 10, 50.0)

        pnl = paper_portfolio.history[0]["pnl"]
        assert pnl > 0, f"Expected positive P&L, got {pnl}"

    def test_buy_then_sell_loss(self, paper_portfolio):
        """Buy high, sell low => negative P&L."""
        paper_portfolio.place_order("BHP.AX", "BUY", 10, 50.0)
        paper_portfolio.place_order("BHP.AX", "SELL", 10, 40.0)

        pnl = paper_portfolio.history[0]["pnl"]
        assert pnl < 0, f"Expected negative P&L, got {pnl}"


class TestTotalValue:
    def test_total_value(self, paper_portfolio):
        """total_value() returns cash + market value of positions."""
        paper_portfolio.place_order("BHP.AX", "BUY", 10, 45.0)
        prices = {"BHP.AX": 50.0}

        total = paper_portfolio.total_value(prices)
        expected = paper_portfolio.cash + 10 * 50.0
        assert total == expected


class TestEquityHistory:
    def test_equity_history(self, paper_portfolio):
        """Equity snapshots list starts empty and can be appended."""
        assert paper_portfolio.equity_history == []
        paper_portfolio.equity_history.append({"t": "2026-01-01", "v": 1000.0})
        assert len(paper_portfolio.equity_history) == 1


class TestFeeImpact:
    def test_fees_deducted(self, paper_portfolio):
        """Trading fees reduce net returns on a round trip."""
        initial_cash = paper_portfolio.cash

        paper_portfolio.place_order("BHP.AX", "BUY", 10, 45.0)
        paper_portfolio.place_order("BHP.AX", "SELL", 10, 45.0)

        # After a round trip at the same price, cash should be LESS than
        # initial due to fees on both buy and sell legs.
        assert paper_portfolio.cash < initial_cash
        assert paper_portfolio.history[0]["fees"] > 0
