import os, requests
from dotenv import load_dotenv

load_dotenv('/home/moonrider1968/bots/killer_beast_sniper/.env')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
API_KEY = os.getenv('APCA_API_KEY_ID')
SECRET_KEY = os.getenv('APCA_API_SECRET_KEY')
BASE_URL = os.getenv('APCA_API_BASE_URL', 'https://paper-api.alpaca.markets')

def send_daily_recap():
    headers = {'APCA-API-KEY-ID': API_KEY, 'APCA-API-SECRET-KEY': SECRET_KEY}
    acc_res = requests.get(f"{BASE_URL}/v2/account", headers=headers).json()
    
    equity = float(acc_res.get('equity', 0))
    last_equity = float(acc_res.get('last_equity', 0))
    daily_pnl = equity - last_equity
    daily_pct = (daily_pnl / last_equity * 100) if last_equity > 0 else 0

    emoji = "🟢" if daily_pnl >= 0 else "🔴"
    msg = (
        f"📋 *MASTER BRAIN DAILY MARKET CLOSE RECAP*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *Portfolio Equity:* `${equity:,.2f}`\n"
        f"{emoji} *Daily PnL:* `${daily_pnl:+,.2f}` (`{daily_pct:+.2f}%`)\n"
        f"🛡️ *Risk Mode:* `Quarter-Kelly (f* 0.25)`\n"
        f"📊 *Alpaca Fleet:* `Active & Monitored`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"})
    print("✅ Telegram daily recap sent.")

if __name__ == '__main__':
    send_daily_recap()
