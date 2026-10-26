import os

file_name = "all_engines_live.py"
with open(file_name, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
# 1. Identify and remove the broken injected block first
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

# 2. Re-inject with matched indentation
final_lines = []
for line in new_lines:
    final_lines.append(line)
    if 'game = f"{away_team} @ {home_team}"' in line:
        # Detect leading spaces
        indent = line[:line.find('game =')]
        
        # Build the new block with matching indent
        final_lines.append(f"{indent}# --- HORIZON FILTER (Skip games > 12h away) ---\n")
        final_lines.append(f"{indent}from datetime import datetime, timezone\n")
        final_lines.append(f"{indent}c_time = event.get('commence_time') if 'event' in locals() else None\n")
        final_lines.append(f"{indent}if c_time:\n")
        final_lines.append(f"{indent}    dt = datetime.fromisoformat(c_time.replace('Z', '+00:00'))\n")
        final_lines.append(f"{indent}    if (dt - datetime.now(timezone.utc)).total_seconds() / 3600 > 12:\n")
        final_lines.append(f"{indent}        continue\n")
        final_lines.append(f"{indent}# ----------------------------------------------\n")

with open(file_name, "w", encoding="utf-8") as f:
    f.writelines(final_lines)

print("✅ SUCCESS: Indentation matched and fixed!")
