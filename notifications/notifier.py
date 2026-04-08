"""
Notification Manager — Discord & Telegram webhooks.

Sends:
  - Trade suggestions (BUY/SELL/SHORT) with full Systematic Justification
  - Sentiment alerts (conflict risk, quadrant shifts)
  - Portfolio health reports (equity, P&L, drawdown, Sharpe)
  - Circuit breaker alerts (CRITICAL)
  - Walk-forward backtest summaries
  - New opportunity suggestions
"""

import json
import requests
from datetime import datetime
from loguru import logger
from typing import Optional

from config.settings import get_settings


# Discord embed colour codes
COLORS = {
    "BUY": 0x2ECC71,       # Green
    "SELL": 0xE74C3C,      # Red
    "SHORT": 0xE67E22,     # Orange
    "COVER": 0x3498DB,     # Blue
    "ALERT": 0xF39C12,     # Yellow
    "CIRCUIT_BREAKER": 0xFF0000,  # Bright Red
    "HEALTH": 0x9B59B6,    # Purple
    "BACKTEST": 0x1ABC9C,  # Teal
    "DEFAULT": 0x95A5A6,   # Grey
}


class NotificationManager:
    """Routes all system alerts to Discord and/or Telegram."""

    def __init__(self):
        self.settings = get_settings()

    # ------------------------------------------------------------------
    # Main Dispatch
    # ------------------------------------------------------------------

    def send(self, data: dict):
        """
        Dispatch a notification based on data type.
        Handles all message types produced by the Dalio agent.
        """
        msg_type = data.get("type", "DEFAULT")
        try:
            if msg_type == "CIRCUIT_BREAKER":
                self._send_circuit_breaker_alert(data)
            elif msg_type == "CYCLE_COMPLETE":
                self._send_cycle_summary(data)
            elif msg_type == "SENTIMENT_ALERT":
                self._send_sentiment_alert(data)
            elif msg_type == "HEALTH_REPORT":
                self._send_health_report(data)
            elif msg_type == "BACKTEST_REPORT":
                self._send_backtest_report(data)
            else:
                self._send_raw(str(data)[:1800])
        except Exception as e:
            logger.error(f"Notification dispatch failed: {e}")

    def send_trade_signal(self, signal_dict: dict):
        """Send a single trade signal alert. Only sends if confidence >= 60%."""
        confidence = signal_dict.get("confidence", 0)
        # Normalize: if confidence looks like a percentage (>1), convert to fraction
        if confidence > 1:
            confidence = confidence / 100.0
        if confidence < 0.60:
            return  # Skip low-confidence signals
        action = signal_dict.get("action", "HOLD")
        ticker = signal_dict.get("ticker", "?")
        color = COLORS.get(action, COLORS["DEFAULT"])

        emoji = {"BUY": "🟢", "SELL": "🔴", "SHORT": "🟠", "COVER": "🔵"}.get(action, "⚪")

        fields = [
            {"name": "Action", "value": f"{emoji} **{action}**", "inline": True},
            {"name": "Price", "value": f"${signal_dict.get('price', 0):.4f}", "inline": True},
            {"name": "Confidence", "value": f"{signal_dict.get('confidence', 0):.1%}", "inline": True},
            {"name": "Quadrant Fit", "value": signal_dict.get("quadrant_fit", "?").upper(), "inline": True},
            {"name": "Sentiment", "value": signal_dict.get("sentiment", "neutral").capitalize(), "inline": True},
            {"name": "RSI", "value": str(signal_dict.get("rsi", "?")), "inline": True},
            {"name": "Trend", "value": signal_dict.get("trend", "?").capitalize(), "inline": True},
            {"name": "Stop Loss", "value": f"${signal_dict.get('stop_loss', 0):.4f}", "inline": True},
            {"name": "Take Profit", "value": f"${signal_dict.get('take_profit', 0):.4f}", "inline": True},
            {"name": "R/R Ratio", "value": f"{signal_dict.get('rr_ratio', 0):.2f}x", "inline": True},
            {"name": "Position Size", "value": f"{signal_dict.get('position_size_pct', 0):.2f}% of portfolio", "inline": True},
        ]

        if signal_dict.get("options_strategy"):
            fields.append({
                "name": "Options Strategy",
                "value": signal_dict["options_strategy"],
                "inline": False,
            })

        reasons = signal_dict.get("reasons", [])
        if reasons:
            fields.append({
                "name": "Signal Reasons",
                "value": "\n".join(f"• {r}" for r in reasons[:5]),
                "inline": False,
            })

        embed = {
            "title": f"Trade Signal: {ticker}",
            "color": color,
            "fields": fields,
            "footer": {"text": "Dalio AI — All Weather System"},
            "timestamp": datetime.utcnow().isoformat(),
        }
        self._discord_embed(embed)
        self._telegram_text(self._signal_to_telegram(signal_dict))

    # ------------------------------------------------------------------
    # Specific message builders
    # ------------------------------------------------------------------

    def _send_cycle_summary(self, data: dict):
        quadrant = data.get("quadrant", "unknown").upper().replace("_", " ")
        conflict = "⚠ ELEVATED" if data.get("conflict_risk") else "Normal"
        health = data.get("portfolio_health", {})

        embed = {
            "title": f"📊 Dalio Cycle #{data.get('cycle', '?')} Complete",
            "color": COLORS["HEALTH"],
            "description": data.get("quadrant_description", ""),
            "fields": [
                {"name": "Economic Quadrant", "value": quadrant, "inline": True},
                {"name": "Conflict Risk", "value": conflict, "inline": True},
                {"name": "Signals Found", "value": str(data.get("signals_found", 0)), "inline": True},
                {"name": "Orders Executed", "value": str(data.get("orders_executed", 0)), "inline": True},
                {"name": "New Opportunities", "value": str(data.get("new_opportunities", 0)), "inline": True},
                {"name": "Portfolio Equity", "value": f"${health.get('equity', 0):,.2f}", "inline": True},
                {"name": "Daily P&L", "value": f"{health.get('daily_pnl_pct', 0):.2f}%", "inline": True},
                {"name": "Drawdown", "value": f"{health.get('drawdown_pct', 0):.2f}%", "inline": True},
                {"name": "Open Positions", "value": str(health.get("open_positions", 0)), "inline": True},
                {"name": "Dalio Diversification", "value": "✅ MET" if health.get("dalio_diversification_met") else "❌ BELOW 15", "inline": True},
            ],
            "footer": {"text": "Dalio AI — All Weather System"},
            "timestamp": datetime.utcnow().isoformat(),
        }
        self._discord_embed(embed)

        # Also send top signals individually
        for signal_dict in data.get("top_signals", [])[:3]:
            self.send_trade_signal(signal_dict)

        # New opportunities (only 60%+ confidence)
        ops = [o for o in data.get("new_opportunities_detail", [])
               if (o.get('confidence', 0) if o.get('confidence', 0) <= 1 else o.get('confidence', 0) / 100) >= 0.60]
        if ops:
            self._telegram_text(
                "🔍 *New Opportunities Identified:*\n" +
                "\n".join(
                    f"• {o['ticker']} — {o['action']} @ ${o['price']:.4f} "
                    f"(conf: {o['confidence']:.0%})"
                    for o in ops
                )
            )

    def _send_circuit_breaker_alert(self, data: dict):
        embed = {
            "title": "🚨 CIRCUIT BREAKER TRIPPED — TRADING HALTED",
            "color": COLORS["CIRCUIT_BREAKER"],
            "description": data.get("message", "Risk limit breached."),
            "fields": [
                {"name": "Timestamp", "value": data.get("timestamp", "?"), "inline": False},
            ],
            "footer": {"text": "Dalio AI — Emergency Stop"},
        }
        self._discord_embed(embed)
        self._telegram_text(
            f"🚨 *CIRCUIT BREAKER TRIPPED*\n{data.get('message', '')}"
        )

    def _send_sentiment_alert(self, data: dict):
        embed = {
            "title": "⚠ Sentiment Alert — Elevated Risk Detected",
            "color": COLORS["ALERT"],
            "description": data.get("message", ""),
            "fields": [
                {"name": "Dominant Quadrant", "value": data.get("dominant_quadrant", "?"), "inline": True},
                {"name": "Timestamp", "value": data.get("timestamp", "?"), "inline": True},
            ],
            "footer": {"text": "Dalio AI — Sentiment Engine"},
        }
        self._discord_embed(embed)
        self._telegram_text(f"⚠ *Sentiment Alert*\n{data.get('message', '')}")

    def _send_health_report(self, data: dict):
        cb = data.get("circuit_breaker", {}) or data
        sharpe = data.get("sharpe_info", {})
        corr = data.get("correlation_stats", {})

        fields = [
            {"name": "Equity", "value": f"${data.get('equity', 0):,.2f}", "inline": True},
            {"name": "Daily P&L", "value": f"{data.get('daily_pnl_pct', 0):.2f}%", "inline": True},
            {"name": "Drawdown", "value": f"{data.get('drawdown_pct', 0):.2f}%", "inline": True},
            {"name": "Open Positions", "value": str(data.get("open_positions", 0)), "inline": True},
        ]
        if sharpe:
            fields.append({
                "name": "Portfolio Sharpe",
                "value": str(sharpe.get("portfolio_sharpe", "N/A")),
                "inline": True,
            })
        if corr:
            fields.append({
                "name": "Avg Correlation",
                "value": str(corr.get("avg_correlation", "N/A")),
                "inline": True,
            })
            fields.append({
                "name": "Dalio Rule Met",
                "value": "✅" if corr.get("meets_dalio_rule") else "❌",
                "inline": True,
            })

        embed = {
            "title": "📋 Portfolio Health Report",
            "color": COLORS["HEALTH"],
            "fields": fields,
            "footer": {"text": "Dalio AI — All Weather System"},
            "timestamp": data.get("timestamp", datetime.utcnow().isoformat()),
        }
        self._discord_embed(embed)

    def _send_backtest_report(self, data: dict):
        embed = {
            "title": "📈 Walk-Forward Backtest Results",
            "color": COLORS["BACKTEST"],
            "fields": [
                {"name": "Windows Tested", "value": str(data.get("windows_tested", 0)), "inline": True},
                {"name": "Avg Sharpe", "value": str(data.get("avg_sharpe_ratio", 0)), "inline": True},
                {"name": "Avg Max Drawdown", "value": f"{data.get('avg_max_drawdown_pct', 0):.2f}%", "inline": True},
                {"name": "Total Compound Return", "value": f"{data.get('total_compound_return_pct', 0):.2f}%", "inline": True},
                {"name": "Consistency Score", "value": f"{data.get('consistency_score_pct', 0):.1f}% windows profitable", "inline": True},
            ],
            "footer": {"text": "Dalio AI — Backtesting Engine"},
            "timestamp": datetime.utcnow().isoformat(),
        }
        self._discord_embed(embed)

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _discord_embed(self, embed: dict):
        """Post an embed to Discord webhook."""
        url = self.settings.discord_webhook_url
        if not url:
            logger.debug("Discord webhook not configured — skipping.")
            return

        payload = {"embeds": [embed]}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code not in (200, 204):
                logger.warning(f"Discord webhook returned {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Discord send failed: {e}")

    def _telegram_text(self, text: str):
        """Send a Markdown message via Telegram Bot API."""
        token = self.settings.telegram_bot_token
        chat_id = self.settings.telegram_chat_id
        if not token or not chat_id:
            logger.debug("Telegram not configured — skipping.")
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": "Markdown",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"Telegram returned {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")

    def _send_raw(self, text: str):
        self._discord_embed({"title": "System Message", "description": text[:2048], "color": COLORS["DEFAULT"]})
        self._telegram_text(text[:4096])

    @staticmethod
    def _signal_to_telegram(s: dict) -> str:
        emoji = {"BUY": "🟢", "SELL": "🔴", "SHORT": "🟠", "COVER": "🔵"}.get(s.get("action", ""), "⚪")
        return (
            f"{emoji} *{s.get('action', '?')} — {s.get('ticker', '?')}*\n"
            f"Price: ${s.get('price', 0):.4f}\n"
            f"Confidence: {s.get('confidence', 0):.0%}\n"
            f"R/R: {s.get('rr_ratio', 0):.2f}x | Stop: ${s.get('stop_loss', 0):.4f}\n"
            f"Quadrant Fit: {s.get('quadrant_fit', '?').upper()}\n"
            f"Sentiment: {s.get('sentiment', 'neutral').capitalize()}"
        )
