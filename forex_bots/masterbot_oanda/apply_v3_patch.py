import sys
from pathlib import Path

file_path = Path("all_engines_live.py")
content = file_path.read_text()

# 1. Add the Import
if "import evaluate_team_props_v3" not in content:
    content = "from team_props_engine_v3 import evaluate_team_props_v3\n" + content
    print("✅ Added V3 Import")

# 2. Inject the Logic into the main loop
# We look for where the 'payload' is built to drop our logic right after it
target_line = "all_alerts.extend(run_signal_pipeline(payload))"
v3_logic = """
            # --- V3 TEAM PROPS INJECTION ---
            tp_results = evaluate_team_props_v3(payload)
            tp_alerts = tp_results.get("alerts", [])
            if tp_alerts:
                all_alerts.extend(tp_alerts)
                print(f"  [V3] Found {len(tp_alerts)} Team Prop edges for {game_name}")
"""

if target_line in content and "V3 TEAM PROPS INJECTION" not in content:
    content = content.replace(target_line, target_line + v3_logic)
    print("✅ Injected V3 Logic Loop")

file_path.write_text(content)
print("🚀 Patch complete! all_engines_live.py is now Dual-Engine.")
