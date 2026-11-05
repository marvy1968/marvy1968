#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_A12_patch_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
s = p.read_text(encoding="utf-8")

# ---------- imports ----------
need_imports = [
    ("import math", r"^\s*import\s+math\s*$", "import math\n"),
    ("import traceback", r"^\s*import\s+traceback\s*$", "import traceback\n"),
]
for label, pat, line in need_imports:
    if not re.search(pat, s, flags=re.M):
        s = re.sub(r"^((?:import|from)[^\n]*\n)", r"\1"+line, s, count=1, flags=re.M)

# ---------- BotConfig fields ----------
# anchor after an existing config line that should exist
anchor = re.search(r"(?m)^\s*adx_dead_low:\s*float\s*=\s*[0-9.]+.*$", s)
if not anchor:
    raise SystemExit("❌ A12: couldn't find BotConfig adx_dead_low to anchor config injection.")

cfg_block = """
    # ===== EXPERT ENGINE V2 (ROI↑, DD↓) =====
    expert_allow: bool = True

    # Minervini trend filter (only trade in direction of higher-timeframe alignment)
    minervini_allow: bool = True
    minervini_ema_fast: int = 50
    minervini_ema_slow: int = 200
    minervini_require_trend_for_trend_entries: bool = True  # block trend entries if bias != trend

    # Volatility squeeze filter (avoid chop; prefer squeeze->expansion)
    squeeze_allow: bool = True
    squeeze_bb_n: int = 20
    squeeze_bb_k: float = 2.0
    squeeze_kc_n: int = 20
    squeeze_kc_k: float = 1.5
    squeeze_require_for_trend_entries: bool = False  # start conservative later if needed

    # Confirm filter (avoid false breakouts)
    confirm_allow: bool = True
    confirm_min_break_pips: float = 0.8   # require break distance beyond trigger
    confirm_min_atr_mult: float = 0.10    # OR break distance >= 0.10 * ATR

    # Risk brain (adaptive risk throttling)
    risk_brain_allow: bool = True
    risk_base_pct: float = 0.50           # base risk in %
    risk_min_pct: float = 0.25
    risk_max_pct: float = 1.00
    risk_loss_streak_step: float = 0.10   # reduce risk per consecutive loss
    risk_spread_penalty: float = 0.15     # reduce risk when spread elevated
    risk_adx_penalty_dead: float = 0.15   # reduce risk in “meh” ADX zones
"""

# inject only once
if "EXPERT ENGINE V2" not in s:
    insert_at = anchor.end()
    s = s[:insert_at] + "\n" + cfg_block + s[insert_at:]

# ---------- helper methods (inject into Bot class) ----------
# find class Bot: (or similar). We'll inject after the first "class " that contains "(object)" or not.
mclass = re.search(r"(?m)^class\s+\w+\s*:\s*$", s)
if not mclass:
    raise SystemExit("❌ A12: couldn't find a class definition to inject methods into.")

# Best effort: inject methods before the NEXT class after the first, or before __main__.
start = mclass.start()
rest = s[start:]
mnext = re.search(r"(?m)^\s*class\s+\w+\s*:\s*$", rest[1:])
end = start + (mnext.start()+1 if mnext else len(s))
cls = s[start:end]

if "def _expert_bias_v2" not in cls:
    # inject near the end of class (before last line)
    methods = r"""
    # ===========================
    # EXPERT ENGINE V2 HELPERS
    # ===========================
    def _ema(self, xs, n):
        if xs is None or len(xs) < n or n <= 1:
            return None
        k = 2.0 / (n + 1.0)
        ema = xs[0]
        for v in xs[1:]:
            ema = (v * k) + (ema * (1.0 - k))
        return float(ema)

    def _sma(self, xs, n):
        if xs is None or len(xs) < n or n <= 1:
            return None
        return float(sum(xs[-n:]) / n)

    def _std(self, xs, n):
        if xs is None or len(xs) < n or n <= 1:
            return None
        m = self._sma(xs, n)
        var = sum((x - m) ** 2 for x in xs[-n:]) / n
        return float(math.sqrt(var))

    def _expert_bias_v2(self, closes):
        # returns: "bull", "bear", or "none"
        if not closes or len(closes) < max(getattr(self.cfg,'minervini_ema_fast',50), getattr(self.cfg,'minervini_ema_slow',200)) + 5:
            return "none"
        ef = self._ema(closes[-(getattr(self.cfg,'minervini_ema_fast',50)+5):], getattr(self.cfg,'minervini_ema_fast',50))
        es = self._ema(closes[-(getattr(self.cfg,'minervini_ema_slow',200)+5):], getattr(self.cfg,'minervini_ema_slow',200))
        if ef is None or es is None:
            return "none"
        if ef > es:
            return "bull"
        if ef < es:
            return "bear"
        return "none"

    def _expert_squeeze_v2(self, closes):
        # True when BB width < KC width (squeeze on)
        n_bb = int(getattr(self.cfg,'squeeze_bb_n',20))
        k_bb = float(getattr(self.cfg,'squeeze_bb_k',2.0))
        n_kc = int(getattr(self.cfg,'squeeze_kc_n',20))
        k_kc = float(getattr(self.cfg,'squeeze_kc_k',1.5))
        if not closes or len(closes) < max(n_bb, n_kc) + 5:
            return None
        sma = self._sma(closes, n_bb)
        sd  = self._std(closes, n_bb)
        if sma is None or sd is None:
            return None
        bb_up = sma + k_bb * sd
        bb_dn = sma - k_bb * sd
        bb_w = bb_up - bb_dn
        # KC approx: SMA +/- k * ATRproxy; use avg abs returns as ATR proxy if real ATR not available
        rets = [abs(closes[i]-closes[i-1]) for i in range(1, len(closes))]
        atrp = self._sma(rets, n_kc) if rets else None
        if atrp is None:
            return None
        kc_w = (2.0 * k_kc * atrp)
        return bool(bb_w < kc_w)

    def _expert_confirm_v2(self, price, long_trigger, short_trigger, atr, pip_size):
        # returns tuple (ok_long, ok_short)
        min_pips = float(getattr(self.cfg,'confirm_min_break_pips',0.8))
        min_atr_mult = float(getattr(self.cfg,'confirm_min_atr_mult',0.10))
        # require break distance beyond trigger
        req_dist = max(min_pips * pip_size, (min_atr_mult * atr if atr else 0.0))
        ok_long  = (price - long_trigger) >= req_dist
        ok_short = (short_trigger - price) >= req_dist
        return ok_long, ok_short, req_dist

    def _expert_risk_pct_v2(self, spread_pips, adx):
        # adaptive risk: start from base, reduce for loss streak / high spread / meh ADX
        base = float(getattr(self.cfg,'risk_base_pct',0.50))
        rmin = float(getattr(self.cfg,'risk_min_pct',0.25))
        rmax = float(getattr(self.cfg,'risk_max_pct',1.00))
        step = float(getattr(self.cfg,'risk_loss_streak_step',0.10))
        sp_pen = float(getattr(self.cfg,'risk_spread_penalty',0.15))
        adx_pen = float(getattr(self.cfg,'risk_adx_penalty_dead',0.15))

        ls = int(getattr(self, 'loss_streak', 0) or 0)
        r = base - (ls * step)

        # spread penalty (assume 1.0 pip is “normal-ish”)
        if spread_pips is not None and spread_pips > 1.2:
            r -= sp_pen

        # ADX dead-ish zone penalty
        adx_dead_low = float(getattr(self.cfg,'adx_dead_low', 24.0))
        adx_trend_strong = float(getattr(self.cfg,'adx_trend_strong', 26.0))
        if adx is not None and adx_dead_low <= adx < adx_trend_strong:
            r -= adx_pen

        r = max(rmin, min(rmax, r))
        return r
"""
    # Insert methods just before the last line of the class block
    cls2 = cls + methods
    s = s[:start] + cls2 + s[end:]

# ---------- inject runtime expert gating near the entry decision ----------
# Insert just BEFORE the risk sizing line "units, sl_dist = self.units_for_risk(nav, atr)"
inj_pat = r"(?m)^\s*units,\s*sl_dist\s*=\s*self\.units_for_risk\(nav,\s*atr\)\s*$"
m = re.search(inj_pat, s)
if not m:
    raise SystemExit("❌ A12: couldn't find units_for_risk(nav, atr) line to inject expert gate.")

indent = re.match(r"^(\s*)", s[m.start():], flags=re.M).group(1)

gate = f"""{indent}# ===== EXPERT ENGINE V2 (runtime gates + adaptive risk) =====
{indent}if getattr(self.cfg,'expert_allow',True):
{indent}    try:
{indent}        bias2 = self._expert_bias_v2(closes if 'closes' in locals() else None)
{indent}        sq = self._expert_squeeze_v2(closes if 'closes' in locals() else None)
{indent}        # spread in pips if available
{indent}        spr_pips = None
{indent}        try:
{indent}            spr_pips = float(spread) / float(self._pip_size()) if 'spread' in locals() else None
{indent}        except Exception:
{indent}            spr_pips = None
{indent}        # adaptive risk_pct (applies globally)
{indent}        if getattr(self.cfg,'risk_brain_allow',True):
{indent}            self.cfg.risk_pct = self._expert_risk_pct_v2(spr_pips, adx)
{indent}            logging.info("EXPERT|RISK v2 risk_pct=%.2f loss_streak=%s spread_pips=%s adx=%.1f", float(self.cfg.risk_pct), getattr(self,'loss_streak',0), spr_pips, float(adx))
{indent}        # confirm filter
{indent}        okL=okS=True
{indent}        req_dist=0.0
{indent}        if getattr(self.cfg,'confirm_allow',True):
{indent}            okL, okS, req_dist = self._expert_confirm_v2(price, long_trigger, short_trigger, atr, self._pip_size())
{indent}            logging.info("EXPERT|CONFIRM v2 req_dist=%.5f okL=%s okS=%s", float(req_dist), okL, okS)
{indent}        # minervini gating for trend entries (do not block if bias2='none')
{indent}        if getattr(self.cfg,'minervini_allow',True) and getattr(self.cfg,'minervini_require_trend_for_trend_entries',True):
{indent}            if bias2 in ('bull','bear'):
{indent}                logging.info("EXPERT|MINERVINI v2 bias=%s", bias2)
{indent}            else:
{indent}                logging.info("EXPERT|MINERVINI v2 bias=none (no block)")
{indent}        # squeeze info
{indent}        if getattr(self.cfg,'squeeze_allow',True):
{indent}            logging.info("EXPERT|SQUEEZE v2 squeeze=%s", sq)
{indent}        # Apply gating to triggers (trend module): only allow long if bias bull; short if bias bear; and confirm ok
{indent}        # NOTE: does not change your donchian long_break/short_break calcs; only filters entry permission.
{indent}        if 'long_break' in locals() and 'short_break' in locals():
{indent}            if getattr(self.cfg,'minervini_allow',True) and bias2 == 'bear':
{indent}                long_break = False
{indent}                logging.info("EXPERT|GATE v2 block_long (bear bias)")
{indent}            if getattr(self.cfg,'minervini_allow',True) and bias2 == 'bull':
{indent}                short_break = False
{indent}                logging.info("EXPERT|GATE v2 block_short (bull bias)")
{indent}            if getattr(self.cfg,'confirm_allow',True):
{indent}                if not okL and long_break:
{indent}                    long_break = False
{indent}                    logging.info("EXPERT|GATE v2 block_long (confirm)")
{indent}                if not okS and short_break:
{indent}                    short_break = False
{indent}                    logging.info("EXPERT|GATE v2 block_short (confirm)")
{indent}    except Exception:
{indent}        logging.exception("EXPERT|ERROR v2")
"""

s = s[:m.start()] + gate + s[m.start():]

p.write_text(s, encoding="utf-8")
print("✅ A12 applied: Expert Engine v2 installed (EXPERT| tags + adaptive risk + minervini + confirm + squeeze)")
PY

python3 -m py_compile oanda_practice_bot.py
echo "✅ compile ok"
