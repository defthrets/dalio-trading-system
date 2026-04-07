"""Tests for FastAPI REST endpoints using TestClient."""

import pytest
from unittest.mock import patch, AsyncMock


@pytest.fixture(autouse=True)
def _reset_paper_portfolio():
    """Reset the global PAPER portfolio before each test."""
    from api.server import PAPER
    PAPER.reset()
    yield
    PAPER.reset()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from api.server import app
    return TestClient(app)


class TestStatusEndpoints:
    def test_status_endpoint(self, client):
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert "mode" in data

    def test_mode_endpoint(self, client):
        r = client.get("/api/mode")
        assert r.status_code == 200
        assert r.json()["mode"] in ("paper", "live")


class TestModeSwitch:
    def test_set_mode_paper(self, client):
        r = client.post("/api/mode", json={"mode": "paper"})
        assert r.status_code == 200
        assert r.json()["mode"] == "paper"

    def test_invalid_mode(self, client):
        r = client.post("/api/mode", json={"mode": "invalid"})
        assert r.status_code == 400


class TestPaperOrderEndpoint:
    @patch("api.server._prices_for_positions", new_callable=AsyncMock, return_value={})
    def test_paper_order_buy(self, mock_prices, client):
        r = client.post(
            "/api/paper/order",
            json={"ticker": "TEST.AX", "side": "BUY", "qty": 10, "price": 50.0},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["side"] == "BUY"
        assert data["ticker"] == "TEST.AX"

    @patch("api.server._prices_for_positions", new_callable=AsyncMock, return_value={})
    def test_paper_order_sell_no_position(self, mock_prices, client):
        r = client.post(
            "/api/paper/order",
            json={"ticker": "NONE.AX", "side": "SELL", "qty": 1, "price": 10.0},
        )
        assert r.status_code == 400


class TestHealthEndpoint:
    def test_health_endpoint(self, client):
        r = client.get("/api/portfolio/health")
        assert r.status_code == 200
