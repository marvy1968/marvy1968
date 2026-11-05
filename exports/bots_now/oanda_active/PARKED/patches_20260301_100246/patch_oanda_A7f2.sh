#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA7f2_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
s = p.read_text(encoding="utf-8")

# Ensure `import re` exists in the bot file (runtime uses it in injected code)
if not re.search(r"^\s*import\s+re\s*$", s, flags=re.M):
    # insert after first import line best-effort
    s = re.sub(r"^(import[^\n]*\n)", r"\1import re\n", s, count=1, flags=re.M)

lines = s.splitlines(True)

# Find the HEARTBEAT logging call (any logging.* containing 'HEARTBEAT |')
idx = None
for i, ln in enumerate(lines):
    if "HEARTBEAT |" in ln and ("logging." in ln or ".info" in ln):
        idx = i
        break
if idx is None:
    raise SystemExit("❌ A7f2: couldn't find HEARTBEAT logging line in source.")

# Find start of the logging call (walk backward until we see 'logging' and '(')
start = idx
for j in range(idx, max(-1, idx-40), -1):
    if ("logging" in lines[j] or ".info" in lines[j]) and "(" in lines[j]:
        start = j
        break

# Find end of the call by balancing parentheses
bal = 0
end = start
for k in range(start, min(len(lines), start+200)):
    ln = lines[k]
    bal += ln.count("(") - ln.count(")")
    end = k
    if bal <= 0 and k > start:
        break

block = "".join(lines[start:end+1])
indent = lines[start][:len(lines[start]) - len(lines[start].lstrip())]

# Replace the logging call with:
#   hb_msg = <same expression but without logging.info(...) wrapper>
#   logging.info(hb_msg)
#
# We do a conservative rewrite: turn `logging.info(<expr>)` -> `hb_msg = <expr>\nlogging.info(hb_msg)`
m = re.search(r"(logging\.[a-zA-Z_]+\s*\()\s*(.*)\)\s*$", block, flags=re.S)
if not m:
    raise SystemExit("❌ A7f2: couldn't parse HEARTBEAT logging call structure.")

expr = m.group(2).rstrip()
new_block = (
    f"{indent}hb_msg = {expr}\n"
    f"{indent}logging.info(hb_msg)\n"
)

# Build NO_ENTRY debug that parses hb_msg (no donH/donL locals needed)
inject = (
    f"{indent}# --- Patch A7f2: explain why no entry (parsing hb_msg) ---\n"
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
    f"{indent}        logging.info(f\"NO_ENTRY | reasons={','.join(reasons) if reasons else 'none'}\")\n"
    f"{indent}except Exception as e:\n"
    f"{indent}    logging.warning(f\"NO_ENTRY debug failed: {e}\")\n"
)

# Replace lines[start:end+1] with new_block + inject
lines[start:end+1] = [new_block, inject]

p.write_text("".join(lines), encoding="utf-8")
print(f"✅ Patch A7f2 applied at source lines {start+1}-{end+1}")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
