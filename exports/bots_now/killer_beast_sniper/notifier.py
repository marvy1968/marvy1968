import os, requests, logging
from dotenv import load_dotenv

ENV_FILE = '/home/moonrider1968/bots/killer_beast_sniper/.env'

def send_alert(message: str, title: str = "🤖 TRADING BOT ALERT"):
    load_dotenv(ENV_FILE, override=True)
    discord_webhook = os.getenv('DISCORD_WEBHOOK_URL', '').strip()
    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()

    # 1. Discord Notification
    if discord_webhook:
        try:
            payload = {
                "username": "Alpha Sniper Daemon",
                "content": f"**{title}**\n{message}"
            }
            requests.post(discord_webhook, json=payload, timeout=5)
        except Exception as e:
            logging.warning(f"Failed to dispatch Discord alert: {e}")

    # 2. Telegram Notification
    if telegram_token and telegram_chat_id:
        try:
            tg_url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
            payload = {
                "chat_id": telegram_chat_id,
                "text": f"*{title}*\n{message}",
                "parse_mode": "Markdown"
            }
            requests.post(tg_url, json=payload, timeout=5)
        except Exception as e:
            logging.warning(f"Failed to dispatch Telegram alert: {e}")
