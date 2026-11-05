import os
from pathlib import Path
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient

load_dotenv("/home/moonrider1968/bots/.env")
API_KEY = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")

tc = TradingClient(API_KEY, API_SECRET, paper=True)
acct = tc.get_account()

print("=" * 65)
print(f"Account Number: {acct.account_number}")
print(f"Status:         {acct.status}")
print(f"Portfolio Value: ${float(acct.portfolio_value):,.2f}")
print(f"Cash:            ${float(acct.cash):,.2f}")
print(f"Buying Power:    ${float(acct.buying_power):,.2f}")
print("=" * 65)

print("\nRecent Account Activities (Last 10):")
try:
    # Use REST client directly to inspect non-trade/trade events
    import requests
    headers = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET}
    url = "https://paper-api.alpaca.markets/v2/account/activities"
    res = requests.get(url, headers=headers, params={"page_size": 10}).json()
    for act in res:
        act_type = act.get("activity_type", "UNKNOWN")
        sym = act.get("symbol", "-")
        qty = act.get("qty", "-")
        price = act.get("price", "-")
        time_str = act.get("transaction_time", act.get("date", "-"))
        print(f"[{time_str}] {act_type:<6} | {sym:<6} | Qty: {qty:<5} | Price: ${price}")
except Exception as e:
    print(f"[!] Could not fetch activities: {e}")
