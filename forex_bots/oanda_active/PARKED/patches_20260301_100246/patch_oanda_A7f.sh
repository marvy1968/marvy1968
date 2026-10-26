#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA7f_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
lines = p.read_text(encoding="utf-8").splitlines(True)

# Find the injected NO_ENTRY block from A7e and remove it (so we don't stack bad debug)
# Remove between markers if present
out = []
skip = False
for ln in lines:
    if "# --- Patch A7e: explain why no entry attempt (debug) ---" in ln:
        skip = True
        continue
    if skip:
        # end skip once we hit a blank line then a non-indented 'except' end, or next def/class, or "# --- Patch"
        if ln.startswith("def ") or ln.startswith("class ") or "# --- Patch" in ln:
            skip = False
            out.append(ln)
        # Otherwise keep skipping
        continue
    out.append(ln)
lines = out

# Find HEARTBEAT line again
idx = None
for i, ln in enumerate(lines):
    if "HEARTBEAT |" in ln:
        idx = i
        break
if idx is None:
    raise SystemExit("❌ Patch A7f: Could not find 'HEARTBEAT |' in source.")

# Find start/end of the logging call (same method as A7e)
start = idx
for j in range(idx, max(-1, idx-40), -1):
    if ("logging" in lines[j] or ".info" in lines[j]) and "(" in lines[j]:
        start = j
        break

bal = 0
end = start
for k in range(start, min(len(lines), start+140)):
    ln = lines[k]
    bal += ln.count("(") - ln.count(")")
    end = k
    if bal <= 0 and k > start:
        break

indent = lines[start][:len(lines[start]) - len(lines[start].lstrip())]

inject = [
    indent + "# --- Patch A7f: NO_ENTRY debug (parse HEARTBEAT string, no local vars required) ---\n",
    indent + "try:\n",
    indent + "    _hb = (\n",
    indent + "        f\"price={price:.5f} adx={adx:.1f} regime={regime} \"\n",
    indent + "        f\"donH={donH:.5f} donL={donL:.5f} buf={buf:.5f} \"\n",
    indent + "        f\"bias={bias} long={long} short={short} cooldown={cooldown}\"\n",
    indent + "    )\n",
    indent + "except Exception:\n",
    indent + "    _hb = None\n",
    indent + "try:\n",
    indent + "    # Fallback: extract values from the log string if locals aren't available\n",
    indent + "    if _hb is None:\n",
    indent + "        # If your heartbeat call already built a message variable, try to reuse it\n",
    indent + "        _hb = locals().get('msg') or locals().get('hb') or ''\n",
    indent + "    if not _hb:\n",
    indent + "        raise ValueError('no heartbeat string available')\n",
    indent + "    def _get(key, cast=float, default=None):\n",
    indent + "        m = re.search(rf\"{key}=([^ ]+)\", _hb)\n",
    indent + "        if not m:\n",
    indent + "            return default\n",
    indent + "        v = m.group(1)\n",
    indent + "        if cast is bool:\n",
    indent + "            return v.lower() == 'true'\n",
    indent + "        return cast(v)\n",
    indent + "    # pull fields\n",
    indent + "    _price = _get('price', float, None)\n",
    indent + "    _donH  = _get('donH', float, None)\n",
    indent + "    _donL  = _get('donL', float, None)\n",
    indent + "    _buf   = _get('buf', float, 0.0)\n",
    indent + "    _cool  = _get('cooldown', bool, False)\n",
    indent + "    _long  = _get('long', bool, False)\n",
    indent + "    _short = _get('short', bool, False)\n",
    indent + "    reasons = []\n",
    indent + "    if _cool:\n",
    indent + "        reasons.append('cooldown')\n",
    indent + "    if _price is not None and _donH is not None and _donL is not None:\n",
    indent + "        bl = _price >= (_donH + _buf)\n",
    indent + "        bs = _price <= (_donL - _buf)\n",
    indent + "        if (not bl) and (not bs):\n",
    indent + "            reasons.append('no_breakout')\n",
    indent + "        if bl and (not _long):\n",
    indent + "            reasons.append('breakout_long_but_longFalse')\n",
    indent + "        if bs and (not _short):\n",
    indent + "            reasons.append('breakout_short_but_shortFalse')\n",
    indent + "    else:\n",
    indent + "        reasons.append('missing_fields')\n",
    indent + "    if (not _long) and (not _short):\n",
    indent + "        logging.info(f\"NO_ENTRY | reasons={','.join(reasons) if reasons else 'none'} | {_hb}\")\n",
    indent + "except Exception as e:\n",
    indent + "    try:\n",
    indent + "        logging.warning(f\"NO_ENTRY debug failed: {e}\")\n",
    indent + "    except Exception:\n",
    indent + "        pass\n",
]

# Need 're' module in scope for _get()
# Insert 'import re' if not present
text = "".join(lines)
if "import re" not in text:
    # best-effort: add after other imports near top
    text = text.replace("import os", "import os\nimport re", 1)
    lines = text.splitlines(True)

insert_at = end + 1
lines[insert_at:insert_at] = inject

p.write_text("".join(lines), encoding="utf-8")
print(f"✅ Patch A7f inserted after HEARTBEAT call lines {start+1}-{end+1}")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
