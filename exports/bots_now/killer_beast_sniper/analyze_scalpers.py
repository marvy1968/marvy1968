import os
import re
from collections import defaultdict

LOG_FILE = "/home/moonrider1968/bots/killer_beast_sniper/logs/multi_scalper_shadow.log"

if not os.path.exists(LOG_FILE):
    print("❌ No shadow log file found yet.")
    exit()

# Regex to match: Class: FOREX | Asset: EUR_USD | Strat: RANGE_BREAKOUT | Side: SELL | Price: 1.15616
PATTERN = re.compile(
    r"Class:\s*(\w+)\s*\|\s*Asset:\s*([\w_]+)\s*\|\s*Strat:\s*([\w_]+)\s*\|\s*Side:\s*(\w+)\s*\|\s*Price:\s*([\d.]+)"
)

signals_by_class = defaultdict(lambda: defaultdict(int))
total_signals = 0

with open(LOG_FILE, "r") as f:
    for line in f:
        if "[SHADOW MATCH]" in line:
            match = PATTERN.search(line)
            if match:
                asset_class, asset, strat, side, price = match.groups()
                key = (asset, strat, side)
                signals_by_class[asset_class][key] += 1
                total_signals += 1

print("=" * 75)
print(f"📊 5-SCALPER SHADOW SUMMARY REPORT ({total_signals} Total Signals Triggered)")
print("=" * 75)

for asset_class, counts in sorted(signals_by_class.items()):
    print(f"\n📂 ASSET CLASS: {asset_class}")
    print(f"{'Asset':<10} | {'Strategy':<22} | {'Side':<6} | {'Triggers':>10}")
    print("-" * 60)
    for (asset, strat, side), count in sorted(counts.items()):
        print(f"{asset:<10} | {strat:<22} | {side:<6} | {count:>10,}")

print("=" * 75)
