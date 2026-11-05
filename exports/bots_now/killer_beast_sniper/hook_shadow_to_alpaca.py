import re

SHADOW_FILE = "/home/moonrider1968/bots/killer_beast_sniper/multi_scalper_shadow.py"

with open(SHADOW_FILE, "r") as f:
    content = f.read()

# Add import for alpaca_paper_executor if not present
import_line = "from alpaca_paper_executor import submit_alpaca_bracket_order\n"

if "from alpaca_paper_executor import submit_alpaca_bracket_order" not in content:
    content = import_line + content

# Inject execution call right after [SHADOW MATCH] logging for STOCKS
target_str = 'log_event(f"🧪 [SHADOW MATCH] Class: {asset_class:<6} | Asset: {asset:<9} | Strat: {strat:<20} | Side: {side:<4} | Price: {price}")'

replacement_str = target_str + """
            if asset_class == "STOCKS":
                try:
                    submit_alpaca_bracket_order(asset, strat, side, float(price))
                except Exception as exec_err:
                    log_event(f"❌ Execution trigger error for {asset}: {exec_err}")"""

if target_str in content and "submit_alpaca_bracket_order" not in content.split("log_event(f\"🧪 [SHADOW MATCH]")[1]:
    new_content = content.replace(target_str, replacement_str)
    with open(SHADOW_FILE, "w") as f:
        f.write(new_content)
    print("✅ Successfully hooked submit_alpaca_bracket_order into multi_scalper_shadow.py!")
else:
    print("ℹ️ Check completed: Shadow loop already hooked or target line structure adjusted.")
