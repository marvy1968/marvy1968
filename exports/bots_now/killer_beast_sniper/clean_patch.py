import re
import shutil

backup_path = "/home/moonrider1968/bots/killer_beast_sniper/velez_stage4_short_daemon.py.bak"
daemon_path = "/home/moonrider1968/bots/killer_beast_sniper/velez_stage4_short_daemon.py"

# 1. Restore fresh copy from pristine backup
shutil.copyfile(backup_path, daemon_path)

with open(daemon_path, "r") as f:
    code = f.read()

# 2. Inject standard imports at top
imports_to_add = "import uuid\nfrom institutional_gate import evaluate_institutional_gate\n"
code = imports_to_add + code

# 3. Cleanly inject Institutional Gate check
if "evaluate_institutional_gate(" not in code:
    pattern = r"(def\s+execute_short_trade\([^)]*\):|def\s+submit_order\([^)]*\):)"
    replacement = r"\1\n    # Institutional Dark Pool Gatekeeper\n    if not evaluate_institutional_gate(symbol):\n        logging.info(f'🛑 Trade blocked by Institutional Gatekeeper for {symbol}')\n        return None\n"
    code, count = re.subn(pattern, replacement, code, count=1)
    if count == 0:
        code = code.replace("trading_client.submit_order(", "if not evaluate_institutional_gate(symbol): return None\n        trading_client.submit_order(")

# 4. Safely overwrite client_order_id assignments with a pure clean expression
code = re.sub(
    r'client_order_id\s*=\s*.*',
    'client_order_id=f"v4_{symbol}_{uuid.uuid4().hex[:8]}",',
    code
)

with open(daemon_path, "w") as f:
    f.write(code)

print("✅ File restored and clean patch applied!")
