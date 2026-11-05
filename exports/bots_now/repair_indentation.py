import re

bot_path = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"

with open(bot_path, "r") as f:
    content = f.read()

# Fix the specific indentation error by ensuring 4 spaces
# Find the line that caused the error and re-indent it
fixed_content = content.replace("signal = get_optimized_signal(df)", "    signal = get_optimized_signal(df)")
fixed_content = fixed_content.replace("if signal == 'WAIT': print('🔍 [Unified Brain] WAIT - Regime unsuitable'); continue", 
                                      "    if signal == 'WAIT': print('🔍 [Unified Brain] WAIT - Regime unsuitable'); continue")

with open(bot_path, "w") as f:
    f.write(fixed_content)
print("✅ Indentation corrected.")
