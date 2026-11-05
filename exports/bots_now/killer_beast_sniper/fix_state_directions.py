import json, os, requests

STATE_FILE = "/home/moonrider1968/bots/killer_beast_sniper/virtual_ratchet_state.json"
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
OANDA_KEY = os.getenv("OANDA_API_KEY", os.getenv("OANDA_API_TOKEN"))
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxpractice.oanda.com")

headers = {"Authorization": f"Bearer {OANDA_KEY}"}
r = requests.get(f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/trades", headers=headers)
trades = r.json().get("trades", [])

state = {}
for t in trades:
    tid = str(t["id"])
    sym = t["instrument"]
    units = float(t["currentUnits"])
    entry = float(t["price"])
    pip_val = 0.01 if "JPY" in sym else 0.0001
    init_risk = max(15.0 * pip_val, 0.0015)
    
    # Correct signed units and initial SL orientation
    state[tid] = {
        "instrument": sym,
        "units": units,
        "entry_px": entry,
        "initial_risk": init_risk,
        "virtual_sl": entry - init_risk if units > 0 else entry + init_risk,
        "tp_tier": 1 if tid in ["490598", "490592"] else 0
    }
    if tid == "490598": # USD_JPY Long
        state[tid]["virtual_sl"] = 158.740
    elif tid == "490592": # EUR_USD Short
        state[tid]["virtual_sl"] = 1.16979

with open(STATE_FILE, "w") as f:
    json.dump(state, f, indent=2)

print("✅ State direction and signs updated successfully.")
