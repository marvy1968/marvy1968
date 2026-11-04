"""Loser autopsy for boxmr_h1 (owner 2026-11-04, from Muse's ranked list: "take all the losing range trades and ask what they had in common").

PRE-REGISTERED (fixed before any result was seen):
  Trades: the live boxmr_h1 expert exactly as built in fxbot.live.STRATS, its own exits (engine.simulate, bid/ask, stop first), 7 majors,
      H1, normal spread. Discovery half 2016-01-01..2020-12-31, confirmation half 2021-01-01..the last full day.
  Traits measured at the signal bar (nothing after it):
      session      New York hour of entry: asia 19-02, london 03-07, overlap 08-11, ny 12-16, roll 17-18
      weekday      Mon..Fri (New York trading day)
      atr_pct      14-bar ATR / price, terciles (cut points from the discovery half)
      box_atr      the 100-bar box height / ATR (as InBox draws it), terciles
      edge         how far into the box from the faded edge (0 = at the edge, 0.5 = the middle), terciles
      spread_atr   bid/ask spread at entry / ATR, terciles
  Rule: in the discovery half pick the ONE bucket (20+ trades) with the lowest profit factor. In the confirmation half drop that bucket.
  PASS only if that removes >= 20% of the losing trades AND a smaller share of the winning trades (Muse's rule). One bucket, one try.
  Diagnostics on the whole period: the fade (every boxmr_h1 signal traded the other way, same stop / target) and the spread at 2x.
Research only: nothing here changes a live strategy."""

from datetime import date

import numpy as np
import pandas as pd

from .data import mid
from .engine import simulate

TRAITS = ("session", "weekday", "atr_pct", "box_atr", "edge", "spread_atr")
DISC = (date(2016, 1, 1), date(2020, 12, 31))


def _session(h: int) -> str:
    if h >= 19 or h <= 2:
        return "asia"
    if h <= 7:
        return "london"
    if h <= 11:
        return "overlap"
    if h <= 16:
        return "ny"
    return "roll"


def trades_with_traits(make, data: dict, a: date, b: date, spread_mult: float = 1.0) -> pd.DataFrame:
    rows = []
    for inst, df in data.items():
        m = mid(df)
        c = m["close"]
        pc = c.shift(1)
        tr = pd.concat([m["high"] - m["low"], (m["high"] - pc).abs(), (m["low"] - pc).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        top, bot = m["high"].shift(1).rolling(100).max(), m["low"].shift(1).rolling(100).min()
        spr = df["ac"] - df["bc"]
        pos = {t: i for i, t in enumerate(df.index)}
        for t in simulate(inst, df, make(), spread_mult):
            if not (a <= t.entry_time.date() <= b):
                continue
            j = pos.get(t.entry_time)
            if j is None or j < 1:
                continue
            i = j - 1                                                  # the signal bar
            a_, h_ = float(atr.iat[i]), float(top.iat[i] - bot.iat[i])
            if not (a_ > 0 and h_ > 0):
                continue
            p = (float(c.iat[i]) - float(bot.iat[i])) / h_
            ny = t.entry_time.tz_convert("America/New_York")
            rows.append({"inst": inst, "entry": t.entry_time, "date": t.entry_time.date(), "side": t.side,
                         "r": t.side * (t.exit_px - t.entry_px) / t.stop_dist,
                         "session": _session(ny.hour), "weekday": (ny - pd.Timedelta(hours=17)).day_name()[:3],
                         "atr_pct": a_ / float(c.iat[i]), "box_atr": h_ / a_, "edge": p if t.side == 1 else 1 - p,
                         "spread_atr": float(spr.iat[i]) / a_})
    return pd.DataFrame(rows)


def _pf(r) -> float:
    r = np.asarray(r, float)
    w, lo = r[r > 0].sum(), -r[r < 0].sum()
    return float(w / lo) if lo > 0 else float("inf")


def bucketize(df: pd.DataFrame, cuts: dict) -> pd.DataFrame:
    out = df.copy()
    for k in ("atr_pct", "box_atr", "edge", "spread_atr"):
        q1, q2 = cuts[k]
        out[k] = np.where(df[k] <= q1, "low", np.where(df[k] <= q2, "mid", "high"))
    return out


def autopsy(tr: pd.DataFrame, today: date) -> list[str]:
    if tr.empty:
        return ["- no trades"]
    d1 = tr[(tr["date"] >= DISC[0]) & (tr["date"] <= DISC[1])]
    d2 = tr[tr["date"] > DISC[1]]
    cuts = {k: (float(d1[k].quantile(1 / 3)), float(d1[k].quantile(2 / 3))) for k in ("atr_pct", "box_atr", "edge", "spread_atr")}
    b1, b2 = bucketize(d1, cuts), bucketize(d2, cuts)
    out = [f"- trades: discovery {len(d1)} (PF {_pf(d1['r']):.2f}, {d1['r'].sum():+.1f}R), confirmation {len(d2)} "
           f"(PF {_pf(d2['r']):.2f}, {d2['r'].sum():+.1f}R); losers {int((d1['r'] < 0).sum())} / {int((d2['r'] < 0).sum())}",
           "| Trait | Bucket | Discovery n / PF / net R | Confirmation n / PF / net R |", "|---|---|---|---|"]
    cand = []
    for k in TRAITS:
        for v in sorted(set(b1[k]) | set(b2[k])):
            x1, x2 = b1[b1[k] == v]["r"], b2[b2[k] == v]["r"]
            out.append(f"| {k} | {v} | {len(x1)} / {_pf(x1):.2f} / {x1.sum():+.1f} | {len(x2)} / {_pf(x2):.2f} / {x2.sum():+.1f} |")
            if len(x1) >= 20:
                cand.append((_pf(x1), k, v))
    if not cand:
        return out + ["- no bucket with 20+ discovery trades"]
    pf_, k, v = min(cand)
    out.append(f"- discovery pick (lowest PF, 20+ trades): {k} = {v} (PF {pf_:.2f})")
    drop = b2[k] == v
    los, win = b2["r"] < 0, b2["r"] > 0
    lc = float((drop & los).sum()) / max(1, int(los.sum()))
    wc = float((drop & win).sum()) / max(1, int(win.sum()))
    kept = b2[~drop]["r"]
    ok = lc >= 0.20 and wc < lc
    out.append(f"- confirmation half without {k} = {v}: losers cut {lc:.0%}, winners cut {wc:.0%}; PF {_pf(b2['r']):.2f} -> {_pf(kept):.2f}, "
               f"net {b2['r'].sum():+.1f}R -> {kept.sum():+.1f}R, trades {len(b2)} -> {len(kept)}  => {'PASS' if ok else 'FAIL'}")
    return out


def run(data: dict, today: date) -> list[str]:
    from . import live as fl
    from .strategies import Reversed
    make = fl.STRATS["boxmr_h1"]["make"]
    a = DISC[0]
    tr = trades_with_traits(make, data, a, today)
    lines = ["## boxmr_h1 loser autopsy (pre-registered: discovery 2016-20 picks one bucket, 2021-today confirms; PASS = >= 20% of losers "
             "cut and a smaller share of winners)"] + autopsy(tr, today)
    tr2 = trades_with_traits(make, data, a, today, spread_mult=2.0)
    fade = trades_with_traits(lambda: Reversed(make()), data, a, today)
    for lab, x in (("boxmr_h1 at 2x spread", tr2), ("FADE (every signal reversed, same stop / target)", fade)):
        if len(x):
            out1 = x[x["date"] <= DISC[1]]["r"]
            out2 = x[x["date"] > DISC[1]]["r"]
            lines.append(f"- {lab}: {len(x)} trades, PF {_pf(x['r']):.2f}, {x['r'].sum():+.1f}R | 2016-20 PF {_pf(out1):.2f} | "
                         f"2021-today PF {_pf(out2):.2f}")
    return lines
