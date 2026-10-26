import re

file_path = "/home/moonrider1968/orchestrator/deploy_brain_fleet.py"
with open(file_path, "r") as f:
    code = f.read()

# 1. Add COOLDOWN_SECONDS variable and tracker dictionary
if "COOLDOWN_SECONDS" not in code:
    code = code.replace(
        "MAX_OPEN_TRADES = 10",
        "MAX_OPEN_TRADES = 10\nCOOLDOWN_SECONDS = 120   # 2-minute post-trade cooldown per expert\nEXPERT_COOLDOWN_TRACKER = {}"
    )

# 2. Add Cooldown Verification logic into proposal processing loop
cooldown_check = """            for prop in proposals:
                expert_id = prop.get("expert_id", "")
                
                # Check Expert Cooldown
                last_exec = EXPERT_COOLDOWN_TRACKER.get(expert_id, 0)
                if time.time() - last_exec < COOLDOWN_SECONDS:
                    logging.info(f"⏳ [COOLDOWN GUARD] {expert_id} is in 120s cooldown period. Skipping execution.")
                    continue"""

if "COOLDOWN GUARD" not in code:
    code = code.replace(
        "            for prop in proposals:\n                expert_id = prop.get(\"expert_id\", \"\")",
        cooldown_check
    )

# 3. Record execution timestamp upon order submission
timestamp_record = """                    OandaMultiTfClient.execute_fifo_order(instrument, final_units, ai_sl, ai_tp, expert_id, reasoning)
                    EXPERT_COOLDOWN_TRACKER[expert_id] = time.time()"""

if "EXPERT_COOLDOWN_TRACKER[expert_id]" not in code:
    code = code.replace(
        "                    OandaMultiTfClient.execute_fifo_order(instrument, final_units, ai_sl, ai_tp, expert_id, reasoning)",
        timestamp_record
    )

with open(file_path, "w") as f:
    f.write(code)

print("✅ 120-second Post-Trade Cooldown Guard successfully added to Executive Brain!")
