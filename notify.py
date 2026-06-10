import logging
from typing import Optional

import requests

from config import Config

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self):
        self.enabled = Config.TELEGRAM_ENABLED
        if self.enabled:
            self._test_connection()

    def _test_connection(self):
        try:
            self.send("Bot started")
            logger.info("Telegram notifier connected")
        except Exception as e:
            logger.warning("Telegram notifier failed: %s", e)
            self.enabled = False

    def send(self, message: str):
        if not self.enabled:
            logger.debug("[Telegram] %s", message)
            return

        url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": Config.TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to send Telegram: %s", e)

    def send_order(self, side: str, symbol: str, amount: float, price: float, order_id: str = ""):
        msg = (
            f"<b>Order {side.upper()}</b>\n"
            f"Symbol: {symbol}\n"
            f"Amount: {amount:.6f}\n"
            f"Price: {price:.2f}\n"
            f"Value: ${amount * price:.2f}\n"
        )
        if order_id:
            msg += f"ID: {order_id}\n"
        self.send(msg)

    def send_signal(self, symbol: str, signal: str, price: float, reason: str = ""):
        msg = (
            f"<b>Signal: {signal.upper()}</b>\n"
            f"Symbol: {symbol}\n"
            f"Price: {price:.2f}\n"
        )
        if reason:
            msg += f"Reason: {reason}\n"
        self.send(msg)

    def send_error(self, error: str):
        self.send(f"<b>Error</b>\n{error}")

    def send_report(self, text: str):
        self.send(f"<b>Daily Report</b>\n{text}")
