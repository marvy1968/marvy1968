import os
import re
import numpy as np
from collections import defaultdict

LOG_FILE = "/home/moonrider1968/bots/killer_beast_sniper/logs/multi_scalper_shadow.log"

if not os.path.exists(LOG_FILE):
    print("❌ Log file not found.")
    exit()

# Parse lines: Class: STOCKS | Asset: SPY | Strat: RANGE_BREAKOUT | Side: BUY | Price: 773.26
PATTERN = re.compile(
    r"Class:\s*STOCKS\s*\|\s*Asset:\s*([\w_]+)\s*\|\s*Strat:\s*([\w_]+)\s*\|\s*Side:\s*(\w+)\s*\|\s*Price:\s*([\d.]+)"
)

data_by_key = defaultdict(list)

with open(LOG_FILE, "r") as f:
    for line in f:
        if "Class: STOCKS" in line and "[SHADOW MATCH]" in line:
            match = PATTERN.search(line)
            if match:
                asset, strat, side, price = match.groups()
                data_by_key[(asset, strat, side)].append(float(price))

print("=" * 85)
print("📊 STOCKS MFE / MAE PARAMETER OPTIMIZATION MATRIX")
print("=" * 85)
print(f"{'Asset':<8} | {'Strategy':<18} | {'Side':<4} | {'Samples':>7} | {'Rec. Stop (MAE)':>15} | {'Rec. Target (MFE)':>17}")
print("-" * 85)

for (asset, strat, side), prices in sorted(data_by_key.items()):
    if len(prices) < 5:
        continue
        
    prices_arr = np.array(prices)
    returns = np.diff(prices_arr) / prices_arr[:-1]
    
    if side == "SELL":
        returns = -returns

    # Calculate 85th percentile MFE (Profit Potential) and 15th percentile MAE (Drawdown Limit)
    mae_stop = np.percentile(returns[returns < 0], 25) if len(returns[returns < 0]) > 0 else -0.002
    mfe_target = np.percentile(returns[returns > 0], 75) if len(returns[returns > 0]) > 0 else 0.005

    print(f"{asset:<8} | {strat:<18} | {side:<4} | {len(prices):>7,} | {mae_stop*100:>14.2f}% | {mfe_target*100:>16.2f}%")

print("=" * 85)
