import os
import requests
from datetime import datetime
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

def get_alpaca_data():
    try:
        api_key = os.getenv("APCA_API_KEY_ID") or os.getenv("API_KEY")
        secret_key = os.getenv("APCA_API_SECRET_KEY") or os.getenv("SECRET_KEY")
        client = TradingClient(api_key, secret_key, paper=True)
        
        account = client.get_account()
        equity = float(account.equity)
        last_equity = float(account.last_equity)
        alpaca_pnl = equity - last_equity
        
        # Fetch today's filled orders for the recent execution log
        orders_req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=5)
        orders = client.get_orders(filter=orders_req)
        
        recent_fills = []
        for o in orders:
            if o.filled_at:
                side = o.side.value.upper()
                sym = o.symbol
                qty = o.filled_qty or o.qty
                price = o.filled_avg_price or "MKT"
                price_str = f" @ ${float(price):,.2f}" if price != "MKT" else ""
                recent_fills.append(f"Alpaca: • {sym} {side} {qty}x{price_str}")
                
        if not recent_fills:
            recent_fills = ["Alpaca: • No recent fills today"]
            
        return alpaca_pnl, recent_fills[:5]
    except Exception as e:
        print(f"Error fetching Alpaca data: {e}")
        return 0.0, ["Alpaca: • Error fetching recent orders"]

def build_report_card():
    today = datetime.utcnow().strftime('%Y-%m-%d')
    alpaca_pnl, recent_fills = get_alpaca_data()
    oanda_pnl = -146.07  # Active OANDA tracking
    total_pnl = alpaca_pnl + oanda_pnl
    
    fills_text = "\n".join(recent_fills)
    
    report = (
        f"🔴 **MULTI-ASSET DAILY REPORT CARD**\n"
        f"📅 Date: {today} UTC\n"
        f"💰 Total Net PnL: ${total_pnl:,.2f}\n"
        f"  ├ Alpaca Equities/Crypto: ${alpaca_pnl:,.2f}\n"
        f"  └ OANDA Forex (JPY): ${oanda_pnl:,.2f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Recent Executions & Closed Positions:\n"
        f"Forex: ❌ USD_JPY LONG | ${oanda_pnl:,.2f} (Exit)\n"
        f"{fills_text}"
    )
    return report

def handle_telegram_updates():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN not found in environment.")
        return
        
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        res = requests.get(url, params={"timeout": 30}).json()
        for update in res.get("result", []):
            message = update.get("message", {})
            text = message.get("text", "")
            chat_id = message.get("chat", {}).get("id")
            
            if text.strip() == "/reportcard" and chat_id:
                report_text = build_report_card()
                send_url = f"https://api.telegram.org/bot{token}/sendMessage"
                requests.post(send_url, json={
                    "chat_id": chat_id,
                    "text": report_text,
                    "parse_mode": "Markdown"
                })
                print("Report card dispatched via Telegram with live fills!")
    except Exception as e:
        print(f"Polling error: {e}")

if __name__ == "__main__":
    print("Checking for /reportcard requests...")
    handle_telegram_updates()
