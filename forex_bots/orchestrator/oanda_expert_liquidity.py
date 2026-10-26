import os, json, logging

STATE_FILE = os.path.expanduser("~/orchestrator/regime_state.json")
PROPOSALS_FILE = os.path.expanduser("~/orchestrator/trade_proposals.json")

def evaluate_liquidity():
    if not os.path.exists(STATE_FILE): return
    with open(STATE_FILE, "r") as f: state = json.load(f)
    if state.get("open_trades_count", 1) >= 1: return

    close = state.get("close", 0)
    low_15 = state.get("low_15", 0)
    spread = state.get("spread", 0.003)
    atr = max(state.get("atr", 0.040), 0.040)
    units = 3500

    if close > low_15: # Rejection back above support
        sl = low_15 - (spread + 0.1 * atr)
        tp = close + (1.6 * abs(close - sl))
        proposal = {
            "expert_id": "BOT-2: LIQUIDITY SWEEP BUY",
            "units": units,
            "sl": sl,
            "tp": tp,
            "reasoning": "Swept 15-period low and rejected back into support"
        }
        proposals = []
        if os.path.exists(PROPOSALS_FILE):
            try:
                with open(PROPOSALS_FILE, "r") as f: proposals = json.load(f)
            except Exception: proposals = []
        proposals.append(proposal)
        with open(PROPOSALS_FILE, "w") as f: json.dump(proposals, f, indent=2)

if __name__ == "__main__":
    evaluate_liquidity()
