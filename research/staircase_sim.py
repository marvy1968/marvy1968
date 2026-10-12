"""Staircase exit vs other exits on synthetic 5-minute markets with KNOWN behaviour, using the bot's own engine and ExitState."""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from alpacabot.engine import Costs, simulate_day
from alpacabot.indicators import adx, atr
from alpacabot.strategies import Signal

NY = "America/New_York"


def market(kind, days, seed):
    rng = np.random.default_rng(seed)
    out = []
    px = 100.0
    for d in pd.bdate_range("2020-01-02", periods=days):
        idx = pd.date_range(f"{d.date()} 09:30", f"{d.date()} 15:45", freq="5min", tz=NY)
        n = len(idx)
        e = rng.normal(0, 0.0015, n)
        r = np.zeros(n)
        for i in range(n):
            if kind == "random":
                r[i] = e[i]
            elif kind == "trending":            # positive autocorrelation: moves persist
                r[i] = e[i] + (0.35 * r[i - 1] if i else 0)
            else:                                 # mean-reverting: moves fade
                r[i] = e[i] - (0.35 * r[i - 1] if i else 0)
        c = px * np.exp(np.cumsum(r))
        o = np.r_[px, c[:-1]]
        w = c * 0.0008 * rng.random(n)
        df = pd.DataFrame({"open": o, "high": np.maximum(o, c) + w, "low": np.minimum(o, c) - w, "close": c, "volume": 1.0}, index=idx)
        px = c[-1]
        out.append(df)
    full = pd.concat(out)
    full["atr"], full["adx"] = atr(full, 14), adx(full, 14)
    return [g for _, g in full.groupby(full.index.date)][3:]


EXITS = {
    "staircase (bot)":        lambda a, x: Signal(0, 1, 1.5 * a, 0.0, 0, "", True, a, x, 1.5, 1.0),
    "fixed 1.5ATR stop / 2R": lambda a, x: Signal(0, 1, 1.5 * a, 2.0, 0, ""),
    "fixed stop / 1R":        lambda a, x: Signal(0, 1, 1.5 * a, 1.0, 0, ""),
    "stop only, hold to 3:50": lambda a, x: Signal(0, 1, 1.5 * a, 0.0, 0, ""),
}


def run(kind, seeds=(7, 8, 9, 10), days=200, per_day=4):
    worlds = []
    for seed in seeds:
        frames = market(kind, days, seed)
        rng = np.random.default_rng(seed + 1)
        worlds.append((frames, [(k, sorted(rng.choice(np.arange(6, len(f) - 6), per_day, replace=False))) for k, f in enumerate(frames)]))
    rows = {}
    for name, mk in EXITS.items():
        rs, rs_cost = [], []
        for frames, picks in worlds:
          for k, idxs in picks:
            f = frames[k]
            for n_, i in enumerate(idxs):
                side = 1 if (k + n_) % 2 == 0 else -1
                a, x = float(f["atr"].iloc[i]), float(f["adx"].iloc[i])
                if not np.isfinite(a) or not np.isfinite(x):
                    continue
                s = mk(a, x)
                s = Signal(int(i), side, s.stop_dist, s.target_r, s.max_bars, "", s.managed, s.atr, s.adx, s.trail, s.tp_scale)
                for costs, bucket in ((Costs(0, 0), rs), (Costs(2.0, 0.4), rs_cost)):
                    t = simulate_day("X", f, [s], costs, max_trades=1)
                    if t:
                        bucket.append(t[0].pnl_per_share(costs) / t[0].stop_dist)
        rs, rc = np.array(rs), np.array(rs_cost)
        rows[name] = {"trades": len(rs), "win %": round(100 * (rs > 0).mean(), 1), "avg win R": round(rs[rs > 0].mean(), 2),
                      "avg loss R": round(rs[rs <= 0].mean(), 2), "avg R (no costs)": round(rs.mean(), 3), "+/- 2SE": round(2 * rs.std() / np.sqrt(len(rs)), 3), "avg R (2bp+fees)": round(rc.mean(), 3),
                      "R std": round(rs.std(), 2)}
    return pd.DataFrame(rows).T


pd.set_option("display.width", 200)
for kind in ("random", "trending", "mean_reverting"):
    print(f"\n=== {kind} market, random entries, half long / half short, 4 price paths ===")
    print(run(kind).to_string())
