#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA7c_${TS}"

python3 - <<'PY'
from pathlib import Path

p = Path("oanda_practice_bot.py")
lines = p.read_text(encoding="utf-8").splitlines(True)

# Find a likely HEARTBEAT log line by the unique substrings we see in runtime logs
idx = None
for i, ln in enumerate(lines):
    if ("bias=" in ln and "cooldown=" in ln and ("long=" in ln or "short=" in ln) and "logging." in ln) or \
       ("bias=" in ln and "cooldown=" in ln and ("long=" in ln or "short=" in ln) and ".info" in ln):
        idx = i
        break

if idx is None:
    # fallback: find any log/info line mentioning bias= (even if split weird)
    for i, ln in enumerate(lines):
        if "bias=" in ln and (".info" in ln or "logging." in ln):
            idx = i
            break

if idx is None:
    raise SystemExit("❌ Patch A7c: couldn't find a heartbeat-like log line (looked for bias=/cooldown=/long=/short=).")

indent = lines[idx][:len(lines[idx]) - len(lines[idx].lstrip())]

# Avoid double-inject
window = "".join(lines[max(0, idx-10): idx+40])
if "NO_ENTRY |" in window:
    raise SystemExit("❌ Patch A7c: NO_ENTRY injection already exists near the heartbeat block.")

inject = (
f"{indent}# --- Patch A7c: explain why no entry attempt (debug) ---\n"
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
f"{indent}    try:\n"
f"{indent}        logging.warning(f\"NO_ENTRY debug failed: {e}\")\n"
f"{indent}    except Exception:\n"
f"{indent}        pass\n"
)

lines.insert(idx + 1, inject)
p.write_text("".join(lines), encoding="utf-8")
print(f"✅ Patch A7c applied after line {idx+1}: {lines[idx].strip()}")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
