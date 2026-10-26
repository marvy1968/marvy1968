#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA7_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
s = p.read_text(encoding="utf-8")

# Find the HEARTBEAT logging line and inject NO_ENTRY reasoning right after it.
# We look for a line that contains: logging.info(f"HEARTBEAT | ...
hb_pat = r"^(?P<indent>\s*)logging\.info\(f\"HEARTBEAT \| .*?\) *$"

m = re.search(hb_pat, s, flags=re.M)
if not m:
    raise SystemExit("❌ Patch A7: Could not find HEARTBEAT logging line to patch.")

indent = m.group("indent")

inject = (
f"{indent}# --- Patch A7: explain why no entry attempt (debug) ---\n"
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
f"{indent}    # open-trade gate (if your bot uses it)\n"
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

# Insert injection immediately AFTER the first HEARTBEAT log line
s2 = re.sub(hb_pat, lambda mm: mm.group(0) + "\n" + inject, s, count=1, flags=re.M)

if s2 == s:
    raise SystemExit("❌ Patch A7: substitution failed unexpectedly.")

p.write_text(s2, encoding="utf-8")
print("✅ Patch A7 applied: NO_ENTRY reason logging injected after HEARTBEAT")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
