file_path = "/home/moonrider1968/orchestrator/deploy_brain_fleet.py"

with open(file_path, "r") as f:
    content = f.read()

# Clean out improperly indented toxic_hours block if present
clean_content = content.replace("""for prop in proposals:

            # --- ROI MASTER OPTIMIZATION: HOUR & LOSS TRUNCATION FILTER ---
            now_utc = datetime.now(timezone.utc)
            current_hour = now_utc.hour
            
            # Block counter-trend proposals during high-drawdown choppy hours (UTC 06, 10, 13, 18)
            toxic_hours = [6, 10, 13, 18]
            expert_id = prop.get('expert_id', '')
            if current_hour in toxic_hours and expert_id in ['BOT-2', 'BOT-3']:
                logging.info(f"🚫 [HOUR FILTER] Suppressing {expert_id} counter-trend trade during high-drag UTC hour {current_hour}:00")
                continue""", "for prop in proposals:")

# Re-inject with precise 12-space indentation
fixed_loop = """for prop in proposals:
            # --- ROI MASTER OPTIMIZATION: HOUR & LOSS TRUNCATION FILTER ---
            now_utc = datetime.now(timezone.utc)
            current_hour = now_utc.hour
            toxic_hours = [6, 10, 13, 18]
            expert_id = prop.get('expert_id', '')
            if current_hour in toxic_hours and expert_id in ['BOT-2', 'BOT-3']:
                logging.info(f"🚫 [HOUR FILTER] Suppressing {expert_id} counter-trend trade during high-drag UTC hour {current_hour}:00")
                continue"""

if "toxic_hours" not in clean_content:
    clean_content = clean_content.replace("for prop in proposals:", fixed_loop)

with open(file_path, "w") as f:
    f.write(clean_content)

print("✅ Indentation successfully repaired in deploy_brain_fleet.py!")
