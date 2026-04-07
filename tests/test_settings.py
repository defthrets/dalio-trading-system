"""Tests for config/settings.py defaults."""

import pytest
import os
from unittest.mock import patch


class TestDefaultSettings:
    def test_default_settings(self):
        """Default settings load with expected values."""
        # Create a fresh instance to avoid .env interference
        from config.settings import Settings
        s = Settings(
            _env_file=None,  # skip .env loading
        )
        assert s.trading_mode == "paper"
        assert s.max_daily_loss_pct == 2.0
        assert s.max_drawdown_pct == 10.0
        assert s.max_pos_size_pct == 10.0
        assert s.max_open_positions == 20
        assert s.correlation_lookback_days == 252

    def test_optional_api_keys(self):
        """API keys default to None when not set."""
        from config.settings import Settings
        s = Settings(
            _env_file=None,
        )
        assert s.alpha_vantage_api_key is None
        assert s.itick_api_key is None
        assert s.eodhd_api_key is None
        assert s.finnhub_api_key is None
        assert s.newsapi_api_key is None

    def test_database_url_default(self):
        """Database URL has a sensible default."""
        from config.settings import Settings
        s = Settings(_env_file=None)
        assert "sqlite" in s.database_url
