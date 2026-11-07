"""Pre-registered forex refinements (fxbot/RESEARCH_QUEUE.md), one change at a time vs the live rule.

Shared rule (fixed before any result): the last 3 months split in two halves by date; bid/ask; the live exits (0.5R cut, 1.25R take-profit;
exitlab "live"). ADOPT-candidate only if ALL hold:
  - 30+ trades for the variant over the 3 months
  - variant avg R >= live avg R in half 1
  - variant avg R > live avg R in half 2 (unseen)
  - variant PF >= 1.0 over the 3 months with the spread doubled
  - variant avg R (3 months) > the mean avg R of 5 random-timing controls (same signal count, same filter, same exits)
A candidate then needs a 3-year check before going live. Research only: nothing here changes a live strategy."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from alpacabot.exitlab import stats

from . import exitlab as xl


class SessionWindow:
    """Wrap a strategy: entry signals only on bars whose NEXT bar (the fill) opens within [start_h, end_h) UTC. Exits, stops unchanged."""

    def __init__(self, strat, start_h: int = 7, end_h: int = 17):
        self.s, self.start_h, self.end_h = strat, start_h, end_h
        self.max_bars = getattr(strat, "max_bars", 72)

    def __getattr__(self, name):
        return getattr(self.s, name)

    def frame(self, m):
        f = self.s.frame(m).copy()
        h = np.asarray(m.index.hour)
        fill_h = np.r_[h[1:], (h[-1] + 1) % 24] if len(h) else h      # the fill is the next bar's open (weekend gaps included)
        ok = (fill_h >= self.start_h) & (fill_h < self.end_h)
        f["long"] = f["long"].astype(bool) & ok
        f["short"] = f["short"].astype(bool) & ok
        return f


def check(make_live, make_variant, data: dict, today: date, days: int = 92, seeds: int = 5) -> dict:
    a = today - timedelta(days=days)
    mid_ = a + (today - a) / 2
    h2a = mid_ + timedelta(days=1)
    sel = lambda tr, x, y: [t["r"] for t in tr if x <= t["entry"] <= y]  # noqa: E731
    out = {}
    for name, mk in (("live", make_live), ("variant", make_variant)):
        tr = xl.run(mk, data, "live", a, today)
        tr2 = xl.run(mk, data, "live", a, today, spread_mult=2.0)
        out[name] = {"all": stats(sel(tr, a, today)), "half1": stats(sel(tr, a, mid_)), "half2": stats(sel(tr, h2a, today)),
                     "all_2x": stats(sel(tr2, a, today))}
    plc = [stats(sel(xl.run(make_variant, data, "live", a, today, seed=s), a, today))["avg_r"] for s in range(seeds)]
    plc = [p for p in plc if p is not None]
    v, b = out["variant"], out["live"]
    g = lambda d, k: (d.get(k) if d.get(k) is not None else float("-inf"))  # noqa: E731
    checks = {"30+ trades": (v["all"]["trades"] or 0) >= 30,
              "half 1 not worse": g(v["half1"], "avg_r") >= g(b["half1"], "avg_r"),
              "half 2 better": g(v["half2"], "avg_r") > g(b["half2"], "avg_r"),
              "PF >= 1.0 at 2x spread": (v["all_2x"]["pf"] or 0) >= 1.0,
              "beats random timing": g(v["all"], "avg_r") > (float(np.mean(plc)) if plc else float("inf"))}
    out.update({"checks": checks, "random_avg_r": float(np.mean(plc)) if plc else None, "passes": all(checks.values()),
                "window": (a, mid_, h2a, today)})
    return out


def lines(title: str, res: dict) -> list[str]:
    a, m, h2a, t = res["window"]
    f = lambda s: f"{s['trades']} t, PF {(s['pf'] or 0):.2f}, avg R {(s['avg_r'] or 0):+.3f}, net {(s['total_r'] or 0):+.1f}R"  # noqa: E731
    out = [f"### {title} (halves {a}..{m} / {h2a}..{t}, bid/ask, {res.get('exits', 'live exits 0.5R cut / 1.25R target')})"]
    for k in ("live", "variant"):
        r = res[k]
        out.append(f"- {k}: all {f(r['all'])} | half 1 {f(r['half1'])} | half 2 {f(r['half2'])} | 2x spread {f(r['all_2x'])}")
    out.append(f"- random-timing control avg R {res['random_avg_r'] if res['random_avg_r'] is None else round(res['random_avg_r'], 3)}")
    out.append("- checks: " + ", ".join(f"{k} {'OK' if v else 'NO'}" for k, v in res["checks"].items()))
    out.append(f"- PRE-REGISTERED VERDICT: {'PASS (short test) - 3-year check next' if res['passes'] else 'FAIL - live rule stays'}")
    return out


def session_lien(data_h1: dict, today: date) -> list[str]:
    """Queue item 1: lien_h1 only when the fill is in 07:00-17:00 UTC (London + New York) vs all hours."""
    from .strategies import LienFader
    live = lambda: LienFader(break_pips=20)  # noqa: E731
    var = lambda: SessionWindow(LienFader(break_pips=20), 7, 17)  # noqa: E731
    return lines("Queue 1: lien_h1 London + New York hours only", check(live, var, data_h1, today))


class DailyTrend:
    """Wrap a strategy: longs only while the last COMPLETED day's close is above its daily SMA(n), shorts only below. Exits unchanged."""

    def __init__(self, strat, n: int = 200):
        self.s, self.n = strat, n
        self.max_bars = getattr(strat, "max_bars", 72)

    def __getattr__(self, name):
        return getattr(self.s, name)

    def frame(self, m):
        f = self.s.frame(m).copy()
        dc = m["close"].resample("1D").last().dropna()
        sma = dc.rolling(self.n).mean()
        up = (dc > sma).where(sma.notna())
        up = up.shift(1)                                         # yesterday's completed day decides today's bars (no look-ahead)
        day = m.index.floor("1D")
        u = up.reindex(day).to_numpy()
        f["long"] = f["long"].astype(bool) & (u == True)          # noqa: E712  (NaN = not enough history = no trade)
        f["short"] = f["short"].astype(bool) & (u == False)        # noqa: E712
        return f


def trend_lien(data_h1: dict, today: date) -> list[str]:
    """Queue item 2: lien_h1 skips a fade against the daily trend (last completed close vs the daily SMA200)."""
    from .strategies import LienFader
    live = lambda: LienFader(break_pips=20)  # noqa: E731
    var = lambda: DailyTrend(LienFader(break_pips=20), 200)  # noqa: E731
    return lines("Queue 2: lien_h1 only with the daily trend (D1 SMA200)", check(live, var, data_h1, today))


def _random_twin(df, t: dict, rng, a: date, b: date, max_bars: int = 120):
    """A random-timing twin of one playbook trade: same pair, side, risk and reward distance, entered at a random bar in [a, b]."""
    idx = df.index
    ok = np.flatnonzero((idx.date >= a) & (idx.date <= b))
    if len(ok) < max_bars + 2:
        return None
    j = int(rng.choice(ok[:-max_bars - 1]))
    side = 1 if t["side"] == "long" else -1
    risk, rr = float(t["risk"]), float(t.get("rr_target") or 2.0)
    fill = float(df["ao"].iloc[j]) if side == 1 else float(df["bo"].iloc[j])
    stop, tgt = fill - side * risk, fill + side * rr * risk
    lo = (df["bl"] if side == 1 else df["al"]).to_numpy(float)
    hi = (df["bh"] if side == 1 else df["ah"]).to_numpy(float)
    cl = (df["bc"] if side == 1 else df["ac"]).to_numpy(float)
    for k in range(j, min(j + max_bars, len(df))):
        adverse, favour = (lo[k], hi[k]) if side == 1 else (hi[k], lo[k])
        if side * (adverse - stop) <= 0:
            return -1.0
        if side * (favour - tgt) >= 0:
            return rr
    return side * (cl[min(j + max_bars, len(df)) - 1] - fill) / risk


def retest_check(data_h1: dict, today: date, base_kw: dict, var_kw: dict, title: str, days: int = 92, seeds: int = 5,
                 var_filter=None) -> list[str]:
    """retest_h1 (the playbook's own exits) live rules vs one changed rule, same pre-registered checks as check().
    var_filter(inst, df, trade) -> bool: an entry filter for the variant, using only data before the entry. (Approximation: a filtered-out
    trade still blocked overlapping setups on that pair, as in the live simulation's one-position-per-pair rule.)"""
    from .playbook import PlaybookRules, simulate_playbook
    a = today - timedelta(days=days)
    mid_ = a + (today - a) / 2
    h2a = mid_ + timedelta(days=1)

    def run(kw, mult=1.0, flt=None):
        out = []
        for inst, df in data_h1.items():
            d = xl._spread(df, mult)
            out += [{**t, "inst": inst} for t in simulate_playbook(inst, d, PlaybookRules(**kw), a, today) if flt is None or flt(inst, df, t)]
        return out
    sel = lambda tr, x, y: [t["r"] for t in tr if x <= t["entry"].date() <= y]  # noqa: E731
    res = {}
    for name, kw in (("live", base_kw), ("variant", var_kw)):
        flt = var_filter if name == "variant" else None
        tr, tr2 = run(kw, 1.0, flt), run(kw, 2.0, flt)
        res[name] = {"all": stats(sel(tr, a, today)), "half1": stats(sel(tr, a, mid_)), "half2": stats(sel(tr, h2a, today)),
                     "all_2x": stats(sel(tr2, a, today)), "_tr": tr}
    rng = np.random.default_rng(0)
    plc = []
    for s in range(seeds):
        tw = [_random_twin(data_h1[t["inst"]], t, rng, a, today) for t in res["variant"]["_tr"]]
        tw = [x for x in tw if x is not None]
        if tw:
            plc.append(float(np.mean(tw)))
    for k in ("live", "variant"):
        res[k].pop("_tr")
    v, b = res["variant"], res["live"]
    g = lambda d, k: (d.get(k) if d.get(k) is not None else float("-inf"))  # noqa: E731
    checks = {"30+ trades": (v["all"]["trades"] or 0) >= 30,
              "half 1 not worse": g(v["half1"], "avg_r") >= g(b["half1"], "avg_r"),
              "half 2 better": g(v["half2"], "avg_r") > g(b["half2"], "avg_r"),
              "PF >= 1.0 at 2x spread": (v["all_2x"]["pf"] or 0) >= 1.0,
              "beats random timing": g(v["all"], "avg_r") > (float(np.mean(plc)) if plc else float("inf"))}
    res.update({"checks": checks, "random_avg_r": float(np.mean(plc)) if plc else None, "passes": all(checks.values()),
                "window": (a, mid_, h2a, today), "exits": "the playbook's own exits (structure stop, swing target, close-back)"})
    return lines(title, res)


def retest_rr(data_h1: dict, today: date) -> list[str]:
    """Queue item 3: retest_h1 needs 2.5R to the next opposing swing level instead of 2R."""
    return retest_check(data_h1, today, {"be_at_r": 0.0}, {"be_at_r": 0.0, "min_rr": 2.5}, "Queue 3: retest_h1 minimum reward 2.5R vs 2R")


def h4_trend_ok(inst, df, t, n: int = 50, slope_bars: int = 5) -> bool:
    """The last COMPLETED 4-hour bar before the entry: SMA(n) of 4-hour closes rising over slope_bars bars for a long, falling for a short."""
    from .data import mid
    c = mid(df)["close"].resample("4h", label="left", closed="left").last().dropna()
    sma = c.rolling(n).mean()
    e = pd.Timestamp(t["entry"])
    done = sma[sma.index + pd.Timedelta(hours=4) <= e].dropna()
    if len(done) <= slope_bars:
        return False
    up = float(done.iloc[-1]) > float(done.iloc[-1 - slope_bars])
    return up if t["side"] == "long" else not up


def retest_h4(data_h1: dict, today: date) -> list[str]:
    """Queue item 4: retest_h1 entries only when the 4-hour SMA50 slopes the trade's way."""
    return retest_check(data_h1, today, {"be_at_r": 0.0}, {"be_at_r": 0.0}, "Queue 4: retest_h1 with the 4-hour SMA50 slope",
                        var_filter=h4_trend_ok)
