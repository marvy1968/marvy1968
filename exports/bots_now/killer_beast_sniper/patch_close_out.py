import re

daemon_path = "/home/moonrider1968/bots/killer_beast_sniper/velez_stage4_short_daemon.py"

with open(daemon_path, "r") as f:
    code = f.read()

# Helper function to check 3:50 PM ET cutoff & handle auto-close
close_logic = """
def is_market_near_close():
    \"\"\"Returns True if market is within 10 minutes of close (3:50 PM - 4:00 PM ET).\"\"\"
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.datetime.now(et_tz)
    
    close_buffer = now_et.replace(hour=15, minute=50, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    
    return close_buffer <= now_et < market_close

def close_all_daemon_positions():
    \"\"\"Flushes all open short positions and cancels working orders before close.\"\"\"
    try:
        # Cancel working open orders
        trading_client.cancel_orders()
        logging.info("🧹 Canceled all working open orders for 3:50 PM ET close buffer.")
        
        # Close all active positions
        positions = trading_client.get_all_positions()
        for pos in positions:
            if pos.symbol in UNIVERSE and float(pos.qty) < 0: # Short position check
                trading_client.close_position(pos.symbol)
                logging.info(f"🚨 Closed short position for {pos.symbol} at 3:50 PM ET buffer.")
    except Exception as e:
        logging.error(f"Error during 3:50 PM ET position flush: {e}")
"""

if "def is_market_near_close" not in code:
    code = close_logic + "\n" + code

# Inject check into the main trade evaluation block
if "is_market_near_close()" not in code:
    pattern = r"(def\s+execute_short_trade\([^)]*\):|def\s+submit_order\([^)]*\):)"
    replacement = r"\1\n    # 3:50 PM ET Close Buffer Check\n    if is_market_near_close():\n        close_all_daemon_positions()\n        logging.info(f'⛔ Market near close (3:50 PM ET). Holding new entry for {symbol}.')\n        return None\n"
    code, count = re.subn(pattern, replacement, code, count=1)

with open(daemon_path, "w") as f:
    f.write(code)

print("✅ 3:50 PM ET auto-close patch applied successfully!")
