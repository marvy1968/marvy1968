import os

file_name = "all_engines_live.py"
with open(file_name, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
in_old_block = False
for line in lines:
    if "# --- HORIZON FILTER" in line:
        in_old_block = True
        continue
    if in_old_block and "# ----------------------------------------------" in line:
        in_old_block = False
        continue
    if not in_old_block:
        new_lines.append(line)

final_lines = []
for line in new_lines:
    final_lines.append(line)
    if 'game = f"{away_team} @ {home_team}"' in line:
        indent = line[:line.find('game =')]
        
        # Using 'return' instead of 'continue' because we are inside a function
        final_lines.append(f"{indent}# --- HORIZON FILTER (Skip games > 12h away) ---\n")
        final_lines.append(f"{indent}from datetime import datetime, timezone\n")
        # Try a few common variable names just in case
        final_lines.append(f"{indent}target_obj = event if 'event' in locals() else (game_data if 'game_data' in locals() else None)\n")
        final_lines.append(f"{indent}c_time = target_obj.get('commence_time') if target_obj else None\n")
        final_lines.append(f"{indent}if c_time:\n")
        final_lines.append(f"{indent}    dt = datetime.fromisoformat(c_time.replace('Z', '+00:00'))\n")
        final_lines.append(f"{indent}    if (dt - datetime.now(timezone.utc)).total_seconds() / 3600 > 12:\n")
        final_lines.append(f"{indent}        return # End function for this game (Too far away)\n")
        final_lines.append(f"{indent}# ----------------------------------------------\n")

with open(file_name, "w", encoding="utf-8") as f:
    f.writelines(final_lines)

print("✅ SUCCESS: Horizon Filter updated with 'return' logic!")
