import re

SHADOW_PATH = "/home/moonrider1968/bots/killer_beast_sniper/multi_scalper_shadow.py"
EXECUTOR_PATH = "/home/moonrider1968/bots/killer_beast_sniper/alpaca_paper_executor.py"

# 1. Update multi_scalper_shadow.py timeframe mapping for CRYPTO
with open(SHADOW_PATH, "r") as f:
    shadow_content = f.read()

# Ensure crypto timeframe is set to 4Hour / 240Min
if "CRYPTO_TIMEFRAME" in shadow_content:
    shadow_content = re.sub(r'CRYPTO_TIMEFRAME\s*=\s*".*?"', 'CRYPTO_TIMEFRAME = "4Hour"', shadow_content)
else:
    shadow_content = "CRYPTO_TIMEFRAME = \"4Hour\"\n" + shadow_content

with open(SHADOW_PATH, "w") as f:
    f.write(shadow_content)

print("✅ Updated multi_scalper_shadow.py to use '4Hour' timeframe for Crypto.")

# 2. Update alpaca_paper_executor.py crypto SL/TP optimization targets for 4H swings
with open(EXECUTOR_PATH, "r") as f:
    exec_content = f.read()

# Add/Update 4H Crypto Optimization Rules (2% SL / 4% TP for swing setups)
crypto_4h_rules = """
CRYPTO_4H_OPTIMIZATION_MATRIX = {
    "BTC/USD":  {"RANGE_BREAKOUT": {"sl_pct": 0.015, "tp_pct": 0.035}, "VELEZ_20EMA": {"sl_pct": 0.012, "tp_pct": 0.030}},
    "ETH/USD":  {"RANGE_BREAKOUT": {"sl_pct": 0.020, "tp_pct": 0.045}, "VELEZ_20EMA": {"sl_pct": 0.015, "tp_pct": 0.035}},
    "SOL/USD":  {"RANGE_BREAKOUT": {"sl_pct": 0.025, "tp_pct": 0.055}, "VELEZ_20EMA": {"sl_pct": 0.020, "tp_pct": 0.045}},
    "DOGE/USD": {"RANGE_BREAKOUT": {"sl_pct": 0.030, "tp_pct": 0.065}, "VELEZ_20EMA": {"sl_pct": 0.025, "tp_pct": 0.050}},
    "LINK/USD": {"RANGE_BREAKOUT": {"sl_pct": 0.025, "tp_pct": 0.055}, "VELEZ_20EMA": {"sl_pct": 0.020, "tp_pct": 0.040}},
}
"""

if "CRYPTO_4H_OPTIMIZATION_MATRIX" not in exec_content:
    exec_content = crypto_4h_rules + "\n" + exec_content
    with open(EXECUTOR_PATH, "w") as f:
        f.write(exec_content)
    print("✅ Appended 4H Crypto SL/TP Optimization Matrix (1.5%-3.0% SL / 3.0%-6.5% TP).")
else:
    print("ℹ️ 4H Crypto Optimization Matrix already present.")

