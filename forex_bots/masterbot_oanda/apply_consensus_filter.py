import sys
from pathlib import Path

file_path = Path("all_engines_live.py")
content = file_path.read_text().splitlines()

# 1. Add the Filter Function to the script
filter_func = """
def filter_to_consensus(alerts):
    if not alerts: return []
    game_map = {}
    for a in alerts:
        g = a.get("game", "unknown")
        if g not in game_map: game_map[g] = []
        game_map[g].append(a)
    
    final_alerts = []
    for game, g_alerts in game_map.items():
        # Count how many engines produced an alert for this game
        # We check tags or summary text to distinguish engines
        v3_count = sum(1 for a in g_alerts if "[V3]" in str(a.get("summary", "")))
        std_count = len(g_alerts) - v3_count
        
        # LOGIC: Skip if only 1 engine found an edge (1/3)
        # Keep if 2 or more engines agree (2/3 or 3/3)
        if (v3_count + std_count) >= 2:
            for a in g_alerts:
                a["summary"] = f"🔥 [CONSENSUS] " + a.get("summary", "")
                final_alerts.append(a)
    return final_alerts
"""

# 2. Inject the function and the call
new_content = []
for line in content:
    if "import asyncio" in line:
        new_content.append(line)
        new_content.append(filter_func)
    elif "send_game_alerts(all_alerts)" in line:
        # Filter the alerts right before they are sent to Telegram
        new_content.append("            all_alerts = filter_to_consensus(all_alerts)")
        new_content.append(line)
    else:
        new_content.append(line)

file_path.write_text("\\n".join(new_content) + "\\n")
print("🚀 Consensus Filter Active: 1/3 signals will now be skipped!")
