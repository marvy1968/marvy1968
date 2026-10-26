file_path = "/home/moonrider1968/orchestrator/deploy_brain_fleet.py"
with open(file_path, "r") as f:
    code = f.read()

if "from datetime import datetime, timezone" not in code and "import datetime" not in code:
    code = "from datetime import datetime, timezone\n" + code

with open(file_path, "w") as f:
    f.write(code)

print("✅ datetime and timezone imported successfully into deploy_brain_fleet.py!")
