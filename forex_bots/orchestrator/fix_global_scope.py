file_path = "/home/moonrider1968/orchestrator/deploy_brain_fleet.py"
with open(file_path, "r") as f:
    code = f.read()

if "global HARD_BREAKER_UNTIL" not in code:
    lines = code.split("\n")
    new_lines = []
    for line in lines:
        if line.startswith("def main_brain_router("):
            new_lines.append(line)
            new_lines.append("    global HARD_BREAKER_UNTIL, SAFETY_PAUSE_UNTIL, SESSION_START_EQUITY, CONSECUTIVE_LOSSES")
        else:
            new_lines.append(line)
    code = "\n".join(new_lines)

with open(file_path, "w") as f:
    f.write(code)

print("✅ Global scope fix applied successfully to deploy_brain_fleet.py!")
