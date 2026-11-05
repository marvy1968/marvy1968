import os
import re

listener_path = "/home/moonrider1968/bots/crypto_fleet/telegram_command_listener.py"

circuit_breaker_code = """
import time
import logging

# CIRCUIT BREAKER STATE
consecutive_losses = 0
peak_equity = 0.0
pause_until_time = 0

def check_circuit_breaker(current_equity, trade_result_win):
    \"\"\"
    Evaluates loss streak and total equity drawdown.
    Returns (True, message) if paused, otherwise (False, "").
    \"\"\"
    global consecutive_losses, peak_equity, pause_until_time
    
    current_time = time.time()
    
    # Check if currently in a cooling pause
    if current_time < pause_until_time:
        remaining_min = int((pause_until_time - current_time) / 60)
        return True, f"🛑 Circuit Breaker active. Cooldown remaining: {remaining_min} mins."
    
    # Initialize or update peak equity
    if peak_equity == 0.0 or current_equity > peak_equity:
        peak_equity = current_equity

    # 1. Check 3 Consecutive Losses Rule
    if trade_result_win is False:
        consecutive_losses += 1
        logging.info(f"⚠️ Loss detected. Consecutive losses: {consecutive_losses}/3")
        if consecutive_losses >= 3:
            pause_until_time = current_time + (15 * 60)  # 15 Minute Pause
            consecutive_losses = 0  # Reset counter
            msg = "🚨 CIRCUIT BREAKER TRIGGERED: 3 consecutive losses! Pausing trading for 15 minutes."
            logging.warning(msg)
            return True, msg
    elif trade_result_win is True:
        consecutive_losses = 0  # Reset streak on win

    # 2. Check 1.0% Total Equity Drawdown Rule
    drawdown_pct = (peak_equity - current_equity) / peak_equity
    if drawdown_pct >= 0.01:  # 1.0% Equity drop
        pause_until_time = current_time + (8 * 3600)  # 8 Hour Pause
        peak_equity = current_equity  # Reset peak baseline after trip
        msg = f"🚨 CIRCUIT BREAKER TRIGGERED: Equity dropped {drawdown_pct*100:.2f}% (>= 1.0%). Pausing trading for 8 hours."
        logging.warning(msg)
        return True, msg

    return False, ""
"""

if os.path.exists(listener_path):
    with open(listener_path, "r") as f:
        code = f.read()

    if "check_circuit_breaker" not in code:
        code = circuit_breaker_code + "\n" + code
        with open(listener_path, "w") as f:
            f.write(code)
        print("✅ Circuit Breaker rules injected into telegram_command_listener.py successfully!")
    else:
        print("ℹ️ Circuit Breaker logic already exists in the file.")
else:
    # Save standalone module if path isn't directly present
    cb_file = "/home/moonrider1968/bots/crypto_fleet/circuit_breaker.py"
    with open(cb_file, "w") as f:
        f.write(circuit_breaker_code)
    print(f"✅ Standalone Circuit Breaker module saved to {cb_file}")
