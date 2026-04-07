"""
Central configuration for the Dalio Trading System.
Loads from .env and provides typed access to all settings.
"""

from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # --- Market Data APIs (None = not configured, logs warning instead of silent fail) ---
    alpha_vantage_api_key: str | None = Field(default=None, alias="ALPHA_VANTAGE_API_KEY")
    itick_api_key: str | None = Field(default=None, alias="ITICK_API_KEY")

    # --- Macro Economic Data ---
    eodhd_api_key: str | None = Field(default=None, alias="EODHD_API_KEY")
    trading_economics_api_key: str | None = Field(default=None, alias="TRADING_ECONOMICS_API_KEY")

    # --- News APIs ---
    finnhub_api_key: str | None = Field(default=None, alias="FINNHUB_API_KEY")
    newsapi_api_key: str | None = Field(default=None, alias="NEWSAPI_API_KEY")

    # --- Optional API-based Sentiment (replaces FinBERT) ---
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    # --- Notifications ---
    discord_webhook_url: str = Field(default="", alias="DISCORD_WEBHOOK_URL")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    # --- Database ---
    # SQLite (default):   sqlite:///data/storage/trading.db
    # PostgreSQL:         postgresql://user:pass@host:5432/dalios
    database_url: str = Field(default="sqlite:///data/storage/trading.db", alias="DATABASE_URL")

    # --- Authentication (disabled by default for single-user) ---
    auth_enabled: bool = Field(default=False, alias="DALIOS_AUTH_ENABLED")
    jwt_secret: str = Field(default="", alias="DALIOS_JWT_SECRET")

    # --- Risk Parameters ---
    max_daily_loss_pct: float = Field(default=2.0, alias="MAX_DAILY_LOSS_PCT")
    max_drawdown_pct: float = Field(default=10.0, alias="MAX_DRAWDOWN_PCT")
    max_portfolio_correlation: float = Field(default=0.3, alias="MAX_PORTFOLIO_CORRELATION")
    min_diversification_assets: int = Field(default=15, alias="MIN_DIVERSIFICATION_ASSETS")
    max_pos_size_pct: float = Field(default=10.0, alias="MAX_POS_SIZE_PCT")
    max_open_positions: int = Field(default=20, alias="MAX_OPEN_POSITIONS")

    # --- Trading Mode ---
    trading_mode: str = Field(default="paper", alias="TRADING_MODE")

    # --- Paths ---
    project_root: Path = Path(__file__).parent.parent
    data_dir: Path = Path(__file__).parent.parent / "data"
    log_dir: Path = Path(__file__).parent.parent / "logs"

    # --- Dalio Parameters ---
    correlation_lookback_days: int = 252
    correlation_update_hours: int = 24
    risk_parity_rebalance_days: int = 5
    walk_forward_train_months: int = 12
    walk_forward_test_months: int = 3

    # --- Sentiment (keyword engine, no model needed) ---
    sentiment_batch_size: int = 16


@lru_cache()
def get_settings() -> Settings:
    return Settings()
