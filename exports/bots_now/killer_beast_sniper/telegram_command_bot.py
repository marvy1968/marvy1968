import generate_dynamic_reportcard
import os
import sys
import time
import requests
import subprocess
import logging
from dotenv import load_dotenv

load_dotenv("/home/moonrider1968/bots/killer_beast_sniper/.env")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "REDACTED_TELEGRAM_TOKEN").strip()
AUTH_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7922925421").strip()
API_URL = f"https://api.telegram.org/bot{TOKEN}"

LOG_FILE = "/home/moonrider1968/bots/killer_beast_sniper/logs/telegram_command_bot.log"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)

def send_message(chat_id, text):
    try:
        requests.post(f"{API_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=10)
    except Exception as e:
        logging.error(f"Error sending message: {e}")

def get_report_card(days=1):
    try:
        res = subprocess.check_output(
            ["python3", "/home/moonrider1968/bots/killer_beast_sniper/generate_report_card.py", str(days)],
            text=True,
            timeout=15
        )
        return f"```\n{res}\n```"
    except Exception as e:
        return f"❌ Error generating report card: {e}"

def get_system_status():
    alpaca_status = "UNKNOWN"
    oanda_status = "UNKNOWN"
    try:
        alpaca_res = subprocess.run(["systemctl", "is-active", "masterbrain"], capture_output=True, text=True)
        alpaca_status = "🟢 RUNNING" if alpaca_res.stdout.strip() == "active" else "🔴 STOPPED"
        
        oanda_res = subprocess.run(["systemctl", "is-active", "oanda_scalper"], capture_output=True, text=True)
        oanda_status = "🟢 RUNNING" if oanda_res.stdout.strip() == "active" else "🔴 STOPPED"
    except Exception:
        pass

    return (
        f"🏛️ *MASTER BOT CENTER: SYSTEM STATUS*\n\n"
        f"• **Alpaca Equities Scalper (60 Sectors)**: {alpaca_status}\n"
        f"• **OANDA Forex Scalper (13 Pairs)**: {oanda_status}\n"
        f"• **Risk Limit**: 1.0% Equity per Trade | Max 10 Concurrent Slots\n\n"
        f"Commands:\n"
        f"• `/reportcard` — Last 24 Hours PnL\n"
        f"• `/reportcard7` — 7-Day Rolling PnL\n"
        f"• `/status` — System Status"
    )

def main():
    logging.info("🚀 Master_trade_bot listener online...")
    last_update_id = 0

    # Flush any backlogged pending updates on startup
    try:
        flush_r = requests.get(f"{API_URL}/getUpdates", params={"offset": -1}, timeout=10)
        if flush_r.status_code == 200:
            res = flush_r.json().get("result", [])
            if res:
                last_update_id = res[-1]["update_id"]
    except Exception as e:
        logging.warning(f"Error flushing updates: {e}")

    while True:
        try:
            r = requests.get(f"{API_URL}/getUpdates", params={"offset": last_update_id + 1, "timeout": 20}, timeout=25)
            if r.status_code == 200:
                data = r.json()
                for update in data.get("result", []):
                    last_update_id = update["update_id"]
                    msg = update.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "").strip().lower()

                    if chat_id != AUTH_CHAT_ID:
                        continue

                    logging.info(f"📩 Command received: {text}")

                    if text in ["/reportcard", "reportcard", "/report_card"]:
                        send_message(chat_id, get_report_card(days=1))
                    elif text in ["/reportcard7", "reportcard7", "/report_card7"]:
                        send_message(chat_id, get_report_card(days=7))
                    elif text in ["/status", "/ping", "status"]:
                        send_message(chat_id, get_system_status())
                    elif text in ["/start", "/help"]:
                        send_message(chat_id, "🤖 *Master_trade_bot Ready*\nSend `/reportcard`, `/reportcard7`, or `/status`.")
        except Exception as e:
            logging.error(f"Polling error: {e}")
        time.sleep(1)

if __name__ == "__main__":
    main()


@bot.message_handler(commands=['reportcard'])
def dynamic_reportcard_command(message):
    try:
        text = generate_dynamic_reportcard.get_report_card_text()
        bot.send_message(message.chat.id, text, parse_mode="HTML")
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ Error generating reportcard: {e}")
