#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA7b_${TS}"

python3 - <<'PY'
from pathlib import Path

p = Path("oanda_practice_bot.py")
lines = p.read_text(encoding="utf-8").splitlines(True)

# Find a line that actually logs HEARTBEAT
idx = None
for i, ln in enumerate(lines):
    if "HEARTBEAT |" in ln and "logging." in ln:
        idx = i
        break

if idx is None:
    raise SystemExit("❌ Patch A7b: couldn't find any logging line containing 'HEARTBEAT |'")

indent = lines[idx][:len(lines[idx]) - len(lines[idx].lstrip())]

inject = (
f"{indent}# --- Patch A7b: explain why no entry attempt (debug) ---\n"
f"{indent}try:\n"
f"{indent}    breakout_long = price >= (donH + buf)\n"
f"{indent}    breakout_short = price <= (donL - buf)\n"
f"{indent}    reasons = []\n"
f"{indent}    if cooldown:\n"
f"{indent}        reasons.append('cooldown')\n"
f"{indent}    if (not breakout_long) and (not breakout_short):\n"
f"{indent}        reasons.append('no_breakout')\n"
f"{indent}    if breakout_long and (not long):\n"
f"{indent}        reasons.append('breakout_long_but_longFalse')\n"
f"{indent}    if breakout_short and (not short):\n"
f"{indent}        reasons.append('breakout_short_but_shortFalse')\n"
f"{indent}    try:\n"
f"{indent}        ot = self.open_trade()\n"
f"{indent}        if ot is not None and int(getattr(self.cfg, 'max_open_trades', 1)) <= 1:\n"
f"{indent}            reasons.append('already_open_trade')\n"
f"{indent}    except Exception:\n"
f"{indent}        pass\n"
f"{indent}    if (not long) and (not short):\n"
f"{indent}        logging.info(\n"
f"{indent}            f\"NO_ENTRY | price={price:.5f} donH={donH:.5f} donL={donL:.5f} buf={buf:.5f} \"\n"
f"{indent}            f\"breakL={breakout_long} breakS={breakout_short} adx={adx:.1f} regime={regime} \"\n"
f"{indent}            f\"bias={bias} cooldown={cooldown} reasons={','.join(reasons) if reasons else 'none'}\"\n"
f"{indent}        )\n"
f"{indent}except Exception as e:\n"
f"{indent}    logging.warning(f\"NO_ENTRY debug failed: {e}\")\n"
)

# Avoid double-injecting if you run patch twice
if any("NO_ENTRY |" in ln for ln in lines[max(0, idx-5):idx+25]):
    raise SystemExit("❌ Patch A7b: looks like NO_ENTRY injection already exists near HEARTBEAT (won't double-inject)")

lines.insert(idx + 1, inject)

p.write_text("".join(lines), encoding="utf-8")
print(f"✅ Patch A7b applied after line {idx+1}: {lines[idx].strip()}")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
