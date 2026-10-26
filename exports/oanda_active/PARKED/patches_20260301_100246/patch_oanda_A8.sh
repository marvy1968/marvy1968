#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA8_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
s = p.read_text(encoding="utf-8")

# Ensure traceback import exists
if not re.search(r"^\s*import\s+traceback\s*$", s, flags=re.M):
    # insert after first import/from line
    s = re.sub(r"^((?:import|from)[^\n]*\n)", r"\1import traceback\n", s, count=1, flags=re.M)

# 1) Replace EVERY "time.sleep(self.cfg.loop_sleep)" with a log line + sleep
#    (keeps behavior same, but tells us WHY we are sleeping)
def repl_sleep(m):
    indent = m.group("indent")
    return (
        f"{indent}logging.info(f\"SLEEP | seconds={{{{self.cfg.loop_sleep}}}} | reason=loop_wait\")\n"
        f"{indent}time.sleep(self.cfg.loop_sleep)"
    )

s2 = re.sub(
    r"^(?P<indent>\s*)time\.sleep\(self\.cfg\.loop_sleep\)\s*$",
    repl_sleep,
    s,
    flags=re.M
)

# 2) Also tag the one hard sleep(10) at line ~575
s2 = re.sub(
    r"^(?P<indent>\s*)time\.sleep\(10\)\s*$",
    r"\g<indent>logging.info('SLEEP | seconds=10 | reason=fixed_wait')\n\g<indent>time.sleep(10)",
    s2,
    flags=re.M
)

# 3) Upgrade any "except Exception:" blocks in the main loop area to print traceback
#    We'll do a safe/targeted improvement: wherever there's "except Exception as e:"
#    followed by a sleep(loop_sleep), ensure we log traceback.
s2 = re.sub(
    r"(except Exception as e:\n)(\s*)(?!logging\.exception)([^\n]*\n)*?(\s*)logging\.(warning|info)\(",
    r"\1\2logging.exception('LOOP_EXCEPTION')\n\4logging.\5(",
    s2,
    flags=re.M
)

p.write_text(s2, encoding="utf-8")
print("✅ Patch A8 applied: sleep reasons + traceback logging")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
