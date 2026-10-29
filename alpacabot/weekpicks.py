"""Weekly "beat SPY" picks (owner 2026-10-29: "set up the weekly picks log only", then "add the new feature and let it trade"): the
swing runner (SwingRunner.weekpicks_plan, zones.json swing.weekpicks) places PAPER orders for each new batch; this module picks and grades.

Every 5th session, while SPY is above its 200-day, the forecast v2 relative model (alpacabot.forecast2) picks the 5 stocks with the highest
predicted next-5-day return vs SPY. Each pick is graded with the tested rule: bought at the next open, stop = the 10-day low, moved to entry +
10 bps once price covers half the way to the target, target = entry + 1.5 sigma5, out at the 5th close, 10 bps a side. Every batch is also
graded against 5 random stocks on the same day (mean of 20 seeds) and SPY with the same exits. Runs after the swing scan (16:20 ET) and by
hand (`alpaca weekpicks run|status`). Promote to paper orders only after 8+ graded batches that beat both controls.
"""

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from . import forecast as v1
from . import forecast2 as f2

STATE = Path("state/alpaca_weekpicks")
EVERY = 5


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"batches": []}


def run(daily_raw: dict, sector_of: dict, today: date, state_dir: Path = STATE, seeds: int = 20) -> list[str]:
    daily = {s: v1._dates(d) for s, d in daily_raw.items() if len(d)}
    daily = {s: d[d.index < pd.Timestamp(today)] for s, d in daily.items()}
    p = f2.panel2(daily, sector_of)
    arr = f2._arrays(p)
    days = list(pd.DatetimeIndex(np.sort(p.index.unique())))
    last = days[-1]
    sc = daily["SPY"]["close"]
    above = bool(sc.iloc[-1] > sc.rolling(200).mean().iloc[-1])
    path = Path(state_dir) / "log.json"
    st = _load(path)
    out = []
    # grade batches whose 5 sessions are complete
    for b in st["batches"]:
        if b.get("graded"):
            continue
        t = pd.Timestamp(b["made"])
        if t not in days or days.index(t) + 5 > len(days) - 1:
            continue
        g = p[p.index == t]
        sig = {r.symbol: r.vol * r.close * np.sqrt(5) for r in g.itertuples()}
        for pk in b["picks"]:
            pk["r"] = f2.week_trade(arr, pk["symbol"], t, sig[pk["symbol"]]) if pk["symbol"] in sig else None
        names = [s for s in g["symbol"] if g[g["symbol"] == s]["sector"].iloc[0] != ""]
        ctrl = []
        for k in range(seeds):
            rs = [f2.week_trade(arr, s, t, sig[s]) for s in np.random.default_rng(k).choice(names, size=min(len(b["picks"]), len(names)), replace=False)]
            ctrl.append(float(np.nansum([x for x in rs if x is not None])))
        spy = f2.week_trade(arr, "SPY", t, sig["SPY"]) if "SPY" in sig else None
        b.update({"graded": str(last.date()), "net": float(np.nansum([pk["r"] for pk in b["picks"] if pk["r"] is not None])),
                  "random_median": float(np.median(ctrl)), "spy": None if spy is None else spy * len(b["picks"])})
        out.append(f"graded batch {b['made']}: {b['net']:+.2f}R (" + ", ".join(f"{pk['symbol']} {pk['r']:+.2f}" if pk["r"] is not None else f"{pk['symbol']} -"
                                                                     for pk in b["picks"]) + f") | random median {b['random_median']:+.2f}R | SPY {b['spy'] if b['spy'] is None else round(b['spy'], 2)}R")
    # a new batch every 5th session
    made = [pd.Timestamp(b["made"]) for b in st["batches"]]
    due = not made or (made[-1] in days and len(days) - 1 - days.index(made[-1]) >= EVERY) or made[-1] not in days
    if due and any(pd.Timestamp(b["made"]) == last for b in st["batches"]):
        due = False
    if due:
        if not above:
            st["batches"].append({"made": str(last.date()), "picks": [], "note": "SPY below its 200-day: no picks", "graded": str(last.date()), "net": 0.0,
                                  "random_median": 0.0, "spy": 0.0})
            out.append(f"{last.date()}: SPY below its 200-day -> no picks this week")
        else:
            fr = f2._fit_rel(p[p.index <= days[-6]])
            z = p[(p.index == last) & (p["sector"] != "")].dropna(subset=f2.FEATS2).copy()
            z["rel"] = fr(z)
            top = z.sort_values("rel").tail(f2.TOP_A).iloc[::-1]
            picks = []
            for r in top.itertuples():
                pos, o, h, l, c = arr[r.symbol]
                i = pos[last]
                stop = float(l[max(0, i - 9):i + 1].min())
                picks.append({"symbol": r.symbol, "rel": round(float(r.rel), 3), "close": round(float(r.close), 2), "stop_10d_low": round(stop, 2),
                              "tp_est": round(float(r.close + 1.5 * r.vol * r.close * np.sqrt(5)), 2)})
            st["batches"].append({"made": str(last.date()), "picks": picks})
            out.append(f"NEW weekly picks from the {last.date()} close (log only, buy at the next open in the record): "
                       + "; ".join(f"{x['symbol']} (vs SPY {x['rel']:+.2f}, stop {x['stop_10d_low']}, tp ~{x['tp_est']})" for x in picks))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(st, indent=1, default=str))
    return out + status(st)


def status(st: dict) -> list[str]:
    g = [b for b in st["batches"] if b.get("graded") and b.get("picks")]
    rs = [pk["r"] for b in g for pk in b["picks"] if pk.get("r") is not None]
    s = v1.summary(rs)
    beat_r = sum(b["net"] > b["random_median"] for b in g)
    beat_s = sum(b["spy"] is not None and b["net"] > b["spy"] for b in g)
    open_ = [b for b in st["batches"] if not b.get("graded")]
    return [f"WEEKLY PICKS (log only): {len(g)} graded batches, picks {v1.fmt(s)} | batches beating random {beat_r}/{len(g)}, beating SPY {beat_s}/{len(g)} | "
            f"open: " + (", ".join(f"{b['made']} " + " ".join(pk['symbol'] for pk in b['picks']) for b in open_) or "-")]
