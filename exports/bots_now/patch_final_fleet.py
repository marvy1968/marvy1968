import os

# Your 3 survivors
final_bots = [
    "/home/moonrider1968/bots/killer_beast_sniper/stock_beast_v1.py",
    "/home/moonrider1968/bots/killer_beast_sniper/master_beast_v49.19_crypto_only.py",
    "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"
]

patch_code = """
# --- LINKED TO UNIFIED BRAIN ---
import sys
sys.path.append('/home/moonrider1968/bots/Unified_Brain')
from brain import assess_regime, decide_action
# -------------------------------
"""

for bot in final_bots:
    if os.path.exists(bot):
        with open(bot, "r") as f:
            content = f.read()
        
        if "from brain import assess_regime" not in content:
            with open(bot, "w") as f:
                f.write(patch_code + content)
            print(f"✅ Integrated Brain into: {bot}")
