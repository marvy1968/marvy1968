file_path = "/home/moonrider1968/orchestrator/deploy_brain_fleet.py"

with open(file_path, "r") as f:
    lines = f.readlines()

new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    if "for prop in proposals:" in line:
        new_lines.append(line)
        i += 1
        # Skip any corrupted/duplicate filter lines right after the for loop header
        while i < len(lines) and ("toxic_hours" in lines[i] or "now_utc = datetime" in lines[i] or "current_hour =" in lines[i] or "expert_id =" in lines[i] or "if current_hour in toxic_hours" in lines[i] or "logging.info" in lines[i] or "continue" in lines[i]):
            i += 1
        
        # Inject properly indented ROI block (12 spaces)
        indent = "            "
        new_lines.append(f"{indent}# --- ROI MASTER OPTIMIZATION: HOUR & LOSS TRUNCATION FILTER ---\n")
        new_lines.append(f"{indent}now_utc = datetime.now(timezone.utc)\n")
        new_lines.append(f"{indent}current_hour = now_utc.hour\n")
        new_lines.append(f"{indent}toxic_hours = [6, 10, 13, 18]\n")
        new_lines.append(f"{indent}expert_id = prop.get('expert_id', '')\n")
        new_lines.append(f"{indent}if current_hour in toxic_hours and expert_id in ['BOT-2', 'BOT-3']:\n")
        new_lines.append(f"{indent}    logging.info(f\"🚫 [HOUR FILTER] Suppressing {{expert_id}} counter-trend trade during high-drag UTC hour {{current_hour}}:00\")\n")
        new_lines.append(f"{indent}    continue\n")
        continue
    else:
        new_lines.append(line)
        i += 1

with open(file_path, "w") as f:
    f.writelines(new_lines)

print("✅ deploy_brain_fleet.py proposal loop cleaned and rebuilt!")
