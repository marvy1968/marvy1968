import re

bot_path = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"

with open(bot_path, "r") as f:
    content = f.read()

# We need to move the signal check inside the symbol loop
# This looks for the loop structure and injects the signal check correctly
fixed_content = re.sub(
    r"while True:[\s\S]*?for symbol in Config\.UNIVERSE:.*?df = self\.calculate_regime_matrix\(self\.fetch_market_data\(symbol\)\)",
    r"while True:\n            for symbol in Config.UNIVERSE:\n                df = self.calculate_regime_matrix(self.fetch_market_data(symbol))\n                signal = get_optimized_signal(df)\n                if signal == 'WAIT':\n                    print('🔍 [Unified Brain] WAIT - Regime unsuitable')\n                    continue",
    content
)

with open(bot_path, "w") as f:
    f.write(fixed_content)
print("✅ Loop logic re-structured.")
