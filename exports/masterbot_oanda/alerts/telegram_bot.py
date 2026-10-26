import os
import requests


class TelegramAlertBot:

    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if not self.token:
            raise ValueError("Missing TELEGRAM_BOT_TOKEN")

        if not self.chat_id:
            raise ValueError("Missing TELEGRAM_CHAT_ID")

        self.url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    def send(self, message: str):

        payload = {
            "chat_id": self.chat_id,
            "text": message
        }

        r = requests.post(self.url, json=payload, timeout=10)

        if r.status_code != 200:
            raise RuntimeError(f"Telegram send failed: {r.text}")
