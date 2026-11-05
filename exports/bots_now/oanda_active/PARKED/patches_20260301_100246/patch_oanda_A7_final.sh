#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA7final_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
s = p.read_text(encoding="utf-8")

# Ensure import re exists in runtime file
if not re.search(r"^\s*import\s+re\s*$", s, flags=re.M):
    s = re.sub(r"^(import[^\n]*\n)", r"\1import re\n", s, count=1, flags=re.M)

lines = s.splitlines(True)

# Find first occurrence of literal HEARTBEAT in source (should be where message is created/logged)
hb_idx = None
for i, ln in enumerate(lines):
    if "HEARTBEAT" in ln:
        hb_idx = i
        break
if hb_idx is None:
    raise SystemExit("❌ A7-final: No literal 'HEARTBEAT' found in source.")

# From hb_idx forward, find a logging.* call that contains HEARTBEAT
log_start = None
for j in range(hb_idx, min(len(lines), hb_idx + 200)):
    if ("logging." in lines[j]) and ("HEARTBEAT" in lines[j]) and ("(" in lines[j]):
        log_start = j
        break

# If not found on same line, find any logging call nearby and we'll inject after its full call block
if log_start is None:
    for j in range(hb_idx, min(len(lines), hb_idx + 200)):
        if "logging." in lines[j] and "(" in lines[j]:
            log_start = j
            break

if log_start is None:
    raise SystemExit("❌ A7-final: couldn't find logging.* call near HEARTBEAT section.")

# Find end of the logging call by balancing parentheses starting at log_start
bal = 0
log_end = None
for k in range(log_start, min(len(lines), log_start + 250)):
    ln = lines[k]
    bal += ln.count("(") - ln.count(")")
    if bal <= 0 and k > log_start:
        log_end = k
        break

# If still None, maybe single-line call
if log_end is None:
    log_end = log_start

indent = lines[log_start][:len(lines[log_start]) - len(lines[log_start].lstrip())]

# Prevent double inject
near = "".join(lines[max(0, log_start-10):min(len(lines), log_end+80)])
if "Patch A7-final" in near or "NO_ENTRY |" in near:
    raise SystemExit("❌ A7-final: looks like NO_ENTRY debug already injected nearby.")

# Try to detect if the logging call is logging.info(hb_msg) style
block = "".join(lines[log_start:log_end+1])
m = re.search(r"logging\.[a-zA-Z_]+\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)", block)
hb_var = m.group(1) if m else None

# NOTE: escape braces so {key} stays runtime code: use {{key}} in this generator f-string.
inject = (
f"{indent}# --- Patch A7-final: explain why no entry (debug via parsing HEARTBEAT message) ---\n"
f"{indent}try:\n"
f"{indent}    hb_msg = str({hb_var}) if {('True' if hb_var else 'False')} else ''\n"
f"{indent}except Exception:\n"
f"{indent}    hb_msg = ''\n"
f"{indent}try:\n"
f"{indent}    def _get(key, cast=float, default=None):\n"
f"{indent}        mm = re.search(rf\"{{key}}=([^ ]+)\", hb_msg)\n"
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
f"{indent}        logging.info(f\"NO_ENTRY | reasons={{','.join(reasons) if reasons else 'none'}}\")\n"
f"{indent}except Exception as e:\n"
f"{indent}    logging.warning(f\"NO_ENTRY debug failed: {{e}}\")\n"
)

lines.insert(log_end+1, inject)

p.write_text("".join(lines), encoding="utf-8")
print(f"✅ A7-final injected after logging block lines {log_start+1}-{log_end+1} (HEARTBEAT located at line {hb_idx+1})")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
