import os
import re
import numpy as np
from collections import defaultdict

LOG_FILE = "/home/moonrider1968/bots/killer_beast_sniper/logs/multi_scalper_shadow.log"

if not os.path.exists(LOG_FILE):
    print("❌ Shadow log file not found.")
    exit()

# Target MFE/MAE Rules
TP_SL_RULES = {
    "TSLA": {"sl": 0.0004, "tp": 0.0005},
    "AMD":  {"sl": 0.0005, "tp": 0.0006},
    "NVDA": {"sl": 0.0006, "tp": 0.0006},
    "AAPL": {"sl": 0.0002, "tp": 0.0002},
    "MSFT": {"sl": 0.0003, "tp": 0.0004},
    "AMZN": {"sl": 0.0004, "tp": 0.0005},
    "SPY":  {"sl": 0.0001, "tp": 0.0001},
    "QQQ":  {"sl": 0.0001, "tp": 0.0001},
}

PATTERN = re.compile(
    r"Class:\s*([\w]+)\s*\|\s*Asset:\s*([\w_]+)\s*\|\s*Strat:\s*([\w_]+)\s*\|\s*Side:\s*(\w+)\s*\|\s*Price:\s*([\d.]+)"
)

data = defaultdict(list)
with open(LOG_FILE, "r") as f:
    for line in f:
        if "[SHADOW MATCH]" in line:
            match = PATTERN.search(line)
            if match:
                asset_class, asset, strat, side, price = match.groups()
                data[(asset_class, asset, strat, side)].append(float(price))

def calc_kelly(w, r, fraction=0.25):
    if r <= 0 or w <= 0:
        return 0.005
    f_star = w - ((1.0 - w) / r)
    return max(min(f_star * fraction, 0.025), 0.005)

print("=" * 100)
print("🚀 MFE/MAE BRACKET-ADJUSTED DYNAMIC SIZING & KELLY REPORT")
print("=" * 100)
print(f"{'Class':<7} | {'Asset':<8} | {'Strategy':<18} | {'Side':<4} | {'Signals':>7} | {'MFE Win %':>9} | {'Payoff (R)':>10} | {'Opt. Risk %':>11}")
print("-" * 100)

for (asset_class, asset, strat, side), prices in sorted(data.items()):
    if len(prices) < 15 or asset_class != "STOCKS":
        continue
        
    rule = TP_SL_RULES.get(asset, {"sl": 0.0003, "tp": 0.0004})
    sl, tp = rule["sl"], rule["tp"]
    
    prices_arr = np.array(prices)
    diffs = np.diff(prices_arr) / prices_arr[:-1]
    if side == "SELL":
        diffs = -diffs
        
    wins = np.sum(diffs >= tp)
    losses = np.sum(diffs <= -sl)
    total = wins + losses
    
    if total == 0:
        continue
        
    win_rate = wins / total
    payoff_r = tp / sl
    opt_risk = calc_kelly(win_rate, payoff_r)
    
    print(f"{asset_class:<7} | {asset:<8} | {strat:<18} | {side:<4} | {total:>7,} | {win_rate*100:>8.1f}% | {payoff_r:>10.2f} | {opt_risk*100:>10.2f}%")

print("=" * 100)
