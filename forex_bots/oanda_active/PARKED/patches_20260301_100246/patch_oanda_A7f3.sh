#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA7f3_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
lines = p.read_text(encoding="utf-8").splitlines(True)

# Ensure import re exists (for runtime parsing in injected code)
txt = "".join(lines)
if not re.search(r"^\s*import\s+re\s*$", txt, flags=re.M):
    # insert near top after first import
    for i, ln in enumerate(lines):
        if ln.startswith("import ") or ln.startswith("from "):
            lines.insert(i+1, "import re\n")
            break

# 1) Find a line containing literal 'HEARTBEAT' in source (likely in a helper)
hb_idx = None
for i, ln in enumerate(lines):
    if "HEARTBEAT" in ln:
        hb_idx = i
        break
if hb_idx is None:
    raise SystemExit("❌ A7f3: No literal 'HEARTBEAT' found in oanda_practice_bot.py. (It may be constructed dynamically.)")

# 2) Find the nearest logging call after that (within next 60 lines)
log_start = None
for j in range(hb_idx, min(len(lines), hb_idx + 60)):
    if "logging." in lines[j] and "(" in lines[j]:
        log_start = j
        break
if log_start is None:
    raise SystemExit("❌ A7f3: Found 'HEARTBEAT' but couldn't find a logging.* call nearby to inject after.")

# 3) Find end of that logging call by balancing parentheses
bal = 0
log_end = log_start
for k in range(log_start, min(len(lines), log_start + 80)):
    ln = lines[k]
    bal += ln.count("(") - ln.count(")")
    log_end = k
    if bal <= 0 and k > log_start:
        break

indent = lines[log_start][:len(lines[log_start]) - len(lines[log_start].lstrip())]

# Avoid double-inject
near = "".join(lines[max(0, log_start-5): min(len(lines), log_end+20)])
if "NO_ENTRY |" in near:
    raise SystemExit("❌ A7f3: NO_ENTRY already injected near this heartbeat/logging block.")

# 4) Inject: capture last formatted message if a variable exists; otherwise parse the line content by reusing a best-effort `hb_msg`
# We try to sniff a variable name used in the logging call like logging.info(hb_msg) or logging.info(msg)
block = "".join(lines[log_start:log_end+1])
m = re.search(r"logging\.[a-zA-Z_]+\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)", block)
hb_var = m.group(1) if m else None

# We'll set hb_msg safely:
# - if hb_var exists: hb_msg = str(hb_var)
# - else: hb_msg = ""  (still logs missing_fields)
inject = (
f"{indent}# --- Patch A7f3: explain why no entry (debug, parse heartbeat text) ---\n"
f"{indent}try:\n"
f"{indent}    hb_msg = str({hb_var}) if '{hb_var}' != 'None' else ''\n"
f"{indent}except Exception:\n"
f"{indent}    hb_msg = ''\n"
f"{indent}try:\n"
f"{indent}    def _get(key, cast=float, default=None):\n"
f"{indent}        mm = re.search(rf\"{key}=([^ ]+)\", hb_msg)\n"
f"{indent}        if not mm:\n"
f"{indent}            return default\n"
f"{indent}        v = mm.group(1)\n"
f"{indent}        if cast is bool:\n"
f"{indent}            return v.lower() == 'true'\n"
f"{indent}        return cast(v)\n"
f"{indent}    _price = _get('price', float, None)\n"
f"{indent}    _donH  = _get('donH', float, None)\n"
f"{indent}    _donL  = _get('donL', float, None)\n"
f"{indent}    _buf   = _get('buf', float, 0.0)\n"
f"{indent}    _cool  = _get('cooldown', bool, False)\n"
f"{indent}    _long  = _get('long', bool, False)\n"
f"{indent}    _short = _get('short', bool, False)\n"
f"{indent}    reasons = []\n"
f"{indent}    if _cool:\n"
f"{indent}        reasons.append('cooldown')\n"
f"{indent}    if _price is not None and _donH is not None and _donL is not None:\n"
f"{indent}        bl = _price >= (_donH + _buf)\n"
f"{indent}        bs = _price <= (_donL - _buf)\n"
f"{indent}        if (not bl) and (not bs):\n"
f"{indent}            reasons.append('no_breakout')\n"
f"{indent}        if bl and (not _long):\n"
f"{indent}            reasons.append('breakout_long_but_longFalse')\n"
f"{indent}        if bs and (not _short):\n"
f"{indent}            reasons.append('breakout_short_but_shortFalse')\n"
f"{indent}    else:\n"
f"{indent}        reasons.append('missing_fields')\n"
f"{indent}    if (not _long) and (not _short):\n"
f"{indent}        logging.info(f\"NO_ENTRY | reasons={','.join(reasons) if reasons else 'none'} | hb={hb_msg}\")\n"
f"{indent}except Exception as e:\n"
f"{indent}    logging.warning(f\"NO_ENTRY debug failed: {e}\")\n"
)

lines.insert(log_end+1, inject)

p.write_text("".join(lines), encoding="utf-8")
print(f"✅ A7f3 injected after logging block lines {log_start+1}-{log_end+1} (HEARTBEAT located at line {hb_idx+1})")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
