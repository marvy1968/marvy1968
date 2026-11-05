import os
import shutil

# 1. Create second experimental safety copy
shutil.copy(
    "/home/moonrider1968/bots/run_config_b_d3_integration.py",
    "/home/moonrider1968/bots/run_config_b_d3_integration_PRE_D3_PATCH.py"
)

with open("/home/moonrider1968/bots/run_config_b_d3_integration_BASELINE_COPY.py", "r") as f:
    code = f.read()

# Verify baseline copy sha256
import hashlib
h_base = hashlib.sha256(code.encode('utf-8')).hexdigest()
print(f"Base code SHA256: {h_base}")

# Add Donchian constants and universe after ALL_SYMBOLS
donchian_block = """
DONCHIAN_SATELLITE_ENABLED = True
DONCHIAN_CORR_CAP = 0.80
DONCHIAN_MAX_POSITIONS = 3
DONCHIAN_LOOKBACK = 55
DONCHIAN_STOP_ATR = 2.00
DONCHIAN_RATCHET_R = 1.50

DONCHIAN_SATELLITE_UNIVERSE = [
    "MSFT", "NVDA", "AVGO", "AMD", "CRM",
    "TSLA", "AMZN", "GOOGL", "META", "AAPL",
    "NFLX", "SNOW", "PLTR", "DDOG", "CRWD",
    "NET", "PANW"
]
"""

# Inject constants
target_str = 'ALL_SYMBOLS = sorted(list(set(XLK_LONG_UNIVERSE + STAGE4_SHORT_FLEET + ["SPY"])))'
if target_str in code:
    code = code.replace(target_str, target_str + "\n" + donchian_block)
else:
    raise RuntimeError("Target string for constants injection not found.")

# We need to wrap the simulation into a function `run_adaptive_simulation(donchian_risk_pct=0.0)`
# Let's inspect where the main simulation body starts and ends in the baseline copy.
print("Constants injected successfully.")
