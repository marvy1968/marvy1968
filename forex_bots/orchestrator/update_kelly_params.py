import json, os

STATE_FILE = os.path.expanduser("~/orchestrator/regime_state.json")

kelly_config = {
    "BOT-1": {"max_equity_risk": 0.0438, "payoff_target": 2.0},
    "BOT-2": {"max_equity_risk": 0.0500, "payoff_target": 1.6},
    "BOT-3": {"max_equity_risk": 0.0500, "payoff_target": 1.2},
    "BOT-4": {"max_equity_risk": 0.0500, "payoff_target": 1.8}
}

if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r") as f:
        data = json.load(f)
    data["kelly_config"] = kelly_config
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print("✅ Kelly Criterion parameters updated in regime state.")
