"""
Dalio Autonomous Trading System — Main Entry Point.

Starts the APScheduler job scheduler with all recurring tasks:

  Every  5 min  : Day-trading signal scan (market hours)
  Every  4 hours: Swing-trading signal scan + sentiment scan
  Every 24 hours: Correlation matrix refresh + portfolio rebalance
  Every  1 hour : Portfolio health report → Discord/Telegram
  Weekly (Mon)  : Walk-forward backtest run

Usage:
    python main.py                  # Live scheduler (paper mode by default)
    python main.py --cycle          # Run a single cycle and exit
    python main.py --backtest       # Run walk-forward backtest and exit
    python main.py --sentiment      # Run sentiment scan and exit
    python main.py --health         # Run health report and exit
"""

import argparse
import sys
from datetime import datetime
from loguru import logger

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from data.storage.models import init_db
from agents.dalio_agent import DalioAgent
from notifications.notifier import NotificationManager
from config.settings import get_settings


# ------------------------------------------------------------------
# Setup
# ------------------------------------------------------------------

def configure_logging():
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
               "<level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{line}</cyan> — {message}",
        level="INFO",
        colorize=True,
    )
    logger.add(
        "logs/dalio_{time:YYYY-MM-DD}.log",
        rotation="00:00",      # New file each day
        retention="30 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} — {message}",
    )


def build_system() -> tuple[DalioAgent, NotificationManager]:
    """Construct and wire the agent + notifier."""
    settings = get_settings()
    notifier = NotificationManager()
    agent = DalioAgent(initial_equity=100_000.0)
    agent.attach_notifier(notifier)
    return agent, notifier


# ------------------------------------------------------------------
# Scheduled Jobs
# ------------------------------------------------------------------

def job_day_trading_scan(agent: DalioAgent):
    """5-minute intraday scan during market hours."""
    logger.info("⚡ [JOB] Day-trading scan started")
    try:
        agent.run_cycle()
    except Exception as e:
        logger.error(f"Day-trading scan failed: {e}")


def job_swing_scan(agent: DalioAgent):
    """4-hour swing signal + sentiment scan."""
    logger.info("🔄 [JOB] Swing trading scan + sentiment alert")
    try:
        agent.run_cycle()
        agent.run_sentiment_alert()
    except Exception as e:
        logger.error(f"Swing scan failed: {e}")


def job_correlation_refresh(agent: DalioAgent):
    """24-hour correlation matrix + risk-parity rebalance."""
    logger.info("🔗 [JOB] Correlation refresh + portfolio rebalance")
    try:
        agent._refresh_correlations()
        agent._refresh_weights()
    except Exception as e:
        logger.error(f"Correlation refresh failed: {e}")


def job_health_report(agent: DalioAgent):
    """Hourly portfolio health report."""
    logger.info("📋 [JOB] Portfolio health report")
    try:
        agent.run_portfolio_health_report()
    except Exception as e:
        logger.error(f"Health report failed: {e}")


def job_weekly_backtest(agent: DalioAgent):
    """Weekly walk-forward validation."""
    logger.info("📈 [JOB] Weekly walk-forward backtest")
    try:
        agent.run_walk_forward_backtest()
    except Exception as e:
        logger.error(f"Walk-forward backtest failed: {e}")


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def run_scheduler(agent: DalioAgent):
    """Start the blocking APScheduler."""
    settings = get_settings()
    scheduler = BlockingScheduler(timezone="Australia/Sydney")

    # Day-trading scan: every 5 minutes, Mon–Fri, ASX hours (10:00–16:00 AEST)
    scheduler.add_job(
        job_day_trading_scan,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="10-15",
            minute="*/5",
            timezone="Australia/Sydney",
        ),
        args=[agent],
        id="day_trading_scan",
        name="Day Trading Signal Scan (5min)",
        max_instances=1,
        coalesce=True,
    )

    # Swing scan: every 4 hours
    scheduler.add_job(
        job_swing_scan,
        trigger=IntervalTrigger(hours=4),
        args=[agent],
        id="swing_scan",
        name="Swing Trading + Sentiment (4h)",
        max_instances=1,
        coalesce=True,
    )

    # Correlation + weights: every 24 hours
    scheduler.add_job(
        job_correlation_refresh,
        trigger=IntervalTrigger(hours=24),
        args=[agent],
        id="correlation_refresh",
        name="Correlation Matrix Refresh (24h)",
        max_instances=1,
        coalesce=True,
    )

    # Health report: every hour
    scheduler.add_job(
        job_health_report,
        trigger=IntervalTrigger(hours=1),
        args=[agent],
        id="health_report",
        name="Portfolio Health Report (1h)",
        max_instances=1,
        coalesce=True,
    )

    # Weekly backtest: every Monday at 07:00 AEST
    scheduler.add_job(
        job_weekly_backtest,
        trigger=CronTrigger(
            day_of_week="mon",
            hour=7,
            minute=0,
            timezone="Australia/Sydney",
        ),
        args=[agent],
        id="weekly_backtest",
        name="Walk-Forward Backtest (Weekly)",
        max_instances=1,
        coalesce=True,
    )

    logger.info("=" * 60)
    logger.info("  DALIO SYSTEM SCHEDULER ACTIVE")
    logger.info(f"  Mode: {settings.trading_mode.upper()}")
    logger.info(f"  Jobs: {len(scheduler.get_jobs())}")
    logger.info("=" * 60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped by user.")
        scheduler.shutdown()


def main():
    parser = argparse.ArgumentParser(description="Dalio Autonomous Trading System")
    parser.add_argument("--cycle",     action="store_true", help="Run one cycle and exit")
    parser.add_argument("--backtest",  action="store_true", help="Run walk-forward backtest and exit")
    parser.add_argument("--sentiment", action="store_true", help="Run sentiment scan and exit")
    parser.add_argument("--health",    action="store_true", help="Run health report and exit")
    parser.add_argument("--equity",    type=float, default=100_000.0, help="Starting equity (default 100000)")
    args = parser.parse_args()

    configure_logging()
    logger.info("Dalio Autonomous Trading System starting...")

    # Database
    init_db()

    # Build system
    agent, notifier = build_system()
    agent.initial_equity = args.equity

    # One-shot modes
    if args.cycle:
        agent.boot()
        result = agent.run_cycle()
        logger.info(f"Cycle result: {result.get('type')} | Quadrant: {result.get('quadrant')}")
        sys.exit(0)

    if args.backtest:
        agent.boot()
        summary = agent.run_walk_forward_backtest()
        logger.info(f"Backtest complete: {summary}")
        sys.exit(0)

    if args.sentiment:
        agent.sentiment_engine.load_model()
        result = agent.run_sentiment_alert()
        logger.info(f"Sentiment: {result}")
        sys.exit(0)

    if args.health:
        report = agent.run_portfolio_health_report()
        logger.info(f"Health: {report}")
        sys.exit(0)

    # Default: boot and run scheduler
    agent.boot()
    run_scheduler(agent)


if __name__ == "__main__":
    main()
