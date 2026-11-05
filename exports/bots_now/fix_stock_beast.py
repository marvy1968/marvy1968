import os

bot_path = "/home/moonrider1968/bots/killer_beast_sniper/stock_beast_v1.py"

API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"

# This is the "clean" header for your bot
new_header = f"""# --- LINKED TO UNIFIED BRAIN ---
import sys
sys.path.append('/home/moonrider1968/bots/Unified_Brain')
from brain import assess_regime, decide_action
# -------------------------------
import time
import datetime
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass

API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"
PAPER = True

api = TradingClient(API_KEY, SECRET_KEY, paper=PAPER)
"""

with open(bot_path, "r") as f:
    lines = f.readlines()

# Keep everything after the original imports/api definition (approx line 82)
# We find where 'def get_stock_state()' starts to keep your logic
start_logic_line = 0
for i, line in enumerate(lines):
    if "def get_stock_state():" in line:
        start_logic_line = i
        break

with open(bot_path, "w") as f:
    f.write(new_header + "\n")
    f.writelines(lines[start_logic_line:])

print(f"✅ Cleanly rebuilt {bot_path}")
