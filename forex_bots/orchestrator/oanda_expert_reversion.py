import os, json, logging

STATE_FILE = os.path.expanduser("~/orchestrator/regime_state.json")
PROPOSALS_FILE = os.path.expanduser("~/orchestrator/trade_proposals.json")

def evaluate_reversion():
    if not os.path.exists(STATE_FILE): return
    with open(STATE_FILE, "r") as f: state = json.load(f)
    if state.get("open_trades_count", 1) >= 1: return

    close = state.get("close", 0)
    sma20 = state.get("sma20", 0)
    std20 = state.get("std20", 0)
    atr = max(state.get("atr", 0.040), 0.040)
    units = 2500

    bb_upper = sma20 + (1.65 * std20)
    bb_lower = sma20 - (1.65 * std20)

    proposal = None
    if close <= bb_lower:
        proposal = {
            "expert_id": "BOT-3: MEAN REVERSION BUY",
            "units": units,
            "sl": close - (1.2 * atr),
            "tp": sma20,
            "reasoning": "Price overextended below 1.65-sigma envelope"
        }
    elif close >= bb_upper:
        proposal = {
            "expert_id": "BOT-3: MEAN REVERSION SELL",
            "units": -units,
            "sl": close + (1.2 * atr),
            "tp": sma20,
            "reasoning": "Price overextended above 1.65-sigma envelope"
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
    evaluate_reversion()
