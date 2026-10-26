"""The five independent scalping experts.

Each expert:
  * decides entries on its own (entry) and exits on its own (exit_signal)
  * owns at most config.MAX_POSITIONS_PER_EXPERT symbols at once (enforced by the engine)
  * obeys the sector regime through allowed_dirs(style, regime)

Efficiency modifications vs. the textbook versions are marked [MOD].
"""
import math
import config
from common import Signal


def _nan(*xs):
    return any(x is None or (isinstance(x, float) and math.isnan(x)) for x in xs)


def _cents(x, down):
    c = round(x * 100, 6)
    return (math.floor(c) if down else math.ceil(c)) / 100


class Expert:
    id = "BASE"
    name = ""
    style = "trend"
    DEFAULTS = {}

    def __init__(self, overrides=None):
        self.p = {**self.DEFAULTS, **(overrides or {})}
        self.traded_today = set()

    @property
    def max_hold(self):
        return self.p["max_hold"]

    def new_day(self):
        self.traded_today = set()

    def on_open(self, symbol):
        self.traded_today.add(symbol)

    def in_window(self, a, i):
        return self.p["start_mod"] <= a["mod"][i] <= self.p["last_mod"]

    def entry(self, sym, a, i, dirs):
        raise NotImplementedError

    def exit_signal(self, a, i, pos):
        return None

    def _build(self, sym, side, a, i, raw_stop, reason, target=None):
        """[MOD] ATR-normalised stop: floored to avoid noise, capped to keep it a scalp."""
        px, atr = float(a["close"][i]), float(a["atr"][i])
        if _nan(px, atr, raw_stop) or atr <= 0:
            return None
        dist = abs(px - raw_stop)
        dist = max(dist, config.MIN_STOP_ATR * atr, config.MIN_STOP_PCT * px)
        dist = min(dist, config.MAX_STOP_ATR * atr)
        dist = max(dist, 0.02)                        # [FIX] at least 2 cents of room
        if config.TARGET_R is not None:               # [v2] fixed target in original-stop units
            target = px + side * config.TARGET_R * dist
        elif target is None:
            target = px + side * self.p["rr"] * dist
        elif _nan(target) or side * (target - px) < self.p.get("min_reward_r", 0.8) * dist:
            return None   # [MOD] skip trades with poor reward:risk
        dist = max(dist * config.STOP_FRACTION, 0.02)  # [v2] tighter stop
        # [FIX] round to cents AWAY from price so rounding can never collapse stop/target onto entry
        stop = _cents(px - side * dist, down=side > 0)
        target = _cents(target, down=side < 0)
        if side * (px - stop) < 0.01 or side * (target - px) < 0.01:
            return None
        return Signal(self.id, sym, side, px, stop, target, abs(px - stop), reason)


class OpeningRangeBreakout(Expert):
    """Toby Crabel-style Opening Range Breakout on a 15-minute range.
    [MOD] needs volume surge, VWAP agreement, fresh break, one trade per symbol per day."""
    id, name, style = "ORB", "Opening Range Breakout", "trend"
    DEFAULTS = dict(start_mod=15, last_mod=120, vol_mult=1.5, rr=1.5, max_hold=45)

    def entry(self, sym, a, i, dirs):
        if i < 2 or not self.in_window(a, i) or sym in self.traded_today:
            return None
        c, pc = a["close"][i], a["close"][i - 1]
        orh, orl, vw, va = a["or_high"][i], a["or_low"][i], a["vwap"][i], a["vol_avg"][i]
        if _nan(orh, orl, vw, va) or a["volume"][i] < self.p["vol_mult"] * va:
            return None
        mid = (orh + orl) / 2
        if 1 in dirs and c > orh and pc <= orh and c > vw:
            return self._build(sym, 1, a, i, mid, "ORB break up")
        if -1 in dirs and c < orl and pc >= orl and c < vw:
            return self._build(sym, -1, a, i, mid, "ORB break down")
        return None

    def exit_signal(self, a, i, pos):
        c, atr = a["close"][i], a["atr"][i]
        if pos.side > 0 and c < a["or_high"][i] - 0.5 * atr:
            return "failed_breakout"
        if pos.side < 0 and c > a["or_low"][i] + 0.5 * atr:
            return "failed_breakout"
        return None


class VWAPPullback(Expert):
    """Brian Shannon-style VWAP pullback: trend above/below VWAP, buy/sell the retest."""
    id, name, style = "VWAP", "VWAP Pullback", "trend"
    DEFAULTS = dict(start_mod=15, last_mod=config.LAST_ENTRY_MOD, rr=2.0, max_hold=30, touch_atr=0.15)

    def entry(self, sym, a, i, dirs):
        if i < 2 or not self.in_window(a, i):
            return None
        o, h, l, c, pc = a["open"][i], a["high"][i], a["low"][i], a["close"][i], a["close"][i - 1]
        vw, e9, e20, atr = a["vwap"][i], a["ema9"][i], a["ema20"][i], a["atr"][i]
        if _nan(vw, e9, e20, atr):
            return None
        t = self.p["touch_atr"] * atr
        if 1 in dirs and e9 > e20 > vw and l <= vw + t and c > vw and c > o and c > pc:
            return self._build(sym, 1, a, i, min(l, vw) - 0.25 * atr, "VWAP retest long")
        if -1 in dirs and e9 < e20 < vw and h >= vw - t and c < vw and c < o and c < pc:
            return self._build(sym, -1, a, i, max(h, vw) + 0.25 * atr, "VWAP retest short")
        return None

    def exit_signal(self, a, i, pos):
        c, vw, atr = a["close"][i], a["vwap"][i], a["atr"][i]
        if pos.side > 0 and c < vw - 0.25 * atr:
            return "lost_vwap"
        if pos.side < 0 and c > vw + 0.25 * atr:
            return "lost_vwap"
        return None


class EMAMomentum(Expert):
    """Momentum-scalper 9/20 EMA pullback: stacked EMAs, dip to the 9, break of prior bar.
    [MOD] rising 50-EMA filter and VWAP side filter."""
    id, name, style = "EMA", "9/20 EMA Momentum Pullback", "trend"
    DEFAULTS = dict(start_mod=10, last_mod=config.LAST_ENTRY_MOD, rr=2.0, max_hold=20, slope_bars=10)

    def entry(self, sym, a, i, dirs):
        sb = self.p["slope_bars"]
        if i < sb + 4 or not self.in_window(a, i):
            return None
        c, vw, atr = a["close"][i], a["vwap"][i], a["atr"][i]
        e9, e20, e50, e50p = a["ema9"][i], a["ema20"][i], a["ema50"][i], a["ema50"][i - sb]
        if _nan(e9, e20, e50, e50p, atr):
            return None
        if 1 in dirs and e9 > e20 > e50 and e50 > e50p and a["low"][i - 1] <= a["ema9"][i - 1] \
                and c > a["high"][i - 1] and c > vw:
            stop = a["low"][i - 3:i + 1].min() - 0.1 * atr
            return self._build(sym, 1, a, i, stop, "EMA pullback long")
        if -1 in dirs and e9 < e20 < e50 and e50 < e50p and a["high"][i - 1] >= a["ema9"][i - 1] \
                and c < a["low"][i - 1] and c < vw:
            stop = a["high"][i - 3:i + 1].max() + 0.1 * atr
            return self._build(sym, -1, a, i, stop, "EMA pullback short")
        return None

    def exit_signal(self, a, i, pos):
        c, e20 = a["close"][i], a["ema20"][i]
        if pos.side > 0 and c < e20:
            return "lost_ema20"
        if pos.side < 0 and c > e20:
            return "lost_ema20"
        return None


class RSI2Bollinger(Expert):
    """Larry Connors RSI(2) + Bollinger Band mean reversion, adapted to 1-min bars.
    [MOD] target = 20-SMA (the mean) with a min reward:risk check; skip crash/spike bars."""
    id, name, style = "RSI2BB", "RSI(2) + Bollinger Reversion", "reversion"
    DEFAULTS = dict(start_mod=15, last_mod=config.LAST_ENTRY_MOD, max_hold=15, rsi_lo=5, rsi_hi=95,
                    stop_atr=1.5, min_reward_r=0.8, max_bar_atr=3.0)

    def entry(self, sym, a, i, dirs):
        if i < 2 or not self.in_window(a, i):
            return None
        c, h, l = a["close"][i], a["high"][i], a["low"][i]
        r, atr, sma = a["rsi2"][i], a["atr"][i], a["sma20"][i]
        up, lo = a["bb_up"][i], a["bb_lo"][i]
        if _nan(r, atr, sma, up, lo) or (h - l) > self.p["max_bar_atr"] * atr:
            return None
        k = self.p["stop_atr"] * atr
        if 1 in dirs and r < self.p["rsi_lo"] and c < lo:
            return self._build(sym, 1, a, i, c - k, "RSI2 oversold < lower band", target=sma)
        if -1 in dirs and r > self.p["rsi_hi"] and c > up:
            return self._build(sym, -1, a, i, c + k, "RSI2 overbought > upper band", target=sma)
        return None

    def exit_signal(self, a, i, pos):
        c, r, sma = a["close"][i], a["rsi2"][i], a["sma20"][i]
        if pos.side > 0 and (c >= sma or r > 70):
            return "mean_reached"
        if pos.side < 0 and (c <= sma or r < 30):
            return "mean_reached"
        return None


class SqueezeBreakout(Expert):
    """John Carter TTM-Squeeze style: Bollinger inside Keltner, trade the release with momentum."""
    id, name, style = "SQZ", "Volatility Squeeze Breakout", "breakout"
    DEFAULTS = dict(start_mod=15, last_mod=config.LAST_ENTRY_MOD, rr=2.0, max_hold=30, min_squeeze=6)

    def entry(self, sym, a, i, dirs):
        if i < 3 or not self.in_window(a, i):
            return None
        fired = bool(a["sq_on"][i - 1]) and not bool(a["sq_on"][i]) and a["sq_count"][i - 1] >= self.p["min_squeeze"]
        if not fired:
            return None
        c, e20, atr, m, mp = a["close"][i], a["ema20"][i], a["atr"][i], a["mom"][i], a["mom"][i - 1]
        if _nan(e20, atr, m, mp):
            return None
        if 1 in dirs and m > 0 and m > mp and c > e20:
            return self._build(sym, 1, a, i, e20 - 0.25 * atr, "Squeeze fired up")
        if -1 in dirs and m < 0 and m < mp and c < e20:
            return self._build(sym, -1, a, i, e20 + 0.25 * atr, "Squeeze fired down")
        return None

    def exit_signal(self, a, i, pos):
        m0, m1, m2 = a["mom"][i], a["mom"][i - 1], a["mom"][i - 2]
        if pos.side > 0 and m0 < m1 < m2:
            return "momentum_fading"
        if pos.side < 0 and m0 > m1 > m2:
            return "momentum_fading"
        return None


def build_experts():
    classes = [OpeningRangeBreakout, VWAPPullback, EMAMomentum, RSI2Bollinger, SqueezeBreakout]
    return [cls(config.EXPERT_OVERRIDES.get(cls.id)) for cls in classes]
