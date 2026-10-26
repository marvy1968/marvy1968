import os, time, json, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
STATE_FILE = os.path.expanduser("~/orchestrator/regime_state.json")

def run_short_worker():
    if not os.path.exists(STATE_FILE):
        return
    with open(STATE_FILE, "r") as f:
        state = json.load(f)
        
    regime = state.get("regime")
    has_trade = state.get("active_trade", True)
    
    if has_trade:
        return
        
    if regime in ["TRENDING_DOWN", "VOLATILITY_EXPANSION", "RANGING"]:
        logging.info(f"🔻 [SHORT DAEMON #1] Monitoring for Short Rejection / Breakout in [{regime}]...")

if __name__ == "__main__":
    run_short_worker()
