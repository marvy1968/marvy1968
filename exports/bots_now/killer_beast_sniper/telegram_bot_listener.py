import generate_dynamic_reportcard
import os
import sys
import time
import json
import requests
from datetime import datetime, timedelta

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

APCA_KEY = os.getenv("APCA_API_KEY_ID", "").strip()
APCA_SECRET = os.getenv("APCA_API_SECRET_KEY", "").strip()
APCA_URL = "https://paper-api.alpaca.markets/v2"

LOG_DIR = "/home/moonrider1968/bots/killer_beast_sniper/logs"
MATRIX_FILE = "/home/moonrider1968/bots/killer_beast_sniper/stock_learning_matrix.json"

def get_alpaca_stats():
    if not APCA_KEY or not APCA_SECRET:
        return "💳 *Alpaca:* Credentials Missing"
    headers = {"APCA-API-KEY-ID": APCA_KEY, "APCA-API-SECRET-KEY": APCA_SECRET}
    try:
        r = requests.get(f"{APCA_URL}/account", headers=headers, timeout=5)
        if r.status_code == 200:
            acc = r.json()
            equity = float(acc.get("equity", 0))
            bp = float(acc.get("buying_power", 0))
            cash = float(acc.get("cash", 0))
            
            pos_r = requests.get(f"{APCA_URL}/positions", headers=headers, timeout=5)
            positions = pos_r.json() if pos_r.status_code == 200 else []
            open_pos = len(positions)
            unrealized_pnl = sum(float(p.get("unrealized_pl", 0)) for p in positions)
            
            pnl_str = f"+${unrealized_pnl:,.2f}" if unrealized_pnl >= 0 else f"-${abs(unrealized_pnl):,.2f}"
            return (f"💳 *PORTFOLIO SUMMARY*\n"
                    f"• Equity: `${equity:,.2f}`\n"
                    f"• Cash: `${cash:,.2f}`\n"
                    f"• Buying Power: `${bp:,.2f}`\n"
                    f"• Open Positions: `{open_pos}`\n"
                    f"• Unrealized PnL: `{pnl_str}`")
    except Exception as e:
        return f"⚠️ Alpaca Error: {e}"
    return "⚠️ Alpaca Connection Failed"

def generate_report_card(days=None):
    header = f"📊 *KILLER BEAST SNIPER REPORT CARD*"
    if days:
        header += f" *({days}-DAY ROLLING AUDIT)*"
    
    alpaca_summary = get_alpaca_stats()
    
    matrix_msg = "🧠 *LEARNING MATRIX SUMMARY*\n"
    if os.path.exists(MATRIX_FILE):
        try:
            with open(MATRIX_FILE, 'r') as f:
                matrix = json.load(f)
            count = 0
            for sym, stats in list(matrix.items())[:6]:
                w = stats.get('wins', 0)
                l = stats.get('losses', 0)
                wr = stats.get('win_rate', 0.0) * 100
                matrix_msg += f"• `{sym:<5}` | W:{w} L:{l} | Win Rate: {wr:.1f}%\n"
                count += 1
            if len(matrix) > 6:
                matrix_msg += f"  _...and {len(matrix)-6} more tracked symbols_\n"
        except Exception as e:
            matrix_msg += f"⚠️ Read error: {e}\n"
    else:
        matrix_msg += "ℹ️ Matrix initializing from active telemetry.\n"

    shadow_log = os.path.join(LOG_DIR, "multi_scalper_shadow.log")
    log_msg = "📈 *SHADOW TELEMETRY*\n"
    if os.path.exists(shadow_log):
        try:
            with open(shadow_log, 'r') as f:
                lines = f.readlines()
            total = len(lines)
            eq = sum(1 for line in lines if "STOCKS" in line)
            fx = sum(1 for line in lines if "FOREX" in line)
            crypto = sum(1 for line in lines if "CRYPTO" in line)
            log_msg += f"• Total Signals Captured: `{total:,}`\n• Equities: `{eq:,}` | Forex: `{fx:,}` | Crypto: `{crypto:,}`"
        except Exception as e:
            log_msg += f"⚠️ Log read error: {e}"
    else:
        log_msg += "⚠️ Shadow log not found."

    return f"{header}\n\n{alpaca_summary}\n\n{matrix_msg}\n{log_msg}"

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Error sending message: {e}")

def poll_telegram():
    print("🚀 Telegram Bot Listener Active. Listening for commands...")
    offset = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            params = {"timeout": 30, "offset": offset}
            r = requests.get(url, params=params, timeout=35)
            if r.status_code == 200:
                data = r.json()
                for result in data.get("result", []):
                    offset = result["update_id"] + 1
                    msg = result.get("message", {})
                    text = msg.get("text", "").strip()
                    chat_id = str(msg.get("chat", {}).get("id", ""))

                    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
                        print(f"Ignored unauthorized message from Chat ID: {chat_id}")
                        continue

                    if text.startswith("/reportcard"):
                        parts = text.split()
                        days = parts[1] if len(parts) > 1 and parts[1].isdigit() else None
                        report = generate_report_card(days)
                        send_message(chat_id, report)
                    elif text in ["/start", "/help"]:
                        send_message(chat_id, "🤖 *Killer Beast Sniper Bot Ready*\n\nSend `/reportcard` or `/reportcard 7` for instant diagnostics.")
        except Exception as e:
            print(f"Polling warning: {e}")
            time.sleep(5)

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN environment variable is not set.")
        sys.exit(1)
    poll_telegram()
