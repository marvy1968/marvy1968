#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA4_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
s = p.read_text(encoding="utf-8")

# Ensure pricing endpoint import exists somewhere
if "endpoints.pricing" not in s and "import oandapyV20.endpoints.pricing as pricing" not in s:
    # try to insert near other oandapy imports
    s = re.sub(
        r"(import oandapyV20\.endpoints\.[a-zA-Z_]+ as [a-zA-Z_]+\n)",
        r"\1import oandapyV20.endpoints.pricing as pricing\n",
        s,
        count=1
    )
    if "import oandapyV20.endpoints.pricing as pricing" not in s:
        # fallback insert after 'from oandapyV20 import API' if present
        s = s.replace("from oandapyV20 import API\n", "from oandapyV20 import API\nimport oandapyV20.endpoints.pricing as pricing\n")

# Inject spread + max open trades gate into place_market_order_with_sl
# We insert AFTER the ENTRY_ATTEMPT log added by Patch A3.
needle = r'logging\.info\(f"ENTRY_ATTEMPT \| units=\{units\} max_units=\{self\.cfg\.max_units\} ref=\{ref_price:\.5f\} SL=\{sl_price:\.5f\} TP=\{tp_price:\.5f\}"\)\n'
m = re.search(needle, s)
if not m:
    raise SystemExit("❌ Patch A4: Could not find ENTRY_ATTEMPT log line (Patch A3 must be present)")

inject = (
    "        # --- Patch A4: enforce max_open_trades + max_spread_pips at submit time ---\n"
    "        try:\n"
    "            if getattr(self.cfg, 'max_open_trades', 1) <= 0:\n"
    "                logging.warning('GATE | max_open_trades<=0 (blocked)')\n"
    "                return\n"
    "            # if there is already an open trade, do not open another\n"
    "            if self.open_trade() is not None and int(getattr(self.cfg, 'max_open_trades', 1)) <= 1:\n"
    "                logging.info('GATE | max_open_trades reached (already open)')\n"
    "                return\n"
    "        except Exception:\n"
    "            pass\n\n"
    "        try:\n"
    "            pip_size = 0.01 if ('_JPY' in self.cfg.instrument) else 0.0001\n"
    "            r = pricing.PricingInfo(self.account_id, params={'instruments': self.cfg.instrument})\n"
    "            pd = self.client.request(r)\n"
    "            pr = pd.get('prices', [])[0]\n"
    "            bid = float(pr['bids'][0]['price'])\n"
    "            ask = float(pr['asks'][0]['price'])\n"
    "            spread_pips = abs(ask - bid) / pip_size\n"
    "            if spread_pips > float(getattr(self.cfg, 'max_spread_pips', 1.2)):\n"
    "                logging.info(f\"GATE | spread too high {spread_pips:.2f}p > {self.cfg.max_spread_pips:.2f}p (skip order)\")\n"
    "                return\n"
    "        except Exception as e:\n"
    "            logging.warning(f\"GATE | spread check failed (allowing order): {e}\")\n\n"
)

s2 = s[:m.end()] + inject + s[m.end():]
p.write_text(s2, encoding="utf-8")
print("✅ Patch A4 applied: spread gate + max_open_trades gate in order submit")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
