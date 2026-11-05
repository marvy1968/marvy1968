import os, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv("/home/moonrider1968/bots/.env")
key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
sec = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")

headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}
url = "https://paper-api.alpaca.markets/v2/orders"
res = requests.get(url, headers=headers, params={"status": "closed", "limit": 10}).json()

print("=" * 85)
print(f"{'SYM':<6} | {'QTY':>4} | {'TYPE':<8} | {'SUBMITTED':<20} | {'FILLED AT':<10} | {'CLIENT ORDER ID'}")
print("-" * 85)
for o in res:
    sym = o.get("symbol")
    qty = o.get("filled_qty")
    otype = o.get("type")
    sub = o.get("submitted_at")[:19]
    px = o.get("filled_avg_price")
    cid = o.get("client_order_id", "-")
    print(f"{sym:<6} | {qty:>4} | {otype:<8} | {sub:<20} | ${px} | {cid}")
print("=" * 85)
