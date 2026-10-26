import re

file_path = "/home/moonrider1968/orchestrator/deploy_brain_fleet.py"
with open(file_path, "r") as f:
    code = f.read()

# Add safety state tracking variables if not present
safety_vars = """
# ==========================================
# ADVANCED SAFETY & CIRCUIT BREAKER STATE
# ==========================================
CONSECUTIVE_LOSSES = 0
SAFETY_PAUSE_UNTIL = 0           # Timestamp for 15-min pause
HARD_BREAKER_UNTIL = 0          # Timestamp for 8-hour pause
SESSION_START_EQUITY = None      # Baseline for 1% drawdown guard
"""

if "CONSECUTIVE_LOSSES" not in code:
    code = code.replace("EXPERT_COOLDOWN_TRACKER = {}", "EXPERT_COOLDOWN_TRACKER = {}\n" + safety_vars)

# Inject Circuit Breakers & Friday Auto-Close logic into evaluation loop
safety_logic = """
            # 1. Check Friday Session End Close (10 mins before close @ 20:50 UTC)
            now_utc = datetime.now(timezone.utc)
            if now_utc.weekday() == 4 and (now_utc.hour > 20 or (now_utc.hour == 20 and now_utc.minute >= 50)):
                logging.warning("⚠️ [FRIDAY AUTO-CLOSE] 10 mins before weekend close! Closing all active trades.")
                OandaMultiTfClient.close_all_positions(ACCOUNT_ID)
                return

            # 2. Check 8-Hour / 15-Min Circuit Breaker Timers
            current_time = time.time()
            if current_time < HARD_BREAKER_UNTIL:
                rem_hrs = (HARD_BREAKER_UNTIL - current_time) / 3600.0
                logging.info(f"🛑 [CIRCUIT BREAKER ACTIVE] 1% Equity Drawdown Pause. Resuming in {rem_hrs:.2f} hours.")
                return

            if current_time < SAFETY_PAUSE_UNTIL:
                rem_mins = (SAFETY_PAUSE_UNTIL - current_time) / 60.0
                logging.info(f"⏳ [SAFETY PAUSE ACTIVE] 3 Consecutive Losses Pause. Resuming in {rem_mins:.1f} minutes.")
                return

            # 3. Dynamic 1% Drawdown Check
            if SESSION_START_EQUITY is None:
                SESSION_START_EQUITY = nav
            
            total_loss_pct = (SESSION_START_EQUITY - nav) / SESSION_START_EQUITY
            if total_loss_pct >= 0.01:  # 1% Total Loss Trigger
                HARD_BREAKER_UNTIL = current_time + (8 * 3600)
                SESSION_START_EQUITY = nav  # Reset baseline for next session
                logging.error(f"🚨 [HARD CIRCUIT BREAKER] 1% Equity Loss Triggered ({total_loss_pct*100:.2f}%). Pausing fleet for 8 Hours!")
                return
"""

if "FRIDAY AUTO-CLOSE" not in code:
    code = code.replace("            for prop in proposals:", safety_logic + "\n            for prop in proposals:")

with open(file_path, "w") as f:
    f.write(code)

print("✅ Safety Circuit Breakers & Friday Auto-Close successfully installed!")
