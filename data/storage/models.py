"""
SQLAlchemy Database Models.

Tables:
  - orders          : All trade orders (paper + live)
  - signals         : Generated trade signals with justification
  - portfolio       : Current open positions
  - macro_snapshots : Historical quadrant classifications
  - sentiment_logs  : FinBERT sentiment results by date
  - backtest_results: Walk-forward window results
  - price_cache     : Local OHLCV cache to reduce API calls
"""

from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, Float, String,
    Boolean, DateTime, Text, JSON, Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from loguru import logger

from config.settings import get_settings

Base = declarative_base()


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(16), unique=True, nullable=False, index=True)
    ticker = Column(String(20), nullable=False, index=True)
    action = Column(String(10), nullable=False)        # BUY | SELL | SHORT | COVER
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    fill_price = Column(Float, nullable=True)
    order_type = Column(String(10), default="market")
    status = Column(String(20), nullable=False, index=True)  # pending|filled|rejected
    pnl = Column(Float, nullable=True)
    justification = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    filled_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_orders_ticker_date", "ticker", "created_at"),)


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    action = Column(String(10), nullable=False)
    direction = Column(String(10))
    confidence = Column(Float)
    price = Column(Float)
    quadrant = Column(String(30))
    quadrant_fit = Column(String(20))
    sentiment_score = Column(Float)
    sentiment_label = Column(String(20))
    conflict_risk = Column(Boolean, default=False)
    rsi = Column(Float)
    macd_signal = Column(String(20))
    bb_position = Column(String(30))
    trend = Column(String(20))
    atr = Column(Float)
    suggested_stop_loss = Column(Float)
    suggested_take_profit = Column(Float)
    risk_reward_ratio = Column(Float)
    position_size_pct = Column(Float)
    options_strategy = Column(Text, nullable=True)
    reasons = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (Index("ix_signals_ticker_date", "ticker", "created_at"),)


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, unique=True, index=True)
    direction = Column(String(10))        # long | short
    quantity = Column(Float)
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    current_price = Column(Float, nullable=True)
    unrealised_pnl = Column(Float, nullable=True)
    weight_in_portfolio = Column(Float, nullable=True)
    opened_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MacroSnapshot(Base):
    __tablename__ = "macro_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    quadrant = Column(String(30), nullable=False, index=True)
    macro_quadrant = Column(String(30))
    sentiment_quadrant = Column(String(30))
    gdp_value = Column(Float, nullable=True)
    gdp_trend = Column(String(10))
    cpi_value = Column(Float, nullable=True)
    cpi_trend = Column(String(10))
    interest_rate = Column(Float, nullable=True)
    unemployment = Column(Float, nullable=True)
    conflict_risk_elevated = Column(Boolean, default=False)
    favoured_assets = Column(JSON)
    avoid_assets = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class SentimentLog(Base):
    __tablename__ = "sentiment_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=True, index=True)    # NULL = market-wide
    sentiment = Column(String(20))
    score = Column(Float)
    quadrant = Column(String(30))
    conflict_risk = Column(Boolean, default=False)
    article_count = Column(Integer, default=0)
    dominant_quadrant = Column(String(30), nullable=True)
    raw_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), nullable=False, index=True)  # UUID per run
    window_id = Column(Integer)
    train_start = Column(String(12))
    train_end = Column(String(12))
    test_start = Column(String(12))
    test_end = Column(String(12))
    total_return_pct = Column(Float)
    sharpe_ratio = Column(Float)
    sortino_ratio = Column(Float)
    max_drawdown_pct = Column(Float)
    win_rate_pct = Column(Float)
    profit_factor = Column(Float)
    cagr_pct = Column(Float)
    total_trades = Column(Integer)
    avg_trade_pct = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


class Trade(Base):
    """Closed paper-trade record."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)   # BUY / SELL
    qty = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    fees = Column(Float, default=0)
    pnl = Column(Float, nullable=True)          # only on SELL
    pnl_pct = Column(Float, nullable=True)
    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (Index("ix_trades_ticker_ts", "ticker", "timestamp"),)


class EquitySnapshot(Base):
    """Point-in-time portfolio equity value."""
    __tablename__ = "equity_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    value = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)


class RealEquitySnapshot(Base):
    """Point-in-time live broker equity value (persists across restarts)."""
    __tablename__ = "real_equity_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    value = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)


class PaperPosition(Base):
    """Current open paper-trading position (mirrors PAPER.positions dict)."""
    __tablename__ = "paper_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, unique=True, index=True)
    side = Column(String(10), nullable=False)
    qty = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    entry_time = Column(DateTime, default=datetime.utcnow)


class PriceCache(Base):
    __tablename__ = "price_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    date = Column(String(12), nullable=False)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)

    __table_args__ = (
        Index("ix_price_cache_ticker_date", "ticker", "date", unique=True),
    )


class PortfolioSnapshot(Base):
    """Daily snapshot of portfolio weights and metrics."""
    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    equity = Column(Float)
    daily_pnl_pct = Column(Float)
    drawdown_pct = Column(Float)
    sharpe_ratio = Column(Float, nullable=True)
    avg_correlation = Column(Float, nullable=True)
    open_positions = Column(Integer)
    weights = Column(JSON)
    quadrant = Column(String(30))
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ------------------------------------------------------------------
# Database setup
# ------------------------------------------------------------------

def get_engine():
    settings = get_settings()
    url = settings.database_url
    kwargs = {"echo": False}

    # SQLite needs check_same_thread=False; PostgreSQL doesn't
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # PostgreSQL connection pool settings for production
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
        kwargs["pool_pre_ping"] = True

    engine = create_engine(url, **kwargs)
    return engine


def init_db():
    """Create all tables if they don't exist."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    logger.info("Database tables initialised.")
    return engine


def get_session():
    """Return a new database session."""
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    return Session()
