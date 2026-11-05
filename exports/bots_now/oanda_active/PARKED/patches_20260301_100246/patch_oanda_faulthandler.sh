#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_faulthandler_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
s = p.read_text(encoding="utf-8")

# If already installed, do nothing
if "faulthandler.register(signal.SIGUSR1" in s:
    print("✅ faulthandler already present")
    raise SystemExit(0)

# Insert right after imports (best-effort: after first 'import logging' or after first import block)
inject = (
    "import faulthandler, signal\n"
    "faulthandler.enable()\n"
    "faulthandler.register(signal.SIGUSR1, all_threads=True)\n"
)

if "import logging" in s:
    s = s.replace("import logging\n", "import logging\n" + inject, 1)
else:
    # fallback: insert after first import line
    s = re.sub(r"^((?:import|from)[^\n]*\n)", r"\1" + inject, s, count=1, flags=re.M)

p.write_text(s, encoding="utf-8")
print("✅ faulthandler injected (SIGUSR1 stack dump enabled)")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
