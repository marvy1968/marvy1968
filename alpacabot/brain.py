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

from .experts import ALL_EXPERTS, RANGE_EXPERTS, TREND_EXPERTS, Experts, short_view
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
        return name in RANGE_EXPERTS

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
        return [s for s in self.brain.signals(day) if (s.side == 1 and st == 2) or (s.side == -1 and st == 4)]


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
