#!/usr/bin/env bash
set -euo pipefail

FILE="ultra_fifo_bot.py"
ENVFILE=".env"

ts="$(date +%F_%H%M%S)"
cp -a "$FILE" "$FILE.bak.$ts"

python - <<'PY'
import re, pathlib

p = pathlib.Path("ultra_fifo_bot.py")
s = p.read_text()

def ensure_cfg_field(name, default, comment=""):
    global s
    if re.search(rf'^\s*{re.escape(name)}\s*:', s, flags=re.M):
        return
    m = re.search(r'^\s*max_stale_secs\s*:\s*.*$', s, flags=re.M) or re.search(r'^\s*tf\s*:\s*.*$', s, flags=re.M)
    if not m:
        raise SystemExit("Could not find insertion anchor in BotConfig (tf or max_stale_secs).")
    line = m.group(0)
    insert = f'\n    {name}: {default}'
    if comment:
        insert += f'  # {comment}'
    s = s.replace(line, line + insert)

# sensible defaults for M15
ensure_cfg_field("max_stale_secs", "int = 2400", "M15: allow up to 40 min age")
ensure_cfg_field("max_consecutive_stale", "int = 3", "pause after N stale cycles")
ensure_cfg_field("oanda_pause_secs", "int = 60", "pause duration on lag/maintenance")
ensure_cfg_field("pricing_stale_secs", "float = 10.0", "pricing tick staleness guard")
ensure_cfg_field("pricing_heartbeat", "bool = True", "log pricing heartbeat")

# add _pause_for_lag if missing
if "def _pause_for_lag(self, reason:" not in s:
    m = re.search(r'^\s*def get_market_data\(self\)\s*->\s*Dict\[str,\s*float\]\s*:\s*$', s, flags=re.M)
    if not m:
        raise SystemExit("Could not find get_market_data() to insert _pause_for_lag() above it.")
    insert = """
    def _pause_for_lag(self, reason: str):
        import time
        pause = int(getattr(self.cfg, "oanda_pause_secs", 60))
        logging.warning("⏸️ PAUSE_FOR_LAG: %s | sleep=%ss", reason, pause)
        time.sleep(max(1, pause))
"""
    s = s[:m.start()] + insert + s[m.start():]

# env overrides (inject once)
if "freshness/pause overrides" not in s:
    s = re.sub(
        r'(\n\s*cfg\s*=\s*BotConfig\(\)\s*\n)',
        r"""\1    # --- freshness/pause overrides (.env optional) ---
    cfg.max_stale_secs = int(os.getenv("MAX_STALE_SECS", str(cfg.max_stale_secs)))
    cfg.pricing_stale_secs = float(os.getenv("PRICING_STALE_SECS", str(cfg.pricing_stale_secs)))
    cfg.max_consecutive_stale = int(os.getenv("MAX_CONSEC_STALE", str(cfg.max_consecutive_stale)))
    cfg.oanda_pause_secs = int(os.getenv("OANDA_PAUSE_SECS", str(cfg.oanda_pause_secs)))
    cfg.pricing_heartbeat = os.getenv("PRICING_HEARTBEAT", "1").strip().lower() not in ("0","false","no")
""",
        s,
        flags=re.M
    )

# ensure candle rows include time "t" for stale guard
s = re.sub(
    r'rows\s*=\s*\[\s*\{\s*"h":\s*float\(x\["mid"\]\["h"\]\)\s*,\s*"l":\s*float\(x\["mid"\]\["l"\]\)\s*,\s*"c":\s*float\(x\["mid"\]\["c"\]\)\s*\}\s*\n\s*for x in c if x\.get\("complete"\)\s*\]',
    'rows = [{"t": x["time"], "h": float(x["mid"]["h"]), "l": float(x["mid"]["l"]), "c": float(x["mid"]["c"])}\n        for x in c if x.get("complete")]',
    s,
    flags=re.S
)

# stale guard: reset hits when fresh + pause after N stale cycles
if "STALE_CANDLE:" in s:
    if "consecutive stale candles" not in s:
        s = re.sub(
            r'(logging\.warning\("STALE_CANDLE: age=.*?\n\s*return\s*\{\s*"stale"\s*:\s*True\s*\}\s*\n)',
            r"""\1                self._stale_hits = getattr(self, "_stale_hits", 0) + 1
                lim = int(getattr(self.cfg, "max_consecutive_stale", 3))
                if self._stale_hits >= lim:
                    self._pause_for_lag(f"consecutive stale candles >= {lim}")
""",
            s,
            flags=re.S
        )
    if "fresh candle -> reset stale hit counter" not in s:
        s = re.sub(
            r'(if age > max_age:\n\s*logging\.warning\("STALE_CANDLE:.*?\n\s*return\s*\{\s*"stale"\s*:\s*True\s*\}\s*\n)',
            r"""\1
              # fresh candle -> reset stale hit counter
              self._stale_hits = 0
""",
            s,
            flags=re.S
        )

p.write_text(s)
print("Patched:", p)
PY

# env defaults
touch "$ENVFILE"
set_kv () { local k="$1" v="$2"; if grep -qE "^${k}=" "$ENVFILE"; then sed -i "s|^${k}=.*|${k}=${v}|" "$ENVFILE"; else echo "${k}=${v}" >> "$ENVFILE"; fi; }

set_kv "MAX_STALE_SECS" "2400"
set_kv "MAX_CONSEC_STALE" "3"
set_kv "OANDA_PAUSE_SECS" "60"
set_kv "PRICING_STALE_SECS" "10"
set_kv "PRICING_HEARTBEAT" "1"

python -m py_compile "$FILE" && echo "✅ compile ok"
echo "✅ ALL patches applied."
