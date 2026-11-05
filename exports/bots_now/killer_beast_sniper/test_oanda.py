import os
import requests

account_id = os.getenv("OANDA_ACCOUNT_ID")
api_token = os.getenv("OANDA_API_TOKEN")
env = os.getenv("OANDA_ENV", "practice")

url = f"https://api-fxpractice.oanda.com/v3/accounts/{account_id}"
headers = {
    "Authorization": f"Bearer {api_token}",
    "Content-Type": "application/json"
}

print("=" * 70)
print("🌐 TESTING OANDA PRACTICE API CONNECTION")
print("=" * 70)

try:
    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code == 200:
        data = r.json().get("account", {})
        nav = data.get("NAV", "N/A")
        currency = data.get("currency", "USD")
        open_trades = data.get("openTradeCount", 0)
        print(f"✅ Connection Successful!")
        print(f"  • Account ID   : {account_id}")
        print(f"  • Environment  : {env}")
        print(f"  • Account NAV  : {nav} {currency}")
        print(f"  • Open Trades  : {open_trades}")
    else:
        print(f"❌ Connection Failed | HTTP Status: {r.status_code}")
        print(f"Response: {r.text}")
except Exception as e:
    print(f"❌ Request Exception: {e}")

print("=" * 70)
