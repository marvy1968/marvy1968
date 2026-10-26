import os

file_name = "all_engines_live.py"
with open(file_name, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
injected = False

# This logic uses the variables already present at Line 406
filter_code = [
    "            # --- HORIZON FILTER (Skip games > 12h away) ---\n",
    "            from datetime import datetime, timezone\n",
    "            c_time = event.get('commence_time') if 'event' in locals() else None\n",
    "            if c_time:\n",
    "                dt = datetime.fromisoformat(c_time.replace('Z', '+00:00'))\n",
    "                if (dt - datetime.now(timezone.utc)).total_seconds() / 3600 > 12:\n",
    "                    continue\n",
    "            # ----------------------------------------------\n"
]

for line in lines:
    new_lines.append(line)
    # Target Line 406 area (the game name definition)
    if 'game = f"{away_team} @ {home_team}"' in line and not injected:
        new_lines.extend(filter_code)
        injected = True

if injected:
    with open(file_name, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print("✅ SUCCESS: Horizon Filter installed at the game loop anchor!")
else:
    print("⚠️ FAILED: Could not find the anchor line in all_engines_live.py.")
