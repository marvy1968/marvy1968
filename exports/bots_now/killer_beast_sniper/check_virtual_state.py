import json, os, requests
from dotenv import load_dotenv

load_dotenv("/home/moonrider1968/.env")

STATE_FILE = "/home/moonrider1968/bots/killer_beast_sniper/virtual_ratchet_state.json"
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
OANDA_KEY = os.getenv("OANDA_API_KEY", os.getenv("OANDA_API_TOKEN"))
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxpractice.oanda.com")

if not os.path.exists(STATE_FILE):
    print("State file not found.")
    exit(1)

with open(STATE_FILE) as f:
    state = json.load(f)

print("=" * 80)
print("  🎯 LIVE VIRTUAL RATCHET TELEMETRY (CORRECTED DIRECTION)")
print("=" * 80)

headers = {"Authorization": f"Bearer {OANDA_KEY}"}

for tid, v in state.items():
    sym = v["instrument"]
    units = v.get("units", 0)
    entry = v["entry_px"]
    v_sl = v["virtual_sl"]
    tier = v.get("tp_tier", 0)
    side = "LONG" if units > 0 else "SHORT"
    
    r = requests.get(f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing", headers=headers, params={"instruments": sym})
    cur_px = entry
    if r.status_code == 200:
        prices = r.json().get("prices", [])
        if prices:
            cur_px = (float(prices[0]["bids"][0]["price"]) + float(prices[0]["asks"][0]["price"])) / 2.0
            
    pip_mult = 100.0 if "JPY" in sym else 10000.0
    pnl_pips = (cur_px - entry) * pip_mult if units > 0 else (entry - cur_px) * pip_mult
    sl_dist = (cur_px - v_sl) * pip_mult if units > 0 else (v_sl - cur_px) * pip_mult
    fmt = ".3f" if "JPY" in sym else ".5f"

    print(f"Trade #{tid:<6} | Pair: {sym:<8} | Side: {side:<5} | Qty: {abs(int(units))}")
    print(f"  • Entry Px     : {entry:{fmt}}")
    print(f"  • Current Px   : {cur_px:{fmt}}  (Profit: {pnl_pips:+.1f} pips)")
    print(f"  • Virtual SL   : {v_sl:{fmt}}  (SL Cushion: {sl_dist:.1f} pips)")
    print(f"  • Ratchet Tier : Tier {tier} Active")
    print("-" * 80)
