#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date +%Y%m%d_%H%M%S)"
cp -v oanda_practice_bot.py "oanda_practice_bot.py.bak_A12_fix_v2_helpers_${TS}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("oanda_practice_bot.py")
s = p.read_text(encoding="utf-8")

# Find class and run() anchor (insert methods right before run)
m_class = re.search(r"(?m)^class\s+OandaHFDonchianBot\b.*?:\s*$", s)
if not m_class:
    raise SystemExit("❌ A12-fix: Couldn't find class OandaHFDonchianBot")

m_run = re.search(r"(?m)^\s{4}def\s+run\s*\(\s*self\b", s)
if not m_run:
    raise SystemExit("❌ A12-fix: Couldn't find def run(self, ...) to anchor insertion")

insert_at = m_run.start()

# Helper to check if a method exists INSIDE class (indent 4)
def has_method(name: str) -> bool:
    return bool(re.search(rf"(?m)^\s{{4}}def\s+{re.escape(name)}\s*\(", s))

need = ["_expert_bias_v2", "_expert_squeeze_v2", "_expert_confirm_v2", "_expert_risk_pct_v2",
        "_expert_sma", "_expert_std", "_expert_ema"]

missing = [n for n in need if not has_method(n)]

if not missing:
    print("ℹ️ A12-fix: All v2 helper methods already present in class; no changes.")
    raise SystemExit(0)

method_block = r'''
    # =========================
    # EXPERT v2 helper methods
    # =========================
    def _expert_sma(self, xs, n):
        try:
            if xs is None or n <= 0 or len(xs) < n:
                return None
            return sum(xs[-n:]) / float(n)
        except Exception:
            return None

    def _expert_std(self, xs, n):
        try:
            if xs is None or n <= 1 or len(xs) < n:
                return None
            import math
            seg = xs[-n:]
            mu = sum(seg) / float(n)
            var = sum((x - mu) ** 2 for x in seg) / float(n)
            return math.sqrt(var)
        except Exception:
            return None

    def _expert_ema(self, xs, n):
        try:
            if xs is None or n <= 1 or len(xs) < n:
                return None
            k = 2.0 / (n + 1.0)
            ema = xs[-n]
            for x in xs[-n+1:]:
                ema = (x * k) + (ema * (1.0 - k))
            return ema
        except Exception:
            return None

    def _expert_bias_v2(self, closes):
        """
        Returns one of: 'bull', 'bear', 'none'
        Safe: if insufficient history -> 'none'
        """
        try:
            if not closes:
                return "none"
            fast = int(getattr(self.cfg, 'minervini_ema_fast', 50))
            slow = int(getattr(self.cfg, 'minervini_ema_slow', 200))
            need = max(fast, slow) + 5
            if len(closes) < need:
                return "none"
            ef = self._expert_ema(closes[-(fast+5):], fast)
            es = self._expert_ema(closes[-(slow+5):], slow)
            if ef is None or es is None:
                return "none"
            if ef > es:
                return "bull"
            if ef < es:
                return "bear"
            return "none"
        except Exception:
            return "none"

    def _expert_squeeze_v2(self, closes):
        """
        True when BB width < KC width (squeeze on).
        Uses simple ATR proxy via average abs returns (no OHLC required).
        Returns: True/False or None if not enough data.
        """
        try:
            if not closes:
                return None
            n_bb = int(getattr(self.cfg, 'squeeze_bb_n', 20))
            k_bb = float(getattr(self.cfg, 'squeeze_bb_k', 2.0))
            n_kc = int(getattr(self.cfg, 'squeeze_kc_n', 20))
            k_kc = float(getattr(self.cfg, 'squeeze_kc_k', 1.5))
            need = max(n_bb, n_kc) + 5
            if len(closes) < need:
                return None

            sma = self._expert_sma(closes, n_bb)
            sd  = self._expert_std(closes, n_bb)
            if sma is None or sd is None:
                return None
            bb_w = (sma + k_bb * sd) - (sma - k_bb * sd)

            rets = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
            atrp = self._expert_sma(rets, n_kc) if rets else None
            if atrp is None:
                return None
            kc_w = 2.0 * k_kc * atrp
            return bool(bb_w < kc_w)
        except Exception:
            return None

    def _expert_confirm_v2(self, price, long_trigger, short_trigger, atr, pip_size):
        """
        Returns (ok_long, ok_short, req_dist)
        Confirms break beyond trigger by min(pips) and/or ATR fraction.
        """
        try:
            min_pips = float(getattr(self.cfg, 'confirm_min_break_pips', 0.8))
            min_atr_mult = float(getattr(self.cfg, 'confirm_min_atr_mult', 0.10))
            req_dist = max(min_pips * float(pip_size), (min_atr_mult * float(atr) if atr else 0.0))
            ok_long  = (float(price) - float(long_trigger)) >= req_dist
            ok_short = (float(short_trigger) - float(price)) >= req_dist
            return ok_long, ok_short, req_dist
        except Exception:
            return False, False, 0.0

    def _expert_risk_pct_v2(self, spread_pips, adx):
        """
        Adaptive risk: base - loss_streak*step - penalties for spread / weak ADX.
        Clamped to [risk_min_pct, risk_max_pct]
        """
        try:
            base = float(getattr(self.cfg, 'risk_base_pct', 0.50))
            rmin = float(getattr(self.cfg, 'risk_min_pct', 0.25))
            rmax = float(getattr(self.cfg, 'risk_max_pct', 1.00))
            step = float(getattr(self.cfg, 'risk_loss_streak_step', 0.10))
            sp_pen = float(getattr(self.cfg, 'risk_spread_penalty', 0.15))
            adx_pen = float(getattr(self.cfg, 'risk_adx_penalty_dead', 0.15))

            ls = int(getattr(self, 'loss_streak', 0) or 0)
            r = base - (ls * step)

            if spread_pips is not None and float(spread_pips) > 1.2:
                r -= sp_pen

            adx_dead_low = float(getattr(self.cfg, 'adx_dead_low', 24.0))
            adx_trend_strong = float(getattr(self.cfg, 'adx_trend_strong', 30.0))
            if adx is not None:
                a = float(adx)
                if a < adx_dead_low:
                    r -= adx_pen
                elif a < adx_trend_strong:
                    r -= (adx_pen * 0.50)

            if r < rmin: r = rmin
            if r > rmax: r = rmax
            return r
        except Exception:
            return float(getattr(self.cfg, 'risk_base_pct', 0.50))
'''

# Only insert once; if some are missing, add full block (safe + idempotent-ish)
s2 = s[:insert_at] + method_block + "\n" + s[insert_at:]
p.write_text(s2, encoding="utf-8")
print(f"✅ A12-fix: Inserted v2 helper block into class before run() (missing: {missing})")
PY

python3 -m py_compile oanda_practice_bot.py && echo "✅ compile ok"
