"""The central brain: every expert says buy/sell/take-profit in its own way; the brain decides which signals become trades.

  1. volatility circuit breaker   ATR14/ATR50 above 2.5 (spike) or below 0.3 (flat, illiquid): no new entries
  2. regime router (ADX)          trend experts only when ADX >= split, range experts only below it
  3. multi-timeframe filter       trend experts need the last completed 15-minute bar's trend up
  4. relative-volume check        breakout experts need RVOL >= 1.2 (already part of donchian_turtle)
  5. consensus                    consensus=1 takes any expert; 2 needs two different experts within the last `window` bars
  6. one position per symbol, a re-entry cooldown after each exit (bars), a per-day trade cap per symbol
  7. priority                     when several experts fire together, the one ranked first in `priority` (best train expectancy) wins
  8. sector heat map (gates.py)    sector_filter=True: no longs while the symbol's sector is BEARISH
  9. shorts                       short_experts: the same experts on the mirrored series, ONLY while the market or the sector is BEARISH
                                  (short_gate="bearish"), or also while SPY is red on the day (short_gate="red"). A bar where both a
                                  long and a short fire is skipped (the experts disagree).
Sizing (1% of equity at risk per trade), the slot cap and the daily loss limit live in engine.Portfolio. Trade tags carry the expert name, so
results can be split by expert.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dataclasses import replace

from .experts import ALL_EXPERTS, BOX_STRATEGIES, RANGE_EXPERTS, TREND_EXPERTS, Experts, short_view
from .strategies import Signal


@dataclass
class Brain:
    experts: list = field(default_factory=lambda: list(ALL_EXPERTS))
    priority: list = field(default_factory=list)       # expert names, best first (empty = ALL_EXPERTS order)
    route: bool = True
    adx_split: float = 25.0
    mtf: bool = True
    consensus: int = 1
    window: int = 3
    vol_lo: float = 0.3
    vol_hi: float = 2.5
    cooldown: int = 1          # bars to wait after an exit (one 5-minute bar is the closest we can get to 60 s)
    max_trades: int = 3
    entry_from: str = "09:50"
    entry_to: str = "15:30"
    name: str = "brain"
    short_experts: list = field(default_factory=list)  # empty = no shorting
    sector_filter: bool = False
    short_gate: str = "bearish"                        # "bearish" (market or sector BEARISH) or "red" (that, or SPY red on the day)
    exit_mode: str = "own"                             # "own": each expert's exits; "staircase": every trade on the trailing staircase

    def _allowed(self, d: pd.DataFrame, name: str, i: int) -> bool:
        r = d.iloc[i]
        ratio = r["f_atr_ratio"]
        if not np.isfinite(ratio) or ratio > self.vol_hi or ratio < self.vol_lo:
            return False
        if self.route:
            trending = r["f_adx"] >= self.adx_split
            if name in TREND_EXPERTS and not trending:
                return False
            if name in RANGE_EXPERTS and trending:
                return False
        if self.mtf and name in TREND_EXPERTS and not bool(r["f_mtf_up"]):
            return False
        return True

    def signals(self, d: pd.DataFrame) -> list[Signal]:
        longs = self._side(d, self.experts) if self.experts else []
        if self.sector_filter and "g_long_ok" in d:
            ok = d["g_long_ok"].to_numpy(bool)
            longs = [s for s in longs if ok[s.i]]
        shorts = []
        if self.short_experts and "g_short_ok" in d and "m_f_atr" in d:     # never short without the gate and the mirrored features
            gate = d["g_short_ok"].to_numpy(bool)
            if self.short_gate == "red" and "g_spy_red" in d:
                gate = gate | d["g_spy_red"].to_numpy(bool)
            shorts = [replace(s, side=-1, tag="S:" + s.tag) for s in self._side(short_view(d), self.short_experts) if gate[s.i]]
        both = {s.i for s in longs} & {s.i for s in shorts}
        return sorted([s for s in longs + shorts if s.i not in both], key=lambda s: s.i)

    def _side(self, d: pd.DataFrame, experts: list) -> list[Signal]:
        found = Experts((self.entry_from, self.entry_to)).signals(d, experts)
        if self.exit_mode == "staircase":      # 1.5 ATR stop, break-even at +1 ATR, 1.5 ATR trail, ADX-scaled top step; no time/indicator exit
            a, x = d["f_atr"].to_numpy(float), d["f_adx"].to_numpy(float)
            found = [(n, replace(s, stop_dist=1.5 * a[s.i], target_r=0.0, max_bars=0, managed=True, atr=a[s.i],
                                 adx=x[s.i] if np.isfinite(x[s.i]) else 0.0, trail=1.5, tp_scale=1.0, exit_col=""))
                     for n, s in found if np.isfinite(a[s.i]) and a[s.i] > 0]
        rank = {n: k for k, n in enumerate(self.priority or ALL_EXPERTS)}
        out, last_i = [], {}
        by_bar: dict[int, list] = {}
        for name, s in found:
            if self._allowed(d, name, s.i):
                by_bar.setdefault(s.i, []).append((name, s))
        seen: list[tuple[int, str]] = []
        for i in sorted(by_bar):
            seen.append((i, ""))
            names_here = {n for n, _ in by_bar[i]}
            recent = {n for k, bars in by_bar.items() if i - self.window < k <= i for n, _ in bars}
            if len(recent) < self.consensus:
                continue
            name, s = sorted(by_bar[i], key=lambda x: rank.get(x[0], 99))[0]
            out.append(s)
            if len(out) >= self.max_trades * 6:
                break
        return out

    def placebo(self, d: pd.DataFrame, n: int, rng: np.random.Generator) -> list[Signal]:
        t = d.index.strftime("%H:%M")
        idx = np.flatnonzero(((t >= self.entry_from) & (t <= self.entry_to) & d["f_atr"].notna() & d["f_adx"].notna()).to_numpy())
        if len(idx) == 0 or n == 0:
            return []
        out = []
        for i in sorted(rng.choice(idx, size=min(n, len(idx)), replace=False)):
            a = float(d["f_atr"].iloc[i])
            out.append(Signal(int(i), 1, 1.5 * a, 0.0, 0, "placebo", True, a, float(d["f_adx"].iloc[i]), 1.5, 1.0))
        return out



class DirectionFirst:
    """The owner's rule: establish each stock's direction first (zonemap.bias on daily bars, set on the session frame as attrs["bias"]),
    then trade only with it - longs in an up bias, shorts in a down bias; in a range bias both sides but only the mean-reversion
    experts. Wraps a Brain; everything else (exits, zones, caps) is the brain's."""

    def __init__(self, brain):
        self.brain = brain

    def __getattr__(self, name):
        return getattr(self.brain, name)

    def keep(self, s, b: int) -> bool:
        name = (s.tag or "").removeprefix("S:")
        if b == 1:
            return s.side == 1
        if b == -1:
            return s.side == -1
        return name in RANGE_EXPERTS or name in BOX_STRATEGIES

    def signals(self, day):
        b = int(day.attrs.get("bias", 0))
        return [s for s in self.brain.signals(day) if self.keep(s, b)]


class StageFilter:
    """Weinstein's stage on top of any brain: longs only in stage 2, shorts only in stage 4 (day.attrs["stage"], from completed daily bars;
    0 = unknown history -> no filter)."""

    def __init__(self, brain):
        self.brain = brain

    def __getattr__(self, name):
        return getattr(self.brain, name)

    def signals(self, day):
        st = int(day.attrs.get("stage", 0))
        if not st:
            return self.brain.signals(day)
        return [s for s in self.brain.signals(day) if stage_ok(s, st)]


def stage_ok(s, st: int) -> bool:
    """Weinstein: longs in stage 2, shorts in stage 4; the owner's box strategies also in the sideways stages (longs 1-2, shorts 3-4)."""
    if not st:
        return True
    if (s.tag or "").removeprefix("S:") in BOX_STRATEGIES:
        return st in ((1, 2) if s.side == 1 else (3, 4))
    return (s.side == 1 and st == 2) or (s.side == -1 and st == 4)


class BoxSignals:
    """The owner's range box as its OWN strategy for stocks (paper drawing 2026-10-16: Weinstein -> trend indicators -> range box; buy zone, TP1 /
    TP2 above it, Sell 1 / Sell 2 / Sell ALL below it, breakout beyond either edge). Adds "range_box" (long) and "S:range_box" (short) signals to
    any brain and lets the wrappers around it (direction first, stage, multi-timeframe) filter them like every other expert. The BOX is the stock's
    daily box (day.attrs["box"] = valid, top, bot; day.attrs["box_atr"] = daily ATR14). Long: between 10:00 and 15:00 a 5-minute bar dips into
    the bottom 15% of the box, closes back above the box low and inside the bottom third, and closes up; stop ("Sell ALL") 0.5 daily ATR below the
    box low; take-profit = the far edge less 0.25 daily ATR (the live ladder's TP steps and cuts apply). Short = mirror. One entry per side per
    day, and only when the stop is at most 3 daily ATR away and the target at least 0.75R away."""

    TAG = "range_box"

    def __init__(self, brain):
        self.brain = brain

    def __getattr__(self, name):
        return getattr(self.brain, name)

    def box_signals(self, day) -> list:
        valid, top, bot = day.attrs.get("box", (False, 0.0, 0.0))
        datr = float(day.attrs.get("box_atr", 0.0))
        if not valid or top <= bot or datr <= 0 or not len(day):
            return []
        h = top - bot
        t = day.index.strftime("%H:%M")
        win = np.asarray((t >= "10:00") & (t <= "15:00"))
        o, hi, lo, c = (day[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        fa = day["f_atr"].to_numpy(float) if "f_atr" in day else np.full(len(day), datr / 6.0)
        out = []
        buy = win & (lo <= bot + 0.15 * h) & (c > bot) & (c <= bot + 0.33 * h) & (c > o)
        sell = win & (hi >= top - 0.15 * h) & (c < top) & (c >= top - 0.33 * h) & (c < o)
        for side, mask in ((1, buy), (-1, sell)):
            for i in np.flatnonzero(mask):
                stop = (c[i] - (bot - 0.5 * datr)) if side == 1 else ((top + 0.5 * datr) - c[i])
                tgt = (top - 0.25 * datr) if side == 1 else (bot + 0.25 * datr)
                if not (0 < stop <= 3 * datr) or side * (tgt - c[i]) < 0.75 * stop:
                    continue
                a = float(fa[i]) if np.isfinite(fa[i]) and fa[i] > 0 else datr / 6.0
                out.append(Signal(i=int(i), side=side, stop_dist=float(stop), tag=self.TAG if side == 1 else "S:" + self.TAG, atr=a,
                                  target_px=float(tgt)))
                break                                          # one entry per side per day
        return out

    def signals(self, day):
        return sorted(list(self.brain.signals(day)) + self.box_signals(day), key=lambda s: s.i)


class FibBoxSignals(BoxSignals):
    """The owner's range box with FIBONACCI zones (his idea 2026-10-16, live on paper at reduced size without a backtest, by his decision).
    Levels from the daily box (low = the 1.0 retracement, high = 0.0): BUY ZONE = the 0.618-0.786 retracement (bot + 0.214h .. bot + 0.382h);
    a 5-minute bar between 10:00 and 15:00 trades into it and closes up inside it. STOP ("Sell ALL") 0.25 daily ATR below the box low.
    TP1 = the 0.5 level (box middle), TP2 = the 0.236 level (bot + 0.764h); the drawn cuts and the trailing rider apply. Needs TP2 at least
    1R away. Short = mirror. One entry per side per day. Tag "fib_box" so its record is kept apart from the plain box."""

    TAG = "fib_box"

    def box_signals(self, day) -> list:
        valid, top, bot = day.attrs.get("box", (False, 0.0, 0.0))
        datr = float(day.attrs.get("box_atr", 0.0))
        if not valid or top <= bot or datr <= 0 or not len(day):
            return []
        h = top - bot
        t = day.index.strftime("%H:%M")
        win = np.asarray((t >= "10:00") & (t <= "15:00"))
        o, hi, lo, c = (day[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        fa = day["f_atr"].to_numpy(float) if "f_atr" in day else np.full(len(day), datr / 6.0)
        z0, z1 = bot + 0.214 * h, bot + 0.382 * h                    # long buy zone (0.786 .. 0.618 retracement)
        s0, s1 = top - 0.382 * h, top - 0.214 * h                    # short sell zone (mirror)
        buy = win & (lo <= z1) & (c >= z0) & (c <= z1) & (c > o)
        sell = win & (hi >= s0) & (c >= s0) & (c <= s1) & (c < o)
        out = []
        for side, mask in ((1, buy), (-1, sell)):
            for i in np.flatnonzero(mask):
                stop = (c[i] - (bot - 0.25 * datr)) if side == 1 else ((top + 0.25 * datr) - c[i])
                tp1 = bot + 0.5 * h
                tp2 = (bot + 0.764 * h) if side == 1 else (top - 0.764 * h)
                if not (0 < stop <= 3 * datr) or side * (tp2 - c[i]) < stop or side * (tp1 - c[i]) <= 0:
                    continue
                a = float(fa[i]) if np.isfinite(fa[i]) and fa[i] > 0 else datr / 6.0
                out.append(Signal(i=int(i), side=side, stop_dist=float(stop), tag=self.TAG if side == 1 else "S:" + self.TAG, atr=a,
                                  target_px=float(tp1), target2_px=float(tp2)))
                break
        return out


class HybridBoxSignals(BoxSignals):
    """The owner's HYBRID zones on stocks (2026-10-16, live on paper at reduced size without a backtest, by his decision): the entry level is
    where the daily range box, its Fibonacci retracements and the stock's own swing levels agree (zonemap.hybrid_plan, day.attrs "box",
    "box_atr", "levels"). A 5-minute bar between 10:00 and 15:00 trades down to the entry level (within 0.25 daily ATR), closes back above it
    by no more than 0.5 ATR, and closes up. Stop, TP1 and TP2 from zonemap.hybrid_trade. Short = mirror. One entry per side per day."""

    TAG = "hybrid_box"

    def box_signals(self, day) -> list:
        from .zonemap import HYBRID_RULES, hybrid_plan, hybrid_trade
        valid, top, bot = day.attrs.get("box", (False, 0.0, 0.0))
        datr = float(day.attrs.get("box_atr", 0.0))
        if not valid or top <= bot or not (datr > 0) or not len(day):
            return []
        r = HYBRID_RULES
        t = day.index.strftime("%H:%M")
        win = np.asarray((t >= "10:00") & (t <= "15:00"))
        o, hi, lo, c = (day[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        fa = day["f_atr"].to_numpy(float) if "f_atr" in day else np.full(len(day), datr / 6.0)
        out = []
        for side in (1, -1):
            plan = hybrid_plan(top, bot, datr, day.attrs.get("levels"), side)
            if not plan:
                continue
            L, z = plan["entry"], r["zone_atr"] * datr
            if side == 1:
                mask = win & (lo <= L + z) & (c >= L) & (c <= L + r["chase_atr"] * datr) & (c > o)
            else:
                mask = win & (hi >= L - z) & (c <= L) & (c >= L - r["chase_atr"] * datr) & (c < o)
            for i in np.flatnonzero(mask):
                tr = hybrid_trade(plan, bot, top, datr, side, float(c[i]))
                if not tr or tr["stop_dist"] > 3 * datr:
                    continue
                a = float(fa[i]) if np.isfinite(fa[i]) and fa[i] > 0 else datr / 6.0
                out.append(Signal(i=int(i), side=side, stop_dist=float(tr["stop_dist"]), tag=self.TAG if side == 1 else "S:" + self.TAG,
                                  atr=a, target_px=tr["tp1"], target2_px=tr["tp2"]))
                break
        return out


class BoxConfirmSignals(BoxSignals):
    """The owner's range box with candlestick + volume + volatility confirmation on stocks (alpacabot.confirm, 2026-10-16): each 5-minute
    close outside the stock's daily box is read as CONTINUING (strong candle, volume >= vol_mult x the prior 20 bars, 5-minute ATR >=
    atr_ratio x its session average -> trade the breakout) or FAILING (weak candle / no volume, price closes back inside -> fade it).
    Between 10:00 and 15:00; one entry per side per day. Tag "box_confirm"."""

    TAG = "box_confirm"

    def __init__(self, brain, mode: str = "both", vol_mult: float = 1.5, atr_ratio: float = 1.1, vote: float = 0.0):
        super().__init__(brain)
        self.mode, self.vol_mult, self.atr_ratio, self.vote = mode, vol_mult, atr_ratio, vote

    def box_signals(self, day) -> list:
        from .confirm import confirm_signals
        valid, top, bot = day.attrs.get("box", (False, 0.0, 0.0))
        if not valid or top <= bot or len(day) < 22:
            return []
        n = len(day)
        t = day.index.strftime("%H:%M")
        allow = np.asarray((t >= "10:00") & (t <= "15:00"))
        hi, lo, cl = (day[k].to_numpy(float) for k in ("high", "low", "close"))
        if "f_atr" in day:
            a = day["f_atr"].to_numpy(float)
        else:
            tr = np.maximum(hi - lo, np.abs(hi - np.r_[cl[0], cl[:-1]]))
            a = pd.Series(tr).rolling(14, min_periods=5).mean().to_numpy()
        al = pd.Series(a).expanding(min_periods=12).mean().shift(1).to_numpy()        # the session's average so far
        r = confirm_signals(day["open"], hi, lo, cl, day["volume"] if "volume" in day else None, a, al, np.full(n, top), np.full(n, bot),
                            np.ones(n, bool), mode=self.mode, vol_mult=self.vol_mult, atr_ratio=self.atr_ratio, allow=allow, vote=self.vote)
        out, seen = [], set()
        for i in np.flatnonzero(r["long"] | r["short"]):
            side = 1 if r["long"][i] else -1
            if side in seen:
                continue
            seen.add(side)
            tp1 = float(r["tp1"][i])
            tp2 = float(cl[i] + side * r["tgt"][i]) if np.isfinite(r["tgt"][i]) else 0.0
            if side * (tp2 - tp1) <= 0:
                tp2 = 0.0
            out.append(Signal(i=int(i), side=side, stop_dist=float(r["stop"][i]), tag=self.TAG if side == 1 else "S:" + self.TAG,
                              atr=float(a[i]) if np.isfinite(a[i]) else 0.0, target_px=tp1, target2_px=tp2))
        return out


class TrendStairsSignals(BoxSignals):
    """The owner's trend_stairs design (drawing 2026-10-17, levels from alpacabot.levels on 58 stocks x 3 months): trade ONLY while the trend is
    up on the 15-minute AND 1-hour bars (the daily stage / direction is checked by the live chain's direction-first + Weinstein filters), and buy
    the pullback when at least `need` of 4 indicators agree:
      1. price at the 20 MA (within near_ma_atr ATR: real pullback lows sat -0.8 .. +0.7 ATR from it),
      2. price holding VWAP (close >= VWAP - vwap_atr ATR: a quarter of real pullback lows dipped 0.75 ATR under it),
      3. the 5-minute stochastic crosses up (below 70),
      4. the 1-hour stochastic <= h1_stoch_max (1-hour stochastic 20-30 in a full uptrend bounced 63% of the time).
    Stop ("sell zone"): stop_atr ATR under the 20 MA (0.8-2 ATR from entry). TP1 = +tp1_atr ATR (the ladder adds TP2 +0.6 ATR later = the
    median 1.9 ATR move); the stair-step stop follows (engine.EXPERT_LADDERS["trend_stairs"]); sell when 2 of 3 sell indicators agree or the
    1-hour trend turns (experts x_stairs). Re-enters after an exit while the trend lasts ("as long as we are trending we buy"). Shorts mirror.
    Between 9:45 and 15:15. Tag "trend_stairs"."""

    TAG = "trend_stairs"
    NEED = ("f_bb_mid", "f_vwap", "f_atr", "f_stk", "f_std", "f_m15_dir", "f_h1_dir", "f_h1_stoch")

    def __init__(self, brain, need: int = 3, h1_stoch_max: float = 40.0, near_ma_atr: float = 0.8, vwap_atr: float = 0.75,
                 tp1_atr: float = 1.3, stop_atr: float = 1.2, sr_rsi: bool = False, need6: int = 4, sides: tuple = (1, -1),
                 confirm: float = 0.0, select: bool = False, tp_zone: bool = False, list_key: str = "ts_ok"):
        super().__init__(brain)
        self.sides = tuple(sides)
        self.select = select        # owner's daily funnel (alpacabot.selection): only stocks on today's long / short list (day.attrs["ts_ok"])
        self.tp_zone = tp_zone      # owner's TP box: also exit when 2 of 4 say the move is stretched (experts x_stairs_tp)
        self.list_key = list_key    # which daily list on day.attrs (research: "ts_ok" = 3 of 3 trend strength, "ts_ok2" = 2 of 3, ...)
        self.confirm = confirm      # owner 2026-10-17: the trend confirmed by candles, volume, volatility (confirm.trend_votes >= confirm x 5)
        self.need, self.h1max, self.near, self.vwap_atr, self.tp1, self.stop_atr = need, h1_stoch_max, near_ma_atr, vwap_atr, tp1_atr, stop_atr
        self.sr_rsi, self.need6 = sr_rsi, need6     # owner 2026-10-17: + S/R (the stock's daily zone map) and RSI 40-55 turning: need6 of 6

    def box_signals(self, day) -> list:
        if len(day) < 3:
            return []
        t = day.index.strftime("%H:%M")
        win = np.asarray((t >= "09:45") & (t <= "15:15"))
        out = []
        ok_lists = day.attrs.get(self.list_key) if self.select else None
        for side, p in ((1, ""), (-1, "m_")):
            if side not in self.sides or any(p + k not in day for k in self.NEED):
                continue
            if self.select and not (ok_lists and ok_lists[0 if side == 1 else 1]):
                continue                                       # not on today's list for this side
            g = lambda k: day[p + k].to_numpy(float)          # noqa: E731  (mirrored features for the short side)
            c = side * day["close"].to_numpy(float)
            mid, vw, a, k, d = g("f_bb_mid"), g("f_vwap"), g("f_atr"), g("f_stk"), g("f_std")
            with np.errstate(invalid="ignore"):
                trend = (g("f_m15_dir") == 1) & (g("f_h1_dir") == 1)
                v1 = np.abs(c - mid) <= self.near * a
                v2 = c >= vw - self.vwap_atr * a
                kp, dp = np.r_[np.nan, k[:-1]], np.r_[np.nan, d[:-1]]
                v3 = (k > d) & (kp <= dp) & (k < 70)
                v4 = g("f_h1_stoch") <= self.h1max
                votes = v1.astype(int) + v2.astype(int) + v3.astype(int) + v4.astype(int)
                need = self.need
                if self.sr_rsi:
                    lv = [z["price"] for z in (day.attrs.get("levels") or []) if z.get("strong")]
                    rl, rh, rc = (day[x].to_numpy(float) for x in ("low", "high", "close"))
                    v5 = np.zeros(len(day), bool)
                    for L in lv:                     # real prices: a long tests strong support and holds it; a short tests resistance
                        v5 |= ((rl <= L + a) & (rc >= L)) if side == 1 else ((rh >= L - a) & (rc <= L))
                    r_ = g("f_rsi14")                # mirrored for shorts: 40-55 rising there = 45-60 falling on the real chart
                    v6 = (r_ >= 40) & (r_ <= 55) & (r_ > np.r_[np.nan, r_[:-1]])
                    votes = votes + v5.astype(int) + v6.astype(int)
                    need = self.need6
                ok = win & trend & (votes >= need) & (a > 0)
            edge = ok & ~np.r_[False, ok[:-1]]                # a new buy setup (not every bar of the same one)
            for i in np.flatnonzero(edge):
                if self.confirm > 0:
                    from .confirm import trend_votes
                    if trend_votes(day, int(i), side)[0] / 5.0 < self.confirm - 1e-9:
                        continue                               # the trend is not confirmed on the signal bar
                sd = float(np.clip(c[i] - (mid[i] - self.stop_atr * a[i]), 0.8 * a[i], 2.0 * a[i]))
                out.append(Signal(i=int(i), side=side, stop_dist=sd, tag=self.TAG if side == 1 else "S:" + self.TAG, atr=float(a[i]),
                                  exit_col="x_stairs_tp" if self.tp_zone else "x_stairs", target_px=float(side * (c[i] + self.tp1 * a[i]))))
        return out


class ScalperSignals(BoxSignals):
    """Owner 2026-10-19 (scalper redesign), longs only, 5-minute bars, between 10:00 and 15:00 ET: buy a pullback inside an uptrend.
      trend      the 15-minute AND 1-hour trends are up (the daily stage / direction come from the live chain's wrappers),
      oversold   at least `need` of 5 oversold readings (confirm.oversold_votes) on the signal bar,
      reversal   the bar closes up and its volume is at least `vol_mult` x the average of the prior 20 bars.
    R = one 5-minute ATR: the hard stop is stop_r R under the close, the one take-profit tp_r R over it (target_px), and the time stop is `hold`
    bars (6 = 30 minutes). Tag "scalper"."""

    TAG = "scalper"
    NEED = ("f_atr", "f_m15_dir", "f_h1_dir", "volume")

    def __init__(self, brain, need: int = 3, stop_r: float = 0.5, tp_r: float = 1.25, hold: int = 6, vol_mult: float = 1.2, trend: str = "both"):
        super().__init__(brain)
        self.need, self.stop_r, self.tp_r, self.hold, self.vol_mult = need, stop_r, tp_r, hold, vol_mult
        self.trend = trend      # "both" = 15-minute AND 1-hour up, "m15" = the 15-minute only, "none" = no intraday trend condition (research)

    def box_signals(self, day) -> list:
        if len(day) < 25 or any(k not in day for k in self.NEED):
            return []
        from .confirm import oversold_votes
        t = day.index.strftime("%H:%M")
        win = np.asarray((t >= "10:00") & (t <= "15:00"))
        o, c, v = (day[k].to_numpy(float) for k in ("open", "close", "volume"))
        a = day["f_atr"].to_numpy(float)
        vavg = pd.Series(v).rolling(20, min_periods=20).mean().shift(1).to_numpy()
        with np.errstate(invalid="ignore"):
            m15, h1 = day["f_m15_dir"].to_numpy(float) == 1, day["f_h1_dir"].to_numpy(float) == 1
            trend = (m15 & h1) if self.trend == "both" else m15 if self.trend == "m15" else np.ones(len(day), bool)
            base = win & trend & (c > o) & (v >= self.vol_mult * vavg) & (a > 0)
        out = []
        prev = False
        for i in range(len(day)):
            ok = bool(base[i]) and oversold_votes(day, i)[0] >= self.need
            if ok and not prev:                                    # a new setup, not every bar of the same one
                out.append(Signal(i=i, side=1, stop_dist=float(self.stop_r * a[i]), max_bars=int(self.hold), tag=self.TAG, atr=float(a[i]),
                                  target_px=float(c[i] + self.tp_r * a[i])))
            prev = ok
        return out


VELEZ_TAGS = ("velez_halt", "velez_vbs", "velez_rbi")
VELEZ_RESEARCH = ("velez_gift", "velez_nrb", "velez_orb", "velez_trap", "velez_ldb")      # owner 2026-10-20 second list (research only, not live)
VELEZ_RESEARCH2 = ("velez_bt", "velez_clear", "velez_bull180", "velez_2020", "velez_mortgage", "velez_flat20", "velez_flat200")   # owner 2026-10-21 "all those rules"
VELEZ_TOD = ("09:35", "10:00", "10:30", "11:15", "12:00", "13:30", "14:30", "15:00")      # his historical reversal-time watchlist (ET)


class VelezSignals(BoxSignals):
    """Owner 2026-10-20: Oliver Velez's price-action rules as a long scalper on the 5-minute chart (his older Micro Trading charts; his newer 2-minute
    chart is not in our data). Environment: the SIMPLE 20 average rising and above the 200 (the 20 SMA is the trend guide and support), between
    09:45 and 15:15. Three isolated setups, each its own tag so the live record is kept apart:
      velez_halt  20 MA Halt: a pullback tags the 20 SMA (low within 0.3 ATR of it), the close holds above it and the bar closes up,
      velez_vbs   Velez Buy Setup / 3-5 bar rule: 3 to 5 lower closes in a row, then a bar that closes up through the prior bar's high,
      velez_rbi   Red Bar Ignored: a red bar, then a green bar that closes above the red bar's high.
    Confirmed entries (the completed bar, filled at the next open). One-bar stop: under the trigger bar's low (0.1 ATR buffer, kept between 0.5 and
    2 ATR). Target tp_r x the stop distance, the 20 SMA breaking (x_velez) or a time stop of `hold` bars exits early. Shorts are not built
    (every short test so far lost)."""

    NEED = ("f_atr", "f_sma20", "f_sma200", "x_velez")

    def __init__(self, brain, setups=VELEZ_TAGS, tp_r: float = 1.5, hold: int = 18, buffer_atr: float = 0.1, halt_atr: float = 0.3,
                 look_left: bool = False, tod: tuple = (), stop_mult: float = 1.0, sig_exit: bool = True):
        super().__init__(brain)
        self.setups, self.tp_r, self.hold, self.buffer, self.halt_atr = tuple(setups), tp_r, hold, buffer_atr, halt_atr
        self.look_left = look_left      # research: reject a setup when a prior 20-bar high sits between the entry and the target (Velez's "look left")
        self.stop_mult = stop_mult      # research: widen the one-bar stop (day-trade style holds)
        self.sig_exit = sig_exit        # research: False = no "20 SMA broke" exit, only the stop, the target and the time stop
        self.tod = tuple(tod)           # research: only setups within 10 minutes after one of these times (his reversal-time watchlist)

    def box_signals(self, day) -> list:
        if len(day) < 8 or any(k not in day for k in self.NEED):
            return []
        t = day.index.strftime("%H:%M")
        win = np.asarray((t >= "09:45") & (t <= "15:15"))
        o, h, l, c = (day[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        a, ma, m200 = (day[k].to_numpy(float) for k in ("f_atr", "f_sma20", "f_sma200"))
        n = len(day)
        sh = lambda x, k: np.r_[np.full(k, np.nan), x[:-k]]               # noqa: E731  (x shifted k bars back)
        with np.errstate(invalid="ignore"):
            env = win & (ma > sh(ma, 3)) & (ma > m200) & (a > 0)
            up = c > o
            halt = env & up & (l <= ma + self.halt_atr * a) & (c > ma) & (sh(c, 1) <= sh(ma, 1) + self.halt_atr * a)
            down = [sh(c, k) < sh(c, k + 1) for k in (1, 2, 3)]                   # closes 1, 2, 3 bars back each lower than the one before
            run = down[0] & down[1] & down[2]
            vbs = env & up & run & (c > sh(h, 1))
            rbi = env & up & (sh(c, 1) < sh(o, 1)) & (c > sh(h, 1))
        masks = {"velez_halt": halt, "velez_vbs": vbs, "velez_rbi": rbi}
        if any(x in self.setups for x in VELEZ_RESEARCH):
            masks.update(self._research_masks(day, t, o, h, l, c, a, ma, m200, env, up, sh))
        if any(x in self.setups for x in VELEZ_RESEARCH2):
            masks.update(self._research_masks2(day, t, o, h, l, c, a, ma, m200, env, up, sh))
        if self.tod:
            mins = np.array([int(x[:2]) * 60 + int(x[3:]) for x in t])
            near = np.zeros(n, bool)
            for x in self.tod:
                near |= (mins >= int(x[:2]) * 60 + int(x[3:])) & (mins < int(x[:2]) * 60 + int(x[3:]) + 10)
            masks = {k: v & near for k, v in masks.items()}
        out, taken = [], set()
        for tag in self.setups:
            m = masks[tag]
            prev = np.r_[False, m[:-1]]
            for i in np.flatnonzero(m & ~prev):
                if int(i) in taken:
                    continue
                taken.add(int(i))
                lo = min(l[i], l[i - 1]) if tag == "velez_vbs" else l[i]
                sd = float(np.clip((c[i] - lo + self.buffer * a[i]) * self.stop_mult, 0.5 * a[i], 2.0 * self.stop_mult * a[i]))
                if self.look_left and i >= 20 and c[i] < h[i - 20:i].max() < c[i] + self.tp_r * sd:
                    continue                                   # a prior high blocks the way to the target
                out.append(Signal(i=int(i), side=1, stop_dist=sd, max_bars=int(self.hold), tag=tag, atr=float(a[i]), exit_col="x_velez" if self.sig_exit else "",
                                  target_px=float(c[i] + self.tp_r * sd)))
        return sorted(out, key=lambda s: s.i)


    @staticmethod
    def _research_masks2(day, t, o, h, l, c, a, ma, m200, env, up, sh) -> dict:
        """Velez's remaining setups that fit 5-minute bars (owner 2026-10-21): bottoming tail, clearing elephant, Bull 180 on the SIMPLE averages,
        the 20/20 bar and the Mortgage play, the flat-20 range scalp and the flat-200 reversal. Long side only."""
        n = len(day)
        rng = np.maximum(h - l, 1e-9)
        win = np.asarray((t >= "09:45") & (t <= "15:15"))
        with np.errstate(invalid="ignore"):
            # Bottoming tail: a long lower wick (at least 60% of the range) closing in the top third, at or under the 20 SMA's reach, in an uptrend
            bt = win & (ma > m200) & ((np.minimum(o, c) - l) >= 0.6 * rng) & ((h - c) <= 0.34 * rng) & (rng >= 0.6 * a) & (l <= ma + 0.5 * a)
            # Clearing elephant: an elephant bar that closes above the highs of the last 5 bars out of a narrow (compressed) range
            hi5 = np.full(n, np.nan)
            for i in range(5, n):
                hi5[i] = h[i - 5:i].max()
            spread = np.abs(ma - m200)
            ele = (rng >= 1.5 * a) & ((c - o) >= 0.7 * rng) & ((h - c) <= 0.2 * rng) & up
            clear = win & ele & (c > hi5) & (spread <= 3.0 * a)
            # Bull 180 (simple averages): a bullish bar that fully reverses the prior bearish bar, in an uptrend (20 over the 200, 20 rising)
            b180 = win & (ma > m200) & (ma > sh(ma, 3)) & (sh(c, 1) < sh(o, 1)) & up & (c > sh(o, 1)) & (o < sh(c, 1)) & (rng >= 0.8 * a)
            # 20/20 bar: a big bearish bar (range >= 1.5 ATR, closing in its bottom 20%); the Mortgage play = the next bar opens ABOVE its high (shorts trapped)
            bear2020 = (rng >= 1.5 * a) & ((o - c) >= 0.6 * rng) & ((c - l) <= 0.2 * rng)
            m2020 = win & sh(bear2020.astype(float), 1).astype(bool) & (o > sh(h, 1)) & up
            # the plain 20/20 reversal: the bar AFTER a bearish 20/20 closes up through its midpoint
            r2020 = win & sh(bear2020.astype(float), 1).astype(bool) & up & (c > (sh(h, 1) + sh(l, 1)) / 2)
            # Flat 20 range scalp: the 20 SMA is flat (under 0.1 ATR of change in 6 bars); buy a bar below it that closes back up near the range support
            flat20 = win & (np.abs(ma - sh(ma, 6)) <= 0.1 * a) & (np.abs(ma - m200) <= 2.0 * a) & up & (c < ma) & (l <= np.minimum.accumulate(l) + 0.3 * a)
            # Flat 200 reversal: price sharply down to a flat 200 SMA (within 0.3 ATR), rejection bar (closes up in its top half)
            flat200 = win & (np.abs(m200 - sh(m200, 12)) <= 0.1 * a) & (l <= m200 + 0.3 * a) & (c > m200) & up & ((c - l) >= 0.5 * rng)
        return {"velez_bt": bt, "velez_clear": clear, "velez_bull180": b180, "velez_2020": r2020, "velez_mortgage": m2020,
                "velez_flat20": flat20, "velez_flat200": flat200}

    @staticmethod
    def _research_masks(day, t, o, h, l, c, a, ma, m200, env, up, sh) -> dict:
        n = len(day)
        rng = h - l
        with np.errstate(invalid="ignore"):
            # GIFT: an elephant bar within the last 6 bars, then an up bar that pulled back into its lower half without breaking its low
            ele = (rng >= 1.5 * a) & ((c - o) >= 0.7 * rng) & ((h - c) <= 0.2 * rng) & up
            gift = np.zeros(n, bool)
            for i in range(2, n):
                for j in range(max(0, i - 6), i - 1):
                    if ele[j] and env[i] and up[i] and l[i] <= l[j] + 0.5 * rng[j] and l[i] > l[j] and c[i] >= l[j] + 0.5 * rng[j]:
                        gift[i] = True
                        break
            # NRB: the prior bar is the narrowest of the last 7 and under half an ATR; this bar closes up through its high
            r7 = np.full(n, np.nan)
            for i in range(7, n):
                r7[i] = rng[i - 7:i].min()
            nrb = env & up & (sh(rng, 1) <= 0.5 * a) & (sh(rng, 1) <= r7) & (c > sh(h, 1))
            # ORB: the first close above the high of the first 5-minute bar (09:30), between 09:40 and 10:30
            orb = np.zeros(n, bool)
            if t[0] == "09:30":
                inwin = np.asarray((t >= "09:40") & (t <= "10:30"))
                brk = (c > h[0]) & (sh(c, 1) <= h[0]) & up & (a > 0)
                orb = inwin & brk
            # Bear trap: a new 12-bar low is taken out and the bar closes back above it, up (an uptrend: the 20 over the 200)
            low12 = np.full(n, np.nan)
            for i in range(12, n):
                low12[i] = l[i - 12:i].min()
            trap = (ma > m200) & up & (l < low12) & (c > low12) & np.asarray((t >= "09:45") & (t <= "15:15"))
            # Late-day breakout: after 14:30, above the day's open, near the day high, through the prior 12-bar high
            hi12 = np.full(n, np.nan)
            dayhi = np.maximum.accumulate(h)
            for i in range(12, n):
                hi12[i] = h[i - 12:i].max()
            ldb = np.asarray(t >= "14:30") & (c > o[0]) & (c >= sh(dayhi, 1) - 0.3 * a) & (c > hi12) & up & (a > 0)
        return {"velez_gift": gift, "velez_nrb": nrb, "velez_orb": orb, "velez_trap": trap, "velez_ldb": ldb}


class VelezHybrid:
    """Owner 2026-10-21 ("hybrid all Alpaca experts with those rules"): Velez's context rules applied to EVERY expert's signal.
      env        trade WITH the simple averages: longs only while the 20 SMA rises and sits over the 200; shorts only while it falls under it
      look_left  skip a trade when a prior 20-bar high (longs) / low (shorts) sits between the entry and 1.5 x the stop distance (obstruction)
      one_bar    replace the expert's stop with Velez's one-bar stop: beyond the signal bar's extreme + 0.1 ATR (kept 0.5-2 ATR)
      no_chase   skip "wide state" entries: price more than 2 ATR away from the 20 SMA (overextended)."""

    def __init__(self, brain, env: bool = True, look_left: bool = False, one_bar: bool = False, no_chase: bool = False):
        self.brain, self.env, self.look_left, self.one_bar, self.no_chase = brain, env, look_left, one_bar, no_chase

    def __getattr__(self, name):
        return getattr(self.brain, name)

    def signals(self, day):
        sigs = self.brain.signals(day)
        if not sigs or "f_sma20" not in day or "f_sma200" not in day:
            return sigs
        from dataclasses import replace as _rep
        h, l, c = (day[k].to_numpy(float) for k in ("high", "low", "close"))
        ma, m200 = day["f_sma20"].to_numpy(float), day["f_sma200"].to_numpy(float)
        a = day["f_atr"].to_numpy(float) if "f_atr" in day else np.full(len(day), np.nan)
        out = []
        for s in sigs:
            i, side = s.i, (-1 if s.side == -1 else 1)
            if i < 3 or not (a[i] > 0):
                out.append(s)
                continue
            if self.env:
                rising = ma[i] > ma[i - 3]
                ok = (ma[i] > m200[i] and rising) if side == 1 else (ma[i] < m200[i] and not rising)
                if not ok:
                    continue
            if self.no_chase and abs(c[i] - ma[i]) > 2.0 * a[i]:
                continue
            sd = s.stop_dist
            if self.one_bar:
                ext = l[i] if side == 1 else h[i]
                sd = float(np.clip(side * (c[i] - ext) + 0.1 * a[i], 0.5 * a[i], 2.0 * a[i]))
            if self.look_left and i >= 20:
                tgt = c[i] + side * 1.5 * sd
                blk = h[i - 20:i].max() if side == 1 else l[i - 20:i].min()
                if (side == 1 and c[i] < blk < tgt) or (side == -1 and tgt < blk < c[i]):
                    continue
            out.append(_rep(s, stop_dist=sd) if sd != s.stop_dist else s)
        return out


class OnlyTags:
    """Keeps only the signals whose tag is in `tags` (the scalper redesign trades nothing else)."""

    def __init__(self, brain, tags):
        self.brain, self.tags = brain, set(tags)

    def __getattr__(self, name):
        return getattr(self.brain, name)

    def signals(self, day):
        return [s for s in self.brain.signals(day) if (s.tag or "") in self.tags]


class OversoldGate:
    """Owner 2026-10-19: every BUY (long signal, any expert or strategy) must show at least `need` of the five oversold readings
    (confirm.oversold_votes) on its signal bar. Shorts pass untouched. Backtest wrapper; the live bot applies the same rule in paper.py."""

    def __init__(self, brain, need: int = 3):
        self.brain, self.need = brain, need

    def __getattr__(self, name):
        return getattr(self.brain, name)

    def signals(self, day):
        from .confirm import oversold_votes
        return [s for s in self.brain.signals(day) if s.side != 1 or oversold_votes(day, s.i)[0] >= self.need]


def fib_ladder_cfg(cfg: dict, tp1: float, tp2: float, atr: float) -> dict:
    """Per-trade ladder for a Fibonacci box trade: the first take-profit step at TP1 (the zone itself) and the second at TP2, written as
    ATRs beyond TP1 so the live ladder code needs no change."""
    if not cfg or atr <= 0 or len(cfg.get("tp_depth_atr", [])) < 2:
        return cfg
    d = list(cfg["tp_depth_atr"])
    d[0], d[1] = 0.0, abs(tp2 - tp1) / atr
    return {**cfg, "tp_depth_atr": d}


class TrendContinuation:
    """Backtest wrapper for the owner's trend-continuation check (confirm.trend_votes): a TREND expert's signal survives only when at least
    `vote` of the five candle / volume / volatility checks agree on its signal bar. Other experts pass untouched."""

    def __init__(self, brain, vote: float = 0.6, vol_mult: float = 1.2):
        self.brain, self.vote, self.vol_mult = brain, vote, vol_mult

    def __getattr__(self, name):
        return getattr(self.brain, name)

    def signals(self, day):
        from .confirm import trend_votes
        out = []
        for s in self.brain.signals(day):
            if (s.tag or "").removeprefix("S:") in TREND_EXPERTS:
                k, _ = trend_votes(day, s.i, -1 if s.side == -1 else 1, self.vol_mult)
                if k / 5.0 < self.vote - 1e-9:
                    continue
            out.append(s)
        return out


class ReversalConfirm:
    """Backtest wrapper for the owner's mean-reversion fix (confirm.reversal_votes): a RANGE expert's signal survives only when at least
    `need` of the 3 reversal checks agree. Box strategies and trend experts pass untouched."""

    def __init__(self, brain, need: int = 2):
        self.brain, self.need = brain, need

    def __getattr__(self, name):
        return getattr(self.brain, name)

    def signals(self, day):
        from .confirm import reversal_votes
        out = []
        for s in self.brain.signals(day):
            nm = (s.tag or "").removeprefix("S:")
            if nm in RANGE_EXPERTS and nm not in BOX_STRATEGIES:
                k, _ = reversal_votes(day, s.i, -1 if s.side == -1 else 1)
                if k < self.need:
                    continue
            out.append(s)
        return out


class BoxRangeBrain:
    """Owner's range box for stocks (OANDA drawing applied to Alpaca; the paper plan 2026-10-16 reads Weinstein -> trend indicators -> range box).
    Every signal keeps the stage rule (longs in stage 2, shorts in stage 4) and the multi-timeframe check that wraps this brain; the mean-reversion
    experts must in addition pass the box: the stock's daily BOX must be valid (day.attrs["box"] = valid, top, bot) and the entry in the right part
    of it - position "valid": anywhere in the box; "half": a long in the lower half (the buy zone), a short in the upper half; "edge": the outer third."""

    def __init__(self, brain, position: str = "half"):
        self.brain, self.position = brain, position

    def __getattr__(self, name):
        return getattr(self.brain, name)

    def signals(self, day):
        st = int(day.attrs.get("stage", 0))
        valid, top, bot = day.attrs.get("box", (False, 0.0, 0.0))
        frac = {"valid": 1.0, "half": 0.5, "edge": 1.0 / 3.0}[self.position]
        out = []
        for s in self.brain.signals(day):
            if not stage_ok(s, st):
                continue
            if (s.tag or "").removeprefix("S:") in RANGE_EXPERTS:
                if not valid or top <= bot:
                    continue
                pos = (float(day["close"].iloc[s.i]) - bot) / (top - bot)
                if self.position != "valid" and not ((s.side == 1 and pos <= frac) or (s.side == -1 and pos >= 1 - frac)):
                    continue
            out.append(s)
        return out


MTF = {"adx_min": 20.0, "range_adx_max": 25.0}


def tf_confirm_reason(day: pd.DataFrame, s, wbias: int = 0, adx_min: float = MTF["adx_min"], range_adx_max: float = MTF["range_adx_max"]) -> str:
    """'' when the signal's trend is CONFIRMED on several timeframes, else why not (owner, 2026-10-15). Timeframes: weekly (not against the
    trade), 1 hour and 15 minutes (the trade's direction, from the EMA9/EMA21 + close rule), 1-hour strength (ADX >= adx_min). Trend experts
    need all of that. Mean-reversion experts need the opposite: the 1-hour market must NOT be trending (ADX < range_adx_max). Shorts read the
    mirrored columns (m_*), so the same code is the downtrend check."""
    from .experts import RANGE_EXPERTS, TREND_EXPERTS
    name = (s.tag or "").removeprefix("S:")
    pre = "m_" if s.side == -1 else ""
    if wbias and wbias == -s.side:
        return "against the weekly trend"
    col = lambda c: day[pre + c].iloc[s.i] if (pre + c) in day else float("nan")     # noqa: E731
    h1, m15, adx1 = col("f_h1_dir"), col("f_m15_dir"), col("f_h1_adx")
    if name in TREND_EXPERTS:
        if h1 != 1:
            return "1-hour trend not confirmed"
        if m15 != 1:
            return "15-minute trend not confirmed"
        if not (adx1 >= adx_min):
            return f"1-hour strength too weak (ADX {adx1:.0f} < {adx_min:.0f})" if np.isfinite(adx1) else "1-hour strength unknown"
    elif name in RANGE_EXPERTS:
        if np.isfinite(adx1) and adx1 >= range_adx_max:
            return f"1-hour market is trending (ADX {adx1:.0f}), not a range"
    return ""


class TrendConfirm:
    """Backtest wrapper: keep only the signals whose trend is confirmed on weekly / 1-hour / 15-minute (tf_confirm_reason)."""

    def __init__(self, brain, **kw):
        self.brain, self.kw = brain, kw

    def __getattr__(self, name):
        return getattr(self.brain, name)

    def signals(self, day):
        wb = int(day.attrs.get("wbias", 0))
        return [s for s in self.brain.signals(day) if not tf_confirm_reason(day, s, wb, **self.kw)]
