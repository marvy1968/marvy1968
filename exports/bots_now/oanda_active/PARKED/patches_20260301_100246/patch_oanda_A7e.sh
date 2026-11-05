#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA7e_${TS}"

python3 - <<'PY'
from pathlib import Path

p = Path("oanda_practice_bot.py")
lines = p.read_text(encoding="utf-8").splitlines(True)

# Find a line containing HEARTBEAT |
idx = None
for i, ln in enumerate(lines):
    if "HEARTBEAT |" in ln:
        idx = i
        break
if idx is None:
    raise SystemExit("❌ Patch A7e: Could not find 'HEARTBEAT |' in source.")

# Prevent double-inject near this section
near = "".join(lines[max(0, idx-20): idx+160])
if "NO_ENTRY |" in near:
    raise SystemExit("❌ Patch A7e: NO_ENTRY already injected near HEARTBEAT.")

# Find likely start of the logging call (walk backward a bit)
start = idx
for j in range(idx, max(-1, idx-30), -1):
    if ("logging" in lines[j] or ".info" in lines[j]) and "(" in lines[j]:
        start = j
        break

# Walk forward counting parentheses to find the end of the call
bal = 0
end = start
for k in range(start, min(len(lines), start+120)):
    ln = lines[k]
    bal += ln.count("(") - ln.count(")")
    end = k
    if bal <= 0 and k > start:
        break

indent = lines[start][:len(lines[start]) - len(lines[start].lstrip())]

inject = [
    indent + "# --- Patch A7e: explain why no entry attempt (debug) ---\n",
    indent + "try:\n",
    indent + "    breakout_long = price >= (donH + buf)\n",
    indent + "    breakout_short = price <= (donL - buf)\n",
    indent + "    reasons = []\n",
    indent + "    if cooldown:\n",
    indent + "        reasons.append('cooldown')\n",
    indent + "    if (not breakout_long) and (not breakout_short):\n",
    indent + "        reasons.append('no_breakout')\n",
    indent + "    if breakout_long and (not long):\n",
    indent + "        reasons.append('breakout_long_but_longFalse')\n",
    indent + "    if breakout_short and (not short):\n",
    indent + "        reasons.append('breakout_short_but_shortFalse')\n",
    indent + "    try:\n",
    indent + "        ot = self.open_trade()\n",
    indent + "        if ot is not None and int(getattr(self.cfg, 'max_open_trades', 1)) <= 1:\n",
    indent + "            reasons.append('already_open_trade')\n",
    indent + "    except Exception:\n",
    indent + "        pass\n",
    indent + "    if (not long) and (not short):\n",
    indent + "        logging.info(\n",
    indent + "            f\"NO_ENTRY | price={price:.5f} donH={donH:.5f} donL={donL:.5f} buf={buf:.5f} \"\n",
    indent + "            f\"breakL={breakout_long} breakS={breakout_short} adx={adx:.1f} regime={regime} \"\n",
    indent + "            f\"bias={bias} cooldown={cooldown} reasons={','.join(reasons) if reasons else 'none'}\"\n",
    indent + "        )\n",
    indent + "except Exception as e:\n",
    indent + "    try:\n",
    indent + "        logging.warning(f\"NO_ENTRY debug failed: {e}\")\n",
    indent + "    except Exception:\n",
    indent + "        pass\n",
]

# Insert AFTER the call ends
insert_at = end + 1
lines[insert_at:insert_at] = inject

p.write_text("".join(lines), encoding="utf-8")
print(f"✅ Patch A7e inserted after HEARTBEAT call lines {start+1}-{end+1}")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
