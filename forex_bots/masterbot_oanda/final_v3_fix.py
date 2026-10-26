import sys
from pathlib import Path

file_path = Path("all_engines_live.py")
content = file_path.read_text().splitlines()

# 1. Clean up and Fix Import Order
new_content = []
v3_import = "from team_props_engine_v3 import evaluate_team_props_v3"

# Remove any misplaced V3 imports first
content = [line for line in content if v3_import not in line]

for i, line in enumerate(content):
    new_content.append(line)
    # Insert V3 import right after the __future__ line
    if "from __future__ import annotations" in line:
        new_content.append(v3_import)

# 2. Inject the Logic (Smarter Search)
final_lines = []
injected = False
v3_logic = [
    "            # --- V3 TEAM PROPS INJECTION ---",
    "            tp_results = evaluate_team_props_v3(payload)",
    "            tp_alerts = tp_results.get('alerts', [])",
    "            if tp_alerts:",
    "                all_alerts.extend(tp_alerts)",
    "                print(f'  [V3] Found {len(tp_alerts)} Team Prop edges for {game_name}')"
]

for line in new_content:
    final_lines.append(line)
    # We look for the main pipeline call to drop our V3 logic right after it
    if "run_signal_pipeline(payload)" in line and "V3 TEAM PROPS INJECTION" not in line:
        final_lines.extend(v3_logic)
        injected = True

if injected:
    print("✅ Successfully injected V3 Logic into the live loop!")
else:
    print("⚠️  Warning: Could not find injection point. Standard logic will run, but V3 might be silent.")

file_path.write_text("\n".join(final_lines) + "\n")
print("🚀 Syntax fixed! all_engines_live.py is ready to roll.")
