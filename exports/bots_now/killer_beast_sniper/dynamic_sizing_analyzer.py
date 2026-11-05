import os
import re
import numpy as np
from collections import defaultdict

LOG_FILE = "/home/moonrider1968/bots/killer_beast_sniper/logs/multi_scalper_shadow.log"

if not os.path.exists(LOG_FILE):
    print("❌ Shadow log file not found.")
    exit()

# Parse log lines
PATTERN = re.compile(
    r"Class:\s*([\w]+)\s*\|\s*Asset:\s*([\w_]+)\s*\|\s*Strat:\s*([\w_]+)\s*\|\s*Side:\s*(\w+)\s*\|\s*Price:\s*([\d.]+)"
)

# Aggregation structures
strategy_data = defaultdict(list)

with open(LOG_FILE, "r") as f:
    for line in f:
        if "[SHADOW MATCH]" in line:
            match = PATTERN.search(line)
            if match:
                asset_class, asset, strat, side, price = match.groups()
                strategy_data[(asset_class, asset, strat)].append(float(price))

def calculate_dynamic_kelly(win_rate, payoff_ratio, fraction=0.25):
    """Calculates Fractional Kelly Risk %."""
    if payoff_ratio <= 0:
        return 0.005  # Default baseline risk 0.5%
    full_kelly = win_rate - ((1.0 - win_rate) / payoff_ratio)
    # Clamp Kelly fraction between 0.5% floor and 2.5% max risk cap
    quarter_kelly = max(min(full_kelly * fraction, 0.025), 0.005)
    return quarter_kelly

print("=" * 100)
print("🚀 DYNAMIC POSITION SIZING & ROI OPTIMIZATION REPORT")
print("=" * 100)
print(f"{'Class':<7} | {'Asset':<8} | {'Strategy':<18} | {'Signals':>7} | {'Win Est %':>9} | {'Payoff (R)':>10} | {'Opt. Risk %':>11} | {'ROI Rank':>8}")
print("-" * 100)

for (asset_class, asset, strat), prices in sorted(strategy_data.items()):
    if len(prices) < 10:
        continue
    
    prices_arr = np.array(prices)
    returns = np.diff(prices_arr) / prices_arr[:-1]
    
    wins = returns[returns > 0]
    losses = np.abs(returns[returns < 0])
    
    win_rate = len(wins) / len(returns) if len(returns) > 0 else 0.5
    avg_win = np.mean(wins) if len(wins) > 0 else 0.001
    avg_loss = np.mean(losses) if len(losses) > 0 else 0.001
    
    payoff_ratio = avg_win / avg_loss if avg_loss > 0 else 1.0
    
    # Calculate optimal risk percentage
    opt_risk_pct = calculate_dynamic_kelly(win_rate, payoff_ratio)
    
    # Estimate ROI factor (Geometric Expectancy)
    roi_score = (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss)
    rank = "🔥 HIGH" if roi_score > 0.0002 else ("⚡ MED" if roi_score > 0 else "❄️ LOW")

    print(f"{asset_class:<7} | {asset:<8} | {strat:<18} | {len(prices):>7,} | {win_rate*100:>8.1f}% | {payoff_ratio:>10.2f} | {opt_risk_pct*100:>10.2f}% | {rank:>8}")

print("=" * 100)
