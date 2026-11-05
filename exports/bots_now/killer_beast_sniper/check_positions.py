import os
import requests

APCA_KEY = (os.getenv("APCA_API_KEY_ID") or "REDACTED_BROKER_KEY").strip()
APCA_SECRET = (os.getenv("APCA_API_SECRET_KEY") or "EnXL5FZSVSQzHvVTGLh1venMboWHXhGeQ2Kgy2gjzX5e").strip()
URL = "https://paper-api.alpaca.markets/v2/positions"

HEADERS = {
    "APCA-API-KEY-ID": APCA_KEY,
    "APCA-API-SECRET-KEY": APCA_SECRET
}

try:
    r = requests.get(URL, headers=HEADERS, timeout=5)
    if r.status_code == 200:
        positions = r.json()
        print("=" * 65)
        print(f"💼 ALPACA OPEN POSITIONS ({len(positions)})")
        print("=" * 65)
        if not positions:
            print("🟢 No active paper positions open.")
        for p in positions:
            sym = p["symbol"]
            qty = p["qty"]
            entry = float(p["avg_entry_price"])
            pnl = float(p["unrealized_pl"])
            pnl_pct = float(p["unrealized_plpc"]) * 100
            print(f"📌 {sym:<5} | Qty: {qty:>4} | Entry: ${entry:<7.2f} | PnL: ${pnl:+6.2f} ({pnl_pct:+6.2f}%)")
        print("=" * 65)
    else:
        print(f"⚠️ API Error: HTTP {r.status_code} - {r.text}")
except Exception as e:
    print(f"⚠️ Exception: {e}")
