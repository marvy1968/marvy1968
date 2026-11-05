import os
import alpaca_trade_api as tradeapi

print("=== 1. Checking Stock Daemon Auth Setup ===")
stock_script = "/home/moonrider1968/bots/killer_beast_sniper/velez_stage4_short_daemon.py"
if os.path.exists(stock_script):
    with open(stock_script) as f:
        for line in f:
            if any(w in line for w in ["REST(", "BASE_URL", "load_dotenv", "API_KEY"]):
                print(" ", line.strip())

print("\n=== 2. Testing Keys Across Paper & Live Endpoints ===")
paths = [
    "/home/moonrider1968/bots/alpaca_microedge/.env",
    "/home/moonrider1968/bots/killer_beast_sniper/.env"
]
urls = [
    "https://paper-api.alpaca.markets",
    "https://api.alpaca.markets"
]

for env_path in paths:
    if not os.path.exists(env_path):
        continue
    keys = {}
    with open(env_path) as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                keys[k.strip()] = v.strip().strip("\"'")
    
    k = keys.get("ALPACA_API_KEY") or keys.get("APCA_API_KEY_ID", "")
    s = keys.get("ALPACA_SECRET_KEY") or keys.get("APCA_API_SECRET_KEY", "")
    
    for url in urls:
        try:
            api = tradeapi.REST(key_id=k, secret_key=s, base_url=url, api_version="v2")
            acc = api.get_account()
            print(f"  ✅ MATCH FOUND!")
            print(f"     File: {env_path}")
            print(f"     URL:  {url}")
            print(f"     Key:  {k[:8]}...")
            print(f"     Equity: ${acc.equity}\n")
        except Exception as e:
            domain = url.split("//")[1]
            fname = os.path.basename(env_path)
            print(f"  ❌ FAILED ({fname} @ {domain}): {e}")
