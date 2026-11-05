#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA2_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
s = p.read_text(encoding="utf-8")

# 1) Ensure max_units reads env (fix the hardcode)
# Replace ONLY the dataclass field line for max_units.
s2 = re.sub(
    r"^(\s*max_units\s*:\s*int\s*=\s*)(\d+)\s*$",
    r"\1int(os.getenv('MAX_UNITS','75000'))",
    s,
    flags=re.M
)

if s2 == s:
    # If pattern didn't match exactly, do a more forgiving replacement
    s2 = re.sub(
        r"^\s*max_units\s*:\s*int\s*=.*$",
        "    max_units: int = int(os.getenv('MAX_UNITS','75000'))",
        s,
        flags=re.M
    )

s = s2

# 2) Ensure OrderCreate has takeProfitOnFill if stopLossOnFill exists but TP missing in same block
# We'll look for a dict that contains stopLossOnFill and inject takeProfitOnFill if absent.
# This is conservative: only patches inside the order payload area.
if "stopLossOnFill" in s and "takeProfitOnFill" not in s:
    s = s.replace(
        '"stopLossOnFill": {"price": f"{sl_price:.5f}"},',
        '"stopLossOnFill": {"price": f"{sl_price:.5f}"},\n                "takeProfitOnFill": {"price": f"{tp_price:.5f}"},'
    )

p.write_text(s, encoding="utf-8")
print("✅ Patch A2 applied (MAX_UNITS env + TP safeguard)")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
