import re

daemon_path = "/home/moonrider1968/bots/killer_beast_sniper/velez_stage4_short_daemon.py"

with open(daemon_path, "r") as f:
    code = f.read()

# 1. Add import if missing
if "from institutional_gate import evaluate_institutional_gate" not in code:
    import_line = "from institutional_gate import evaluate_institutional_gate\n"
    code = import_line + code

# 2. Inject gate check before order placement
if "evaluate_institutional_gate(" not in code:
    pattern = r"(def\s+execute_short_trade\([^)]*\):|def\s+submit_order\([^)]*\):)"
    replacement = r"\1\n    # Institutional Dark Pool Gatekeeper\n    if not evaluate_institutional_gate(symbol):\n        logging.info(f'🛑 Trade blocked by Institutional Gatekeeper for {symbol}')\n        return None\n"
    code, count = re.subn(pattern, replacement, code, count=1)
    
    if count == 0:
        # Fallback replacement if function signature differs
        code = code.replace("trading_client.submit_order(", "if not evaluate_institutional_gate(symbol): return None\n        trading_client.submit_order(")

with open(daemon_path, "w") as f:
    f.write(code)

print("✅ Patch applied successfully to velez_stage4_short_daemon.py")
