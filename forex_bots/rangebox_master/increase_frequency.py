import re

bot_path = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"

with open(bot_path, "r") as f:
    content = f.read()

# Lowering the sigma threshold from 1.5 to 0.8
new_content = content.replace("dev.iloc[-1] > 1.5", "dev.iloc[-1] > 0.8")
new_content = new_content.replace("dev.iloc[-1] < -1.5", "dev.iloc[-1] < -0.8")

with open(bot_path, "w") as f:
    f.write(new_content)
print("✅ Alpha threshold lowered to 0.8 sigma for higher frequency.")
