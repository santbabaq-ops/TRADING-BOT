"""Alert system for trade events — supports Email (SMTP) and Telegram."""

import logging
import os
import smtplib
import time
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

logger = logging.getLogger(__name__)

# Cooldown defaults (seconds)
DEFAULT_TRADE_COOLDOWN = 900   # 15 min between trade alerts
DEFAULT_DAILY_COOLDOWN = 3600  # 1h between daily PnL alerts
DEFAULT_SYSTEM_COOLDOWN = 0    # no cooldown for system alerts


class Alerter:
    """Send alerts on trade events via Email and/or Telegram, with anti-spam cooldown."""

    def __init__(
        self,
        loss_threshold: float = 5.0,
        gain_threshold: float = 10.0,
        daily_loss_threshold: float = 20.0,
        daily_gain_threshold: float = 50.0,
    ):
        # Thresholds (in $)
        self.loss_threshold = loss_threshold
        self.gain_threshold = gain_threshold
        self.daily_loss_threshold = daily_loss_threshold
        self.daily_gain_threshold = daily_gain_threshold

        # Email config
        self._smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self._smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self._smtp_user = os.getenv("SMTP_USER", "")
        self._smtp_pass = os.getenv("SMTP_PASSWORD", "")
        self._alert_to = os.getenv("ALERT_EMAIL_TO", self._smtp_user)
        self._email_enabled = bool(self._smtp_user) and bool(self._smtp_pass)

        # Telegram config
        self._tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self._tg_enabled = bool(self._tg_token) and bool(self._tg_chat_id)

        self.enabled = self._email_enabled or self._tg_enabled

        # Anti-spam: last send time per alert type
        self._last_sent: dict[str, float] = {}
        self._lock = threading.Lock()

        channels = []
        if self._email_enabled:
            channels.append(f"Email ({self._alert_to})")
        if self._tg_enabled:
            channels.append(f"Telegram ({self._tg_chat_id})")
        if channels:
            logger.info("Alerts enabled: %s", ", ".join(channels))
        else:
            logger.info("Alerts disabled (no Email or Telegram configured)")

    def _can_send(self, alert_type: str, cooldown: int) -> bool:
        """Check cooldown for an alert type."""
        now = time.time()
        with self._lock:
            last = self._last_sent.get(alert_type, 0)
            if now - last < cooldown:
                return False
            self._last_sent[alert_type] = now
            return True

    def _send_email(self, subject: str, body: str) -> bool:
        """Send an email via SMTP."""
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self._smtp_user
            msg["To"] = self._alert_to
            msg["Subject"] = f"[Polymarket Bot] {subject}"

            # Plain text version
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as server:
                server.starttls()
                server.login(self._smtp_user, self._smtp_pass)
                server.sendmail(self._smtp_user, self._alert_to, msg.as_string())

            logger.info("Email alert sent to %s", self._alert_to)
            return True
        except Exception as e:
            logger.error("Email send failed: %s", e)
            return False

    def _send_telegram(self, message: str) -> bool:
        """Send a message via Telegram Bot API."""
        try:
            url = f"https://api.telegram.org/bot{self._tg_token}/sendMessage"
            resp = requests.post(url, json={
                "chat_id": self._tg_chat_id,
                "text": message,
                "parse_mode": "HTML",
            }, timeout=10)
            if resp.ok:
                logger.info("Telegram alert sent")
                return True
            logger.warning("Telegram API error: %s", resp.text)
            return False
        except Exception as e:
            logger.error("Telegram send failed: %s", e)
            return False

    def _send(self, subject: str, body: str) -> bool:
        """Send alert via all enabled channels."""
        if not self.enabled:
            return False
        sent = False
        if self._email_enabled:
            sent = self._send_email(subject, body) or sent
        if self._tg_enabled:
            sent = self._send_telegram(body) or sent
        return sent

    # --- Public API ---

    def check_trade(self, strategy: str, side: str, price: float, size: float, pnl: float) -> None:
        """Check if a trade triggers an alert."""
        if not self.enabled or pnl == 0:
            return

        if pnl < 0 and abs(pnl) >= self.loss_threshold:
            if self._can_send("trade_loss", DEFAULT_TRADE_COOLDOWN):
                self._send(
                    f"PERTE -{abs(pnl):.2f}$",
                    f"<b>PERTE</b> -{abs(pnl):.2f}$\n"
                    f"Strategie: {strategy}\n"
                    f"Action: {side} @ {price:.4f}\n"
                    f"Taille: {size:.2f}$"
                )

        elif pnl > 0 and pnl >= self.gain_threshold:
            if self._can_send("trade_gain", DEFAULT_TRADE_COOLDOWN):
                self._send(
                    f"GAIN +{pnl:.2f}$",
                    f"<b>GAIN</b> +{pnl:.2f}$\n"
                    f"Strategie: {strategy}\n"
                    f"Action: {side} @ {price:.4f}\n"
                    f"Taille: {size:.2f}$"
                )

    def check_daily_pnl(self, daily_pnl: float) -> None:
        """Check if daily PnL triggers an alert."""
        if not self.enabled:
            return

        if daily_pnl <= -self.daily_loss_threshold:
            if self._can_send("daily_loss", DEFAULT_DAILY_COOLDOWN):
                self._send(
                    f"ALERTE Perte journaliere {daily_pnl:.2f}$",
                    f"<b>ALERTE PERTE JOURNALIERE</b>\n"
                    f"PnL du jour: {daily_pnl:.2f}$\n"
                    f"Seuil: -{self.daily_loss_threshold:.0f}$"
                )

        elif daily_pnl >= self.daily_gain_threshold:
            if self._can_send("daily_gain", DEFAULT_DAILY_COOLDOWN):
                self._send(
                    f"Objectif journalier atteint +{daily_pnl:.2f}$",
                    f"<b>OBJECTIF JOURNALIER ATTEINT</b>\n"
                    f"PnL du jour: +{daily_pnl:.2f}$\n"
                    f"Seuil: +{self.daily_gain_threshold:.0f}$"
                )

    def notify_level_change(self, direction: str, old_size: float, new_size: float, reason: str) -> None:
        """Alert on scaler level up/down."""
        if not self.enabled:
            return
        if self._can_send("level_change", DEFAULT_SYSTEM_COOLDOWN):
            label = "LEVEL UP" if direction == "up" else "LEVEL DOWN"
            self._send(
                f"{label}: {old_size:.0f}$ -> {new_size:.0f}$",
                f"<b>{label}</b>\n"
                f"{old_size:.0f}$ -> {new_size:.0f}$\n"
                f"Raison: {reason}"
            )

    def notify_bot_error(self, bot_name: str, error: str) -> None:
        """Alert on bot fatal error."""
        if not self.enabled:
            return
        if self._can_send(f"error_{bot_name}", DEFAULT_SYSTEM_COOLDOWN):
            self._send(
                f"ERREUR Bot {bot_name}",
                f"<b>ERREUR BOT</b>\n"
                f"Bot: {bot_name}\n"
                f"Erreur: {error}"
            )

    def notify_kill_all(self, bots: list[str]) -> None:
        """Alert when kill-all is triggered."""
        if not self.enabled:
            return
        if self._can_send("kill_all", DEFAULT_SYSTEM_COOLDOWN):
            self._send(
                "ARRET D'URGENCE",
                f"<b>ARRET D'URGENCE</b>\n"
                f"Bots arretes: {', '.join(bots) if bots else 'aucun'}"
            )

    def send_test(self) -> bool:
        """Send a test message to verify configuration."""
        return self._send(
            "Test alerte",
            "Test alerte Polymarket RBI Bot — configuration OK"
        )
