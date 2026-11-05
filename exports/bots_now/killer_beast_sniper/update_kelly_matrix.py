import sys
import re

EXECUTOR_PATH = "/home/moonrider1968/bots/killer_beast_sniper/alpaca_paper_executor.py"

# --- 1. DEFINE YOUR UPDATED / NEW KELLY RISK ALLOCATIONS ---
# Tuple key format: (Ticker, Strategy, Side): Risk Percentage (e.g., 0.02 = 2.0%, 0.025 = 2.5%, 0.01 = 1.0%)
NEW_KELLY_ENTRIES = {
    # Modify existing risk allocation
    ("AAPL", "MICRO_MOMENTUM", "BUY"): 0.020,  # Lowered from 2.5% to 2.0%
    
    # Add new tickers / strategies
    ("META", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("META", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("META", "RANGE_BREAKOUT", "BUY"): 0.020,
    ("NFLX", "MICRO_MOMENTUM", "BUY"): 0.015,
    ("NFLX", "RANGE_BREAKOUT", "BUY"): 0.015,
}

# --- 2. DEFINE SL/TP OPTIMIZATION RULES FOR NEW TICKERS ---
NEW_OPTIMIZATION_RULES = {
    "META": {"MICRO_MOMENTUM": {"sl_pct": 0.0004, "tp_pct": 0.0005}, "RANGE_BREAKOUT": {"sl_pct": 0.0003, "tp_pct": 0.0004}},
    "NFLX": {"MICRO_MOMENTUM": {"sl_pct": 0.0005, "tp_pct": 0.0006}, "RANGE_BREAKOUT": {"sl_pct": 0.0004, "tp_pct": 0.0005}}
}

sys.path.append("/home/moonrider1968/bots/killer_beast_sniper")
from alpaca_paper_executor import KELLY_RISK_MATRIX, STOCK_OPTIMIZATION_MATRIX

# Apply updates to memory dictionaries
KELLY_RISK_MATRIX.update(NEW_KELLY_ENTRIES)
STOCK_OPTIMIZATION_MATRIX.update(NEW_OPTIMIZATION_RULES)

# Format dicts for clean Python file output
formatted_kelly = "KELLY_RISK_MATRIX = {\n"
for (ticker, strat, side), risk in sorted(KELLY_RISK_MATRIX.items()):
    formatted_kelly += f'    ("{ticker}", "{strat}", "{side}"): {risk},\n'
formatted_kelly += "}"

formatted_opt = "STOCK_OPTIMIZATION_MATRIX = " + str(STOCK_OPTIMIZATION_MATRIX)

# Write updated matrices back to alpaca_paper_executor.py
with open(EXECUTOR_PATH, "r") as f:
    content = f.read()

content = re.sub(r'KELLY_RISK_MATRIX = \{.*?\}', formatted_kelly, content, flags=re.DOTALL)
content = re.sub(r'STOCK_OPTIMIZATION_MATRIX = \{.*?\}', formatted_opt, content, flags=re.DOTALL)

with open(EXECUTOR_PATH, "w") as f:
    f.write(content)

print("✅ Successfully updated KELLY_RISK_MATRIX and STOCK_OPTIMIZATION_MATRIX!")
