import re

bot_path = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"

with open(bot_path, "r") as f:
    content = f.read()

# Define the correct block as a string
correct_block = """    while True:
        signal = get_optimized_signal(df)
        if signal == 'WAIT':
            print('🔍 [Unified Brain] WAIT - Regime unsuitable')
            continue
"""

# Regex to find the broken loop and replace it
# This looks for 'while True:' and everything up to the next non-indented block
pattern = r"while True:[\s\S]*?(?=    def |    class |if __name__)"
fixed_content = re.sub(pattern, correct_block, content)

with open(bot_path, "w") as f:
    f.write(fixed_content)
print("✅ Final indentation fix applied.")
