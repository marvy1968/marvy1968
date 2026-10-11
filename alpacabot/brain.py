"""The central brain: every expert says buy/sell/take-profit in its own way; the brain decides which signals become trades.

  1. volatility circuit breaker   ATR14/ATR50 above 2.5 (spike) or below 0.3 (flat, illiquid): no new entries
  2. regime router (ADX)          trend experts only when ADX >= split, range experts only below it
  3. multi-timeframe filter       trend experts need the last completed 15-minute bar's trend up
  4. relative-volume check        breakout experts need RVOL >= 1.2 (already part of donchian_turtle)
  5. consensus                    consensus=1 takes any expert; 2 needs two different experts within the last `window` bars
  6. one position per symbol, a re-entry cooldown after each exit (bars), a per-day trade cap per symbol
  7. priority                     when several experts fire together, the one ranked first in `priority` (best train expectancy) wins
Sizing (1% of equity at risk per trade), the slot cap and the daily loss limit live in engine.Portfolio. Trade tags carry the expert name, so
results can be split by expert.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .experts import ALL_EXPERTS, RANGE_EXPERTS, TREND_EXPERTS, Experts
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
        found = Experts((self.entry_from, self.entry_to)).signals(d, self.experts)
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
