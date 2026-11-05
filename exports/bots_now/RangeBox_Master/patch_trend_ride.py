#!/usr/bin/env python3
file_path = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"

with open(file_path, "r") as f:
    code = f.read()

# Define the patch that suppresses TP if ADX is high
patch_rule = """
                    # --- MOMENTUM TREND RIDE RULE ---
                    # If ADX is spiking (> 30), suppress TP to let the trend ride
                    if pos['type'] in ['BULL_SCALP', 'BEAR_SCALP'] and row['adx'] > 30:
                        log.info(f"🚀 [TREND RIDE] {symbol} momentum strong (ADX: {row['adx']:.1f}). Suppressing TP target.")
                        continue
"""

# Inject this logic *before* the TP check logic
target_section = """                    # Compute the aggressive volatility runway trailing stop boundary
                    dynamic_stop = self.calculate_dynamic_trailing_stop(pos['dir'], entry, price, row['atr'])"""

if patch_rule not in code:
    code = code.replace(target_section, target_section + patch_rule)
    with open(file_path, "w") as f:
        f.write(code)
    print("✅ Success: Trend-Ride suppression logic injected.")
else:
    print("⚠️ Patch already applied.")
