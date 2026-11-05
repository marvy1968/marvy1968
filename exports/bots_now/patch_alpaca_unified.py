import os

# Define the targets (update if your paths differ)
alpaca_scripts = [
    "/home/moonrider1968/bots/Alpaca_Stocks/stock_beast.py",
    "/home/moonrider1968/bots/Alpaca_Crypto/crypto_beast.py"
]

patch_code = """
# --- INJECTED BY UNIFIED BRAIN ---
import sys
sys.path.append('/home/moonrider1968/bots/Unified_Brain')
from brain import assess_regime, decide_action
# ---------------------------------
"""

for script in alpaca_scripts:
    if os.path.exists(script):
        with open(script, "r") as f:
            lines = f.readlines()
        
        # Check if already patched
        if "from brain import assess_regime" not in "".join(lines):
            lines.insert(0, patch_code)
            with open(script, "w") as f:
                f.writelines(lines)
            print(f"✅ Patched: {script}")
    else:
        print(f"⚠️ Not Found: {script}")
