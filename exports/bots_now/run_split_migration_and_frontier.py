import os, subprocess
import shutil
from pathlib import Path

CANONICAL = "/home/moonrider1968/bots/run_config_b_d3_integration.py"
RAW_FINAL = "/home/moonrider1968/bots/run_config_b_d3_integration_RAW_FINAL.py"
SPLIT_FILE = "/home/moonrider1968/bots/run_config_b_d3_integration_SPLIT.py"

print("=" * 135)
print(" 🔄 EXECUTING SPLIT-ADJUSTED MIGRATION & RECONCILIATION HARNESS")
print("=" * 135)

# 1. Freeze current raw-data result
shutil.copy(CANONICAL, RAW_FINAL)
subprocess.run(["sha256sum", CANONICAL, RAW_FINAL], check=True)

# 2. Create separate split-adjusted candidate
shutil.copy(CANONICAL, SPLIT_FILE)

p = Path(SPLIT_FILE)
s = p.read_text()
count = s.count('"adjustment": "raw"')
if count == 0:
    raise RuntimeError("No adjustment=raw anchor found.")

s = s.replace('"adjustment": "raw"', '"adjustment": "split"')
p.write_text(s)
print(f"Changed {count} historical-data request(s) from RAW to SPLIT adjustment.")

# 3. Verify adjustment string in SPLIT file
res = subprocess.run(["grep", "-n", '"adjustment"', SPLIT_FILE], capture_output=True, text=True)
print("Adjustment verification in SPLIT file:\n", res.stdout)

compile_res = subprocess.run(["python3", "-m", "py_compile", SPLIT_FILE])
if compile_res.returncode != 0:
    raise RuntimeError("SPLIT file compilation failed.")
print("✅ SPLIT file compiled successfully.")
