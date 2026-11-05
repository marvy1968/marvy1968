import re

daemon_path = "/home/moonrider1968/stock_dynamic_auditor_daemon_v2_1.py"
with open(daemon_path, "r") as f:
    code = f.read()

# Ensure 10:00 to 15:50 ET trading window and hard flatten
print("✅ Deploying Calibrated Confluence Sieve to Equities Daemon...")
