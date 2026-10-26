#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA7d_${TS}"

python3 - <<'PY'
from pathlib import Path

p = Path("oanda_practice_bot.py")
lines = p.read_text(encoding="utf-8").splitlines(True)

# Find ANY line containing HEARTBEAT | (no assumptions about logging.*)
idx = None
for i, ln in enumerate(lines):
    if "HEARTBEAT |" in ln:
        idx = i
        break

if idx is None:
    raise SystemExit("❌ Patch A7d: Could not find 'HEARTBEAT |' in source file.")

# Prevent double-inject
near = "".join(lines[max(0, idx-10): idx+60])
if "NO_ENTRY |" in near:
    raise SystemExit("❌ Patch A7d: NO_ENTRY injection already appears near HEARTBEAT block.")

indent = lines[idx][:len(lines[idx]) - len(lines[idx].lstrip())]

inject_lines = [
    indent + "# --- Patch A7d: explain why no entry attempt (debug) ---\n",
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

lines[idx+1:idx+1] = inject_lines
p.write_text("".join(lines), encoding="utf-8")
print(f"✅ Patch A7d applied after source line {idx+1}")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
