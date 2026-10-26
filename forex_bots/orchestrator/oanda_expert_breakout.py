import os, json, logging

STATE_FILE = os.path.expanduser("~/orchestrator/regime_state.json")
PROPOSALS_FILE = os.path.expanduser("~/orchestrator/trade_proposals.json")

def evaluate_breakout():
    if not os.path.exists(STATE_FILE): return
    with open(STATE_FILE, "r") as f: state = json.load(f)
    if state.get("open_trades_count", 1) >= 1: return

    close = state.get("close", 0)
    high_15 = state.get("high_15", 0)
    low_15 = state.get("low_15", 0)
    atr = max(state.get("atr", 0.040), 0.040)
    units = 5000 # Bot-1 Sizing Philosophy

    proposal = None
    if close > high_15:
        proposal = {
            "expert_id": "BOT-1: STRUCTURAL BREAKOUT",
            "units": units,
            "sl": close - (1.2 * atr),
            "tp": close + (2.4 * atr),
            "reasoning": "M5 close broke 15-period structural high"
        }
    elif close < low_15:
        proposal = {
            "expert_id": "BOT-1: STRUCTURAL BREAKOUT",
            "units": -units,
            "sl": close + (1.2 * atr),
            "tp": close - (2.4 * atr),
            "reasoning": "M5 close broke 15-period structural low"
        }

    if proposal:
        proposals = []
        if os.path.exists(PROPOSALS_FILE):
            try:
                with open(PROPOSALS_FILE, "r") as f: proposals = json.load(f)
            except Exception: proposals = []
        proposals.append(proposal)
        with open(PROPOSALS_FILE, "w") as f: json.dump(proposals, f, indent=2)

if __name__ == "__main__":
    evaluate_breakout()
