#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA10_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
s = p.read_text(encoding="utf-8")

# ensure imports exist
if "import traceback" not in s:
    s = re.sub(r"^((?:import|from)[^\n]*\n)", r"\1import traceback\n", s, count=1, flags=re.M)

# pricing import might already exist; we won't enforce it—just use whatever your bot already uses.
# We'll inject code *after* long_trigger/short_trigger calc OR just before HEARTBEAT logging.info(

# Anchor: find the line "logging.info(" that starts HEARTBEAT block
pat = r"(\n\s*logging\.info\(\n\s*\"HEARTBEAT \|)"
m = re.search(pat, s)
if not m:
    raise SystemExit("❌ A10: couldn't find HEARTBEAT logging.info block to patch.")

# Find indentation from the logging.info line
hb_line_start = s.rfind("\n", 0, m.start()) + 1
indent = re.match(r"\s*", s[hb_line_start:]).group(0)

inject = (
f"\n{indent}# --- Patch A10: live pricing debug (bid/ask/time) ---\n"
f"{indent}try:\n"
f"{indent}    # Pull a live tick from OANDA pricing (independent of candles)\n"
f"{indent}    r = pricing.PricingInfo(self.account_id, params={{'instruments': self.cfg.instrument}})\n"
f"{indent}    pd = self.client.request(r)\n"
f"{indent}    pr = (pd.get('prices') or [None])[0]\n"
f"{indent}    if pr:\n"
f"{indent}        _bid = float(pr['bids'][0]['price'])\n"
f"{indent}        _ask = float(pr['asks'][0]['price'])\n"
f"{indent}        _mid = (_bid + _ask) / 2.0\n"
f"{indent}        logging.info(\"LIVE_PRICE | bid=%.5f ask=%.5f mid=%.5f time=%s\" % (_bid, _ask, _mid, pr.get('time','na')))\n"
f"{indent}    else:\n"
f"{indent}        logging.warning(\"LIVE_PRICE | no prices returned\")\n"
f"{indent}except Exception as e:\n"
f"{indent}    logging.warning(f\"LIVE_PRICE failed: {e}\")\n"
)

# Insert injection right before HEARTBEAT logging block
s = s[:hb_line_start] + s[hb_line_start:hb_line_start]  # no-op, keeps indices simple
s = s.replace(m.group(1), inject + m.group(1), 1)

p.write_text(s, encoding="utf-8")
print("✅ A10 injected LIVE_PRICE log before HEARTBEAT")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
