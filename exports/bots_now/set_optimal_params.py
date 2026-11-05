bot_path = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"

with open(bot_path, "r") as f:
    content = f.read()

# Update thresholds
content = content.replace("df['adx'].iloc[-1] < 20", "df['adx'].iloc[-1] < 25")
content = content.replace("df['rsi'].iloc[-1] < 35", "df['rsi'].iloc[-1] < 30")
content = content.replace("df['rsi'].iloc[-1] > 65", "df['rsi'].iloc[-1] > 65")

with open(bot_path, "w") as f:
    f.write(content)
print("✅ Optimal thresholds applied (ADX 25, RSI 30/65).")
