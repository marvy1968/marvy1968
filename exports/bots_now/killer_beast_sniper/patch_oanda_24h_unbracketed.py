import os
import re
from datetime import datetime, timedelta

EXECUTOR_PATH = "/home/moonrider1968/bots/killer_beast_sniper/oanda_forex_executor.py"

# Calculate 24-Hour Timer Window
now = datetime.now()
expiry_time = now + timedelta(hours=24)
expiry_str = expiry_time.strftime("%Y-%m-%d %H:%M:%S")

print("=" * 75)
print("⏱️ OANDA 24-HOUR UNBRACKETED EXPERIMENTAL PATCH")
print("=" * 75)
print(f"  • Start Time : {now.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"  • Expiry Time: {expiry_str} (Auto-restores SL/TP after this)")
print("=" * 75)

UNBRACKETED_LOGIC = f'''
# ==============================================================================
# 🔓 24-HOUR TEMPORARY UNBRACKETED TELEMETRY MODE
# ==============================================================================
from datetime import datetime

EXPIRATION_TIMESTAMP = "{expiry_str}"

def is_unbracketed_active():
    """Checks if the 24-hour unbracketed window is currently active."""
    expiry = datetime.strptime(EXPIRATION_TIMESTAMP, "%Y-%m-%d %H:%M:%S")
    return datetime.now() < expiry
# ==============================================================================
'''

with open(EXECUTOR_PATH, "r") as f:
    code = f.read()

# Inject expiry module if not present
if "EXPIRATION_TIMESTAMP" not in code:
    code = UNBRACKETED_LOGIC + "\n" + code

# Modify execution function to bypass SL/TP if window is active
bypass_code = '''
        # Check 24-Hour Temporary Safety Override Window
        if is_unbracketed_active():
            sl_price, tp_price = None, None
            print(f"🔓 [OANDA 24H UNBRACKETED MODE ACTIVE] {symbol} | SL/TP Removed until {EXPIRATION_TIMESTAMP}")
'''

if "is_unbracketed_active()" in code and "sl_price, tp_price = None, None" not in code:
    # Insert bypass right before building order spec
    code = code.replace('order_spec = {', bypass_code + '\n        order_spec = {')

with open(EXECUTOR_PATH, "w") as f:
    f.write(code)

print("✅ Injected 24-Hour Temporary Unbracketed logic into oanda_forex_executor.py!")
