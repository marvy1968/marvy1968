file_path = "/home/moonrider1968/orchestrator/deploy_brain_fleet.py"

with open(file_path, "r") as f:
    code = f.read()

# Replace any broken proposal loop header/body with a clean, perfectly indented loop
target_loop = """            for prop in proposals:
                # --- ROI MASTER OPTIMIZATION: HOUR & LOSS TRUNCATION FILTER ---
                now_utc = datetime.now(timezone.utc)
                current_hour = now_utc.hour
                toxic_hours = [6, 10, 13, 18]
                expert_id = prop.get('expert_id', '')
                if current_hour in toxic_hours and expert_id in ['BOT-2', 'BOT-3']:
                    logging.info(f"🚫 [HOUR FILTER] Suppressing {expert_id} counter-trend trade during high-drag UTC hour {current_hour}:00")
                    continue"""

# Strip out corrupted fragments
lines = code.split("\n")
cleaned_lines = []
skip = False

for line in lines:
    if "for prop in proposals:" in line:
        cleaned_lines.append(target_loop)
        skip = True
    elif skip:
        # Skip until we hit the next block inside the loop (e.g., MTF veto or expert evaluation)
        if line.strip().startswith("#") or line.strip().startswith("now_utc") or line.strip().startswith("current_hour") or line.strip().startswith("toxic_hours") or line.strip().startswith("expert_id") or line.strip().startswith("if current_hour") or line.strip().startswith("logging.info") or line.strip().startswith("continue") or line.strip() == "":
            continue
        else:
            skip = False
            cleaned_lines.append(line)
    else:
        cleaned_lines.append(line)

with open(file_path, "w") as f:
    f.write("\n".join(cleaned_lines))

print("✅ deploy_brain_fleet.py successfully repaired!")
