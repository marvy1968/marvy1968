"""Exit-a-little-before-the-target test (owner 2026-11-03: "never set your target at the absolute high or low ... exit a few pips earlier").

PRE-REGISTERED (written before any result was seen; nothing else is tried):
  Velez sleeve (alpacabot.velez backtest, the live rules): first target 2R (current) vs 1.9R vs 1.8R with the second at 3R; second target 3R
      (current) vs 2.8R with the first at 2R.
  VWAP reclaim (alpacabot.vwapday quick test, spec defaults, both sides): half off at 1R (current) vs 0.9R.
  Swing weekly picks (alpacabot.forecast2 walk-forward trade rule, the one the live weekpicks OCO uses): target = entry + 1.5 sigma5 (current)
      vs the same target 0.5% lower. The break-even move stays keyed to the original target, so only the exit level changes.
  Same days and costs for every variant. Each test period is split in two halves by date (the same split for every variant of a strategy).
  ADOPT a variant only if its average R per trade is higher than the current rule's in the 2nd (later, unseen) half AND not lower in the
  1st half AND its PF with costs doubled is not lower over the whole period. Otherwise the current rule stays.
Top-Down's zone targets are not covered (their backtest is a separate engine); the live sleeves are untouched by this module.
"""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import vwapday as vd


def _st(rs) -> dict:
    rs = [float(x) for x in rs]
    n = len(rs)
    if not n:
        return {"n": 0, "pf": float("nan"), "exp": float("nan"), "net": 0.0, "win": float("nan")}
    w = sum(x for x in rs if x > 0)
    lo = -sum(x for x in rs if x < 0)
    return {"n": n, "pf": w / lo if lo > 0 else float("inf"), "exp": sum(rs) / n, "net": sum(rs), "win": sum(x > 0 for x in rs) / n}


def _fmt(x: dict) -> str:
    return f"{x['n']} | {x['pf']:.2f} | {x['exp']:+.3f} | {x['net']:+.1f}" if x["n"] else "0 | - | - | -"


def table(title: str, variants: dict, base: str, split) -> list[str]:
    """variants: name -> (rows [(date, R)], rows at 2x costs). Returns markdown lines with the adopt verdict per variant."""
    out = [f"### {title} (halves split at {split})",
           "| Variant | All: n / PF / avg R / net R | 1st half: n / PF / avg R / net R | 2nd half: n / PF / avg R / net R | PF @2x costs | Win% | Adopt? |",
           "|" + "---|" * 7]
    def parts(rows):
        a = _st([r for _, r in rows])
        h1 = _st([r for d, r in rows if d < split])
        h2 = _st([r for d, r in rows if d >= split])
        return a, h1, h2
    b_all, b1, b2 = parts(variants[base][0])
    b2x = _st([r for _, r in variants[base][1]])
    for name, (rows, rows2) in variants.items():
        a, h1, h2 = parts(rows)
        x2 = _st([r for _, r in rows2])
        if name == base:
            verdict = "current rule"
        else:
            ok = (h2["n"] > 0 and b2["n"] > 0 and h2["exp"] > b2["exp"] and h1["exp"] >= b1["exp"] and x2["pf"] >= b2x["pf"])
            verdict = "YES" if ok else "no"
        out.append(f"| {name} | {_fmt(a)} | {_fmt(h1)} | {_fmt(h2)} | {x2['pf']:.2f} | {a['win']:.0%} | {verdict} |")
    return out


def velez_part(load, today: date, out_root: Path, log=print) -> list[str]:
    from . import velez as ve
    combos = {"t1 2.0R / t2 3.0R (current)": (2.0, 3.0), "t1 1.9R / t2 3.0R": (1.9, 3.0), "t1 1.8R / t2 3.0R": (1.8, 3.0),
              "t1 2.0R / t2 2.8R": (2.0, 2.8)}
    res, keep = {}, dict(ve.TGT)
    try:
        for name, (a, b) in combos.items():
            ve.TGT.update(t1=a, t2=b)
            rows = {}
            for mult in (1.0, 2.0):
                sub = out_root / f"velez_{a}_{b}_{mult}"
                sub.mkdir(parents=True, exist_ok=True)
                _velez_once(ve, load, today, sub, mult, log)
                tr = pd.read_csv(next(sub.glob("velez-*/trades.csv")))
                rows[mult] = [(pd.Timestamp(d).date(), float(r) * float(w)) for d, r, w in zip(tr["date"], tr["R"], tr["w"])]
            res[name] = (rows[1.0], rows[2.0])
            log(f"velez {name}: {len(rows[1.0])} trades")
    finally:
        ve.TGT.clear()
        ve.TGT.update(keep)
    days = sorted({d for d, _ in res["t1 2.0R / t2 3.0R (current)"][0]})
    split = days[len(days) // 2] if days else date.today()
    return table("Velez sleeve (55 stocks, last ~4 months, long, R weighted 1 trend / 0.5 exhaustion as live)", res,
                 "t1 2.0R / t2 3.0R (current)", split)


def _velez_once(ve, load, today, sub, mult, log):
    """velez.backtest at 1x or 2x costs only (no controls): the 2x run patches the cost constant."""
    keep = vd.CB
    try:
        vd.CB = keep * mult
        ve.backtest(load, today, sub, log=lambda *_: None, seeds=0)
    finally:
        vd.CB = keep


def reclaim_part(load, today: date, out_root: Path, log=print) -> list[str]:
    res, keep = {}, vd.RECLAIM_TP
    try:
        for name, tp in (("half at 1.0R (current)", 1.0), ("half at 0.9R", 0.9)):
            vd.RECLAIM_TP = tp
            rows = {}
            keep_cb = vd.CB
            for mult in (1.0, 2.0):
                try:
                    vd.CB = keep_cb * mult
                    nm = f"tp_reclaim_{tp}_{mult}"
                    vd.quick(load, today, out_root, sessions=None, days_back=150, seeds=0, name=nm, strats=("reclaim",), log=lambda *_: None)
                finally:
                    vd.CB = keep_cb
                tr = pd.read_csv(out_root / f"{nm}-{today:%Y%m%d}" / "trades.csv")
                rows[mult] = [(pd.Timestamp(d).date(), float(r)) for d, r in zip(tr["date"], tr["R"])]
            rows, rows2 = rows[1.0], rows[2.0]
            res[name] = (rows, rows2)
            log(f"reclaim {name}: {len(rows)} trades")
    finally:
        vd.RECLAIM_TP = keep
    days = sorted({d for d, _ in res["half at 1.0R (current)"][0]})
    split = days[len(days) // 2] if days else date.today()
    return table("VWAP reclaim (in-play names, both sides, every session of the last ~5 months, spec defaults)", res, "half at 1.0R (current)",
                 split)


def swing_part(daily_raw: dict, sector_of: dict, today: date, log=print) -> list[str]:
    from . import forecast as v1
    from . import forecast2 as f2
    daily = {s: v1._dates(d) for s, d in daily_raw.items() if len(d)}
    daily = {s: d[d.index < pd.Timestamp(today)] for s, d in daily.items()}
    p = f2.panel2(daily, sector_of)
    sc = daily["SPY"]["close"]
    spy200 = ((sc > sc.rolling(200).mean()).astype(int)).to_dict()
    wf = f2.walk(p)
    arr = f2._arrays(p)
    days = pd.DatetimeIndex(np.sort(wf.index.unique()))[::5]
    res = {}
    for name, shave in (("target 1.5 sigma5 (current)", 0.0), ("target 0.5% lower", 0.005)):
        rows, rows2 = [], []
        for t in days:
            if spy200.get(t, 0) <= 0:
                continue
            g = wf.loc[[t]]
            g = g[g["sector"] != ""]
            for r in g.sort_values("rel").tail(f2.TOP_A).itertuples():
                for cost, dst in ((f2.COST, rows), (2 * f2.COST, rows2)):
                    x = week_trade_tp(arr, r.symbol, t, r.vol * r.close * np.sqrt(5), shave, cost)
                    if x is not None:
                        dst.append((pd.Timestamp(t).date(), x))
        res[name] = (rows, rows2)
        log(f"swing {name}: {len(rows)} trades")
    d0 = sorted({d for d, _ in res["target 1.5 sigma5 (current)"][0]})
    split = d0[len(d0) // 2] if d0 else today
    return table("Swing weekly picks (forecast v2 walk-forward, last 252 sessions, top 5 every 5th session while SPY > 200-day)", res,
                 "target 1.5 sigma5 (current)", split)


def week_trade_tp(arr, s, t, sig5, shave: float, cost: float) -> float | None:
    """forecast2.week_trade with the exit target `shave` lower (the break-even trigger stays on the original target)."""
    pos, o, h, l, c = arr[s]
    i = pos.get(t)
    if i is None or i + 5 >= len(o) or i < 10:
        return None
    entry = o[i + 1] * (1 + cost)
    stop = l[i - 9:i + 1].min()
    if stop >= entry:
        return None
    risk, tgt0 = entry - stop, entry + 1.5 * sig5
    tgt = tgt0 * (1 - shave)
    if tgt <= entry:
        tgt = tgt0
    be = False
    for k in range(i + 1, i + 6):
        if l[k] <= stop:
            px = o[k] if k > i + 1 and o[k] <= stop else stop
            return (px * (1 - cost) - entry) / risk
        if h[k] >= tgt:
            px = o[k] if k > i + 1 and o[k] >= tgt else tgt
            return (px * (1 - cost) - entry) / risk
        if not be and h[k] >= entry + 0.5 * (tgt0 - entry):
            stop, be = entry * (1 + cost), True
    return (c[i + 5] * (1 - cost) - entry) / risk


def run(load, today: date, out_root: Path, log=print) -> list[str]:
    from .universe import SECTOR_ETFS, SECTOR_OF, STOCKS
    out_root = Path(out_root) / f"tpearly-{today:%Y%m%d}"
    out_root.mkdir(parents=True, exist_ok=True)
    lines = ["## Exit a little before the target - pre-registered test (alpacabot/tpearly.py). Adopt only if avg R is higher in the 2nd half, "
             "not lower in the 1st, and PF at 2x costs is not lower. Costs included in every number."]
    for part in (lambda: velez_part(load, today, out_root, log), lambda: reclaim_part(load, today, out_root, log)):
        try:
            lines += part()
        except Exception as exc:
            lines.append(f"- part failed: {exc!r}")
    try:
        dd = {}
        for s in sorted(set(STOCKS) | set(SECTOR_ETFS) | {"SPY", "QQQ"}):
            try:
                dd[s] = load(s, "1Day", today - timedelta(days=1700), today)
            except Exception as exc:
                log(f"skip {s}: {exc}")
        lines += swing_part(dd, SECTOR_OF, today, log)
    except Exception as exc:
        lines.append(f"- swing part failed: {exc!r}")
    (out_root / "summary.md").write_text("\n".join(lines) + "\n")
    return lines
