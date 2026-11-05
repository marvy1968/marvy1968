bot_path = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"

with open(bot_path, "r") as f:
    content = f.read()

# Replace the broken loop line with a clean version
# We target the specific sequence that is causing the syntax error
new_content = content.replace('log.info("📡 UNIFIED BEAST ONLINE")                        while True:', 
                               'log.info("📡 UNIFIED BEAST ONLINE")\n        while True:')

with open(bot_path, "w") as f:
    f.write(new_content)
print("✅ Loop header split successfully.")
