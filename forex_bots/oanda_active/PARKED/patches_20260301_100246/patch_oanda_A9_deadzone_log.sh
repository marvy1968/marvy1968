#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA9_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
s = p.read_text(encoding="utf-8")

# Insert a log line right before the dead-zone sleep (line with "SAFE HYBRID DEAD-ZONE")
pat = r"(\s*# --- SAFE HYBRID DEAD-ZONE.*\n\s*if\s+getattr\(self\.cfg,\s*\"adx_dead_low\".*?:\s*\n)(\s*)time\.sleep\(self\.cfg\.loop_sleep\)\s*\n\s*continue"
m = re.search(pat, s, flags=re.M)
if not m:
    raise SystemExit("❌ A9: could not find dead-zone gate block to patch.")

head = m.group(1)
ind = m.group(2)

inject = (
    f"{ind}logging.info(\n"
    f"{ind}    \"GATE | dead_zone_sleep adx=%.1f in [%.1f, %.1f) loop_sleep=%s\" % (\n"
    f"{ind}        adx,\n"
    f"{ind}        float(getattr(self.cfg,'adx_dead_low',24.0)),\n"
    f"{ind}        float(getattr(self.cfg,'adx_trend_strong', getattr(self.cfg,'adx_min', 25.0))),\n"
    f"{ind}        self.cfg.loop_sleep,\n"
    f"{ind}    )\n"
    f"{ind})\n"
)

replacement = head + inject + f"{ind}time.sleep(self.cfg.loop_sleep)\n{ind}continue"
s2 = re.sub(pat, replacement, s, flags=re.M)

p.write_text(s2, encoding="utf-8")
print("✅ A9 applied: dead-zone gate now logs why it sleeps")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
