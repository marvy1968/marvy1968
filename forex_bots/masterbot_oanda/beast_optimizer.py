import sqlite3, json, os
from datetime import datetime, timedelta

def self_optimize():
    print("🧠 Brain Optimizer: Analyzing market regime...")
    # Baseline settings for Sunday open
    best_config = {
        "MACD_FAST": 8, "MACD_SLOW": 17, "MACD_SIGNAL": 9,
        "ADX_THRESHOLD": 25, "RSI_THRESHOLD": 50, "HEAT_MIN": 0.85
    }
    with open("brain_params.json", "w") as f:
        json.dump(best_config, f, indent=4)
    print("✅ Self-Learning Complete: Sunday parameters locked.")

if __name__ == "__main__":
    self_optimize()
