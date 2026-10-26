import os

file_name = "all_engines_live.py"
with open(file_name, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
in_old_block = False

for line in lines:
    # 1. Strip out the old, non-working filter block
    if "# --- HORIZON FILTER" in line:
        in_old_block = True
        continue
    if in_old_block and "# ----------------------------------------------" in line:
        in_old_block = False
        continue
    if in_old_block:
        continue
        
    new_lines.append(line)
    
    # 2. Inject the correct filter right after the 'game =' line
    if 'game = f"{away_team} @ {home_team}"' in line:
        indent = "    " # Based on your sed output
        new_lines.append(f"{indent}# --- HORIZON FILTER (Payload Match) ---\n")
        new_lines.append(f"{indent}from datetime import datetime, timezone\n")
        new_lines.append(f"{indent}c_time = payload.get('commence_time')\n")
        new_lines.append(f"{indent}if c_time:\n")
        new_lines.append(f"{indent}    dt = datetime.fromisoformat(c_time.replace('Z', '+00:00'))\n")
        new_lines.append(f"{indent}    if (dt - datetime.now(timezone.utc)).total_seconds() / 3600 > 12:\n")
        new_lines.append(f"{indent}        return [] # Skip this game entirely\n")
        new_lines.append(f"{indent}# --------------------------------------\n")

with open(file_name, "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print("✅ SUCCESS: Filter now correctly uses 'payload' and returns [].")
