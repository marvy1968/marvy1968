#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA3_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
s = p.read_text(encoding="utf-8")

# Insert tp_price computation + ENTRY_ATTEMPT log right after sl_price line
pattern = r"(def place_market_order_with_sl\(self, units: int, ref_price: float, sl_dist_price: float\):\n(?:.*\n)*?\s*sl_price\s*=\s*\(ref_price\s*-\s*sl_dist_price\)\s*if\s*units\s*>\s*0\s*else\s*\(ref_price\s*\+\s*sl_dist_price\)\n)"
m = re.search(pattern, s)
if not m:
    raise SystemExit("❌ Could not find place_market_order_with_sl() / sl_price line to patch")

inject = (
    "        # --- Patch A3: define TP based on SL distance + config multipliers ---\n"
    "        try:\n"
    "            base_dist = sl_dist_price / max(getattr(self.cfg, 'sl_atr_mult', 1.0), 1e-9)\n"
    "            tp_dist_price = base_dist * float(getattr(self.cfg, 'tp_atr_mult', 1.3))\n"
    "        except Exception:\n"
    "            tp_dist_price = sl_dist_price * 1.3\n"
    "        tp_price = (ref_price + tp_dist_price) if units > 0 else (ref_price - tp_dist_price)\n"
    "        logging.info(f\"ENTRY_ATTEMPT | units={units} max_units={self.cfg.max_units} ref={ref_price:.5f} SL={sl_price:.5f} TP={tp_price:.5f}\")\n\n"
)

s2 = s[:m.end(1)] + inject + s[m.end(1):]

# Safety: if TP line exists but tp_price wasn't defined, keep it; we now define tp_price.
p.write_text(s2, encoding="utf-8")
print("✅ Patch A3 applied: tp_price defined + ENTRY_ATTEMPT logging")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
