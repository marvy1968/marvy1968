#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA5_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
s = p.read_text(encoding="utf-8")

# 1) Change spread-check exception from "allowing order" to "blocking order" + return
s = re.sub(
    r"logging\.warning\(f\"GATE \| spread check failed \(allowing order\): \{e\}\"\)",
    "logging.warning(f\"GATE | spread check failed (blocking order): {e}\")\n            return",
    s
)

# 2) Insert ORDER_SUBMIT log right after spread check passes (before 'data = {')
# Anchor on the spread check block ending 'except Exception as e:' section, then blank lines, then 'data = {'
anchor = r"(if spread_pips >.*?\n\s*logging\.info\(f\"GATE \| spread too high.*?\n\s*return\n\s*except Exception as e:\n\s*logging\.warning\(f\"GATE \| spread check failed.*?\n\s*return\n\s*)(\n\s*\n\s*\n\s*data\s*=\s*\{)"
m = re.search(anchor, s, flags=re.S)
if not m:
    # fallback: insert just before "data = {" inside place_market_order_with_sl
    fallback = r"(\n\s*)data\s*=\s*\{"
    if not re.search(fallback, s):
        raise SystemExit("❌ Could not find insertion point for ORDER_SUBMIT")
    s = re.sub(
        fallback,
        "\n        logging.info(f\"ORDER_SUBMIT | units={units} bid={bid:.5f} ask={ask:.5f} spread_pips={spread_pips:.2f} SL={sl_price:.5f} TP={tp_price:.5f}\")\n\\1data = {",
        s,
        count=1
    )
else:
    insert = m.group(1) + "        logging.info(f\"ORDER_SUBMIT | units={units} bid={bid:.5f} ask={ask:.5f} spread_pips={spread_pips:.2f} SL={sl_price:.5f} TP={tp_price:.5f}\")\n" + m.group(2)
    s = s[:m.start()] + insert + s[m.end():]

p.write_text(s, encoding="utf-8")
print("✅ Patch A5 applied: ORDER_SUBMIT log + spread-check failure blocks order")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
