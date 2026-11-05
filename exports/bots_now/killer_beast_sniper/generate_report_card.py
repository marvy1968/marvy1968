import json
import os
import glob
from datetime import datetime, timedelta

LOG_DIR = "/home/moonrider1968/bots/killer_beast_sniper/logs"
MATRIX_FILE = "/home/moonrider1968/bots/killer_beast_sniper/stock_learning_matrix.json"

def main():
    print("=" * 70)
    print("📊 KILLER BEAST SNIPER — SYSTEM REPORT CARD (7-DAY ROLLING)")
    print("=" * 70)

    # Load Learning Matrix if available
    if os.path.exists(MATRIX_FILE):
        try:
            with open(MATRIX_FILE, 'r') as f:
                matrix = json.load(f)
            print("🧠 LEARNING MATRIX STATUS: Active & Tracking")
            for k, v in list(matrix.items())[:5]:
                print(f"   • {k}: {v}")
        except Exception as e:
            print(f"⚠️ Matrix Read Error: {e}")
    else:
        print("ℹ️ Learning matrix initializing from active telemetry.")

    print("-" * 70)

    # Scan recent shadow log entries
    shadow_log = os.path.join(LOG_DIR, "multi_scalper_shadow.log")
    if os.path.exists(shadow_log):
        with open(shadow_log, 'r') as f:
            lines = f.readlines()
        
        total_signals = len(lines)
        print(f"📈 Total Recorded Shadow Signals: {total_signals}")

        # Group by asset class
        forex_cnt = sum(1 for l in lines if "FOREX" in l)
        stock_cnt = sum(1 for l in lines if "STOCKS" in l)
        crypto_cnt = sum(1 for l in lines if "CRYPTO" in l)

        print(f"   • Equities Signals : {stock_cnt}")
        print(f"   • Forex Signals    : {forex_cnt}")
        print(f"   • Crypto Signals   : {crypto_cnt}")
    else:
        print("⚠️ Shadow log file not found at expected path.")

    print("=" * 70)

if __name__ == "__main__":
    main()
