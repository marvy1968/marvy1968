#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_patchA_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
s = p.read_text(encoding="utf-8")

# ---------- ensure dotenv ----------
if "load_dotenv(" not in s:
    s = s.replace(
        "import logging",
        "import logging\nfrom dotenv import load_dotenv\nload_dotenv(dotenv_path='.env')\n"
    )

# ---------- ensure config fields ----------
def ensure_field(name: str, line: str):
    global s
    if re.search(rf"^\s*{re.escape(name)}\s*:", s, flags=re.M):
        return
    # insert after DAILY_LOSS_PCT if exists, else after MAX_DD_PCT, else after loop_sleep
    for anchor in ["daily_loss_pct", "max_dd_pct", "loop_sleep"]:
        m = re.search(rf"^\s*{anchor}\s*:\s*.*$", s, flags=re.M)
        if m:
            s = s[:m.end()] + "\n" + line + s[m.end():]
            return
    s += "\n" + line + "\n"

ensure_field("max_units",       "    max_units: int = int(os.getenv('MAX_UNITS','75000'))")
ensure_field("max_open_trades", "    max_open_trades: int = int(os.getenv('MAX_OPEN_TRADES','1'))")
ensure_field("max_spread_pips", "    max_spread_pips: float = float(os.getenv('MAX_SPREAD_PIPS','1.2'))")
ensure_field("sl_atr_mult",     "    sl_atr_mult: float = float(os.getenv('SL_ATR_MULT','1.8'))")
ensure_field("tp_atr_mult",     "    tp_atr_mult: float = float(os.getenv('TP_ATR_MULT','2.4'))")

# ---------- helpers injected after __init__ ----------
if "_pip_size" not in s:
    m = re.search(r"(def __init__\(.*?\):\n(?:.*\n){1,80})", s)
    if not m:
        raise SystemExit("❌ couldn't locate __init__ block to anchor helpers")
    insert_point = m.end()

    helpers = """
    def _pip_size(self) -> float:
        return 0.01 if "JPY" in self.cfg.instrument else 0.0001

    def _open_trades(self):
        r = trades.OpenTrades(self.account_id)
        return self.client.request(r).get("trades", [])

    def _open_count(self) -> int:
        return len(self._open_trades())

    def _spread_pips(self) -> float:
        # requires get_market_metrics() to include bid/ask or spread; fallback safe 0
        try:
            m = self.get_market_metrics()
            bid = float(m.get("bid", 0.0))
            ask = float(m.get("ask", 0.0))
            if bid > 0 and ask > 0:
                return (ask - bid) / self._pip_size()
        except Exception:
            pass
        return 0.0

    def _clamp_units(self, units: int) -> int:
        u = int(abs(units))
        return max(1, min(self.cfg.max_units, u))

    def _sl_tp_prices(self, side: str, price: float, atr: float):
        pip = self._pip_size()
        # fallback atr in price units; if atr is missing, use a minimum (2 pips)
        atr = max(float(atr or 0.0), 2.0 * pip)
        sl_dist = self.cfg.sl_atr_mult * atr
        tp_dist = self.cfg.tp_atr_mult * atr

        if side == "buy":
            sl = price - sl_dist
            tp = price + tp_dist
        else:
            sl = price + sl_dist
            tp = price - tp_dist

        # format to 5dp for non-JPY, 3dp for JPY
        dp = 3 if "JPY" in self.cfg.instrument else 5
        return f"{sl:.{dp}f}", f"{tp:.{dp}f}"

"""
    s = s[:insert_point] + helpers + s[insert_point:]

# ---------- patch order create to include SL/TP (best-effort anchors) ----------
# We add a small helper order submit and rewrite simple MARKET submits to call it
if "def _submit_market(" not in s:
    # inject submit helper near helpers
    anchor = "def _clamp_units"
    m = re.search(rf"({re.escape(anchor)}.*?\n(?:.*\n){{1,30}})", s)
    if m:
        insert_at = m.end()
        submit = """
    def _submit_market(self, units: int, side: str, price: float, atr: float):
        units = self._clamp_units(units)
        if self._open_count() >= self.cfg.max_open_trades:
            logging.info(f"OPEN_TRADE_GUARD | max_open_trades reached ({self.cfg.max_open_trades})")
            return None

        sp = self._spread_pips()
        if sp and sp > self.cfg.max_spread_pips:
            logging.info(f"SPREAD_GUARD | spread_pips={sp:.2f} > max_spread_pips={self.cfg.max_spread_pips}")
            return None

        sl, tp = self._sl_tp_prices(side, price, atr)

        order = {
            "order": {
                "type": "MARKET",
                "instrument": self.cfg.instrument,
                "units": str(units if side == "buy" else -units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {"price": sl},
                "takeProfitOnFill": {"price": tp},
            }
        }
        logging.info(f"ENTRY | side={side} units={units} sl={sl} tp={tp}")
        return self.client.request(orders.OrderCreate(self.account_id, data=order))

"""
        s = s[:insert_at] + submit + s[insert_at:]

# Rewrite obvious callsites:
# - any "ULTRA BUY/SELL" or "BUY/SELL" blocks that call OrderCreate directly
s = re.sub(
    r"self\.client\.request\(orders\.OrderCreate\(self\.account_id,\s*data=\{\s*\"order\":\s*\{\s*\"units\":\s*str\(([-]?)units\)\s*,\s*\"instrument\":\s*self\.cfg\.instrument\s*,\s*\"type\":\s*\"MARKET\"\s*\}\s*\}\)\)",
    r"self._submit_market(units, 'sell' if '\1'=='-' else 'buy', m.get('price',0.0), m.get('atr',0.0))",
    s
)

# More generic: replace manual create blocks in common patterns
s = s.replace(
    'self.client.request(orders.OrderCreate(self.account_id, data={"order": {"units": str(units), "instrument": self.cfg.instrument, "type": "MARKET"}}))',
    "self._submit_market(units, 'buy', m.get('price',0.0), m.get('atr',0.0))"
)
s = s.replace(
    'self.client.request(orders.OrderCreate(self.account_id, data={"order": {"units": str(-units), "instrument": self.cfg.instrument, "type": "MARKET"}}))',
    "self._submit_market(units, 'sell', m.get('price',0.0), m.get('atr',0.0))"
)

p.write_text(s, encoding="utf-8")
print("✅ Patch A applied to", p)
PY

python3 -m py_compile oanda_practice_bot.py
echo "✅ compile ok"
