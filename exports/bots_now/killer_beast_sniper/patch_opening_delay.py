import re

daemon_path = "/home/moonrider1968/bots/killer_beast_sniper/velez_stage4_short_daemon.py"

with open(daemon_path, "r") as f:
    code = f.read()

# 1. Add datetime/pytz import and helper function if missing
delay_function = """
import datetime
import pytz

def is_past_opening_whipsaw():
    \"\"\"Returns True if market is past the 9:45 AM ET opening volatility window.\"\"\"
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.datetime.now(et_tz)
    
    # Check if weekday (0=Mon, 4=Fri)
    if now_et.weekday() > 4:
        return True
        
    safe_time = now_et.replace(hour=9, minute=45, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    
    # If during trading hours (9:30 AM - 4:00 PM ET), hold until 9:45 AM ET
    if now_et.time() >= datetime.time(9, 30) and now_et < safe_time:
        return False
    return True
"""

if "def is_past_opening_whipsaw" not in code:
    code = delay_function + "\n" + code

# 2. Inject delay check right alongside the Institutional Gate check
if "is_past_opening_whipsaw()" not in code:
    pattern = r"(def\s+execute_short_trade\([^)]*\):|def\s+submit_order\([^)]*\):)"
    replacement = r"\1\n    # 15-Min Opening Whipsaw Guard\n    if not is_past_opening_whipsaw():\n        logging.info(f'⏳ Opening 15-min whipsaw buffer active (9:30-9:45 AM ET). Holding trade for {symbol}.')\n        return None\n"
    code, count = re.subn(pattern, replacement, code, count=1)

with open(daemon_path, "w") as f:
    f.write(code)

print("✅ Opening 15-minute delay patch applied successfully!")
