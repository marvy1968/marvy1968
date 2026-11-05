import re

bot_path = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"

with open(bot_path, "r") as f:
    content = f.read()

# Find the main loop and inject the check
# This regex looks for a typical while loop pattern
new_content = re.sub(r"(while True:)", r"\1\n    signal = get_optimized_signal(df)\n    if signal == 'WAIT': print('🔍 [Unified Brain] WAIT - Regime unsuitable'); continue", content)

with open(bot_path, "w") as f:
    f.write(new_content)
print("✅ Forced signal check into main loop.")
