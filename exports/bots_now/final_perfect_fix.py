import re

bot_path = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"

with open(bot_path, "r") as f:
    content = f.read()

# We look for the line 'def process_execution_loop(self):' 
# and replace the broken block that follows it.
target_pattern = r"(def process_execution_loop\(self\):.*?log\.info\(.*?\))[\s\S]*?(?=if __name__ ==)"

replacement = r"""\1
        while True:
            signal = get_optimized_signal(df)
            if signal == 'WAIT':
                print('🔍 [Unified Brain] WAIT - Regime unsuitable')
                continue
"""

# Apply the fix
fixed_content = re.sub(target_pattern, replacement, content, flags=re.DOTALL)

with open(bot_path, "w") as f:
    f.write(fixed_content)

print("✅ Perfect indentation applied.")
