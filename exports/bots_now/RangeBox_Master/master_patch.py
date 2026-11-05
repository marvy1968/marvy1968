import os

file_path = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"

with open(file_path, "r") as f:
    code = f.read()

# 1. Add Imports
if "from brain import assess_regime, decide_action" not in code:
    code = "from brain import assess_regime, decide_action\n" + code

# 2. Inject the Logic Wrapper (Replaces raw trade triggers)
# This identifies your entry point and wraps it in the brain's decision matrix
target_logic = "if price <= row['bb_lower'] and symbol_pos_count == 0:"
wrapper = """
                # Unified Brain Assessment
                regime, bias = assess_regime(df)
                if decide_action(regime, bias, "BUY") and price <= row['bb_lower'] and symbol_pos_count == 0:"""

if wrapper not in code:
    code = code.replace(target_logic, wrapper)
    
# 3. Add Trend-Ride Suppression
trend_rule = """
                    # MOMENTUM HOLD RULE
                    if regime == "TRENDING" and row['adx'] > 30:
                        log.info(f"🚀 [TREND RIDE] {symbol} momentum strong. Suppressing TP.")
                        continue
"""
code = code.replace("if pos['type'] in ['BULL_SCALP', 'BEAR_SCALP'] and row['adx'] > 30:", 
                    "if regime == 'TRENDING' and row['adx'] > 30:")

with open(file_path, "w") as f:
    f.write(code)

print("✅ Master Patch Applied: Logic unified, brain imported, and trend-ride rules active.")
