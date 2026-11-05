import os
import requests

# Load Alpaca credentials from environment or config
api_key = os.environ.get("APCA_API_KEY_ID")
api_secret = os.environ.get("APCA_API_SECRET_KEY")
base_url = os.environ.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")

if not api_key or not api_secret:
    # Look for config or .env files in the current folder
    for file in os.listdir("."):
        if "env" in file or "config" in file or "bot" in file:
            try:
                with open(file, "r") as f:
                    for line in f:
                        if "API_KEY" in line and not api_key:
                            parts = line.replace("'", "").replace('"', "").strip().split("=")
                            if len(parts) > 1: api_key = parts[1].strip()
                        if "SECRET" in line and not api_secret:
                            parts = line.replace("'", "").replace('"', "").strip().split("=")
                            if len(parts) > 1: api_secret = parts[1].strip()
            except:
                pass

if not api_key or not api_secret:
    print("[INFO] Please provide your Alpaca API keys or ensure they are in your environment variables.")
else:
    try:
        headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret
        }
        response = requests.get(f"{base_url}/v2/orders?status=all&limit=10", headers=headers)
        if response.status_code == 200:
            orders = response.json()
            print(f"\n--- RECENT ALPACA ORDERS ({len(orders)} found) ---")
            if not orders:
                print("No recent orders found in this account.")
            for o in orders[:5]:
                print(f" - [{o.get('created_at')}] {o.get('symbol')}: {o.get('side')} {o.get('qty')} shares @ {o.get('status')} ({o.get('type')})")
        else:
            print(f"API Error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Could not fetch orders: {e}")
