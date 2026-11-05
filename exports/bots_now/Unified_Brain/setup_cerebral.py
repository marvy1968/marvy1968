import os

brain_dir = "/home/moonrider1968/bots/Unified_Brain"

# 1. The Forex Brain
with open(f"{brain_dir}/forex_brain.py", "w") as f:
    f.write("ADX_THRESHOLD = 25\nSMA_WINDOW = 200\nVOLATILITY_FACTOR = 1.0\n")

# 2. The Crypto Brain
with open(f"{brain_dir}/crypto_brain.py", "w") as f:
    f.write("ADX_THRESHOLD = 40\nSMA_WINDOW = 50\nVOLATILITY_FACTOR = 2.5\n")

# 3. The Stock Brain
with open(f"{brain_dir}/stock_brain.py", "w") as f:
    f.write("ADX_THRESHOLD = 30\nSMA_WINDOW = 200\nVOLATILITY_FACTOR = 1.5\n")

print("✅ Cerebral Architecture deployed.")
