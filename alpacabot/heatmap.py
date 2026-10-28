"""Sector heat-map long / short sleeve (owner 2026-10-28: "let it use news, heat maps if possible and short also ... both").

PRE-DECLARED before any run:
- Heat map: the 11 sector SPDR ETFs ranked by their last 21 sessions' return (closes up to the day before the rebalance).
- Every week (first session, at the open): LONG the strongest stock (21-session return minus its sector ETF's) in each of the 3 hottest
  sectors, SHORT the weakest stock in each of the 3 coldest; 6 equal slots of the sleeve (gross 100%, net 0), held to the next week's
  open. A stock held two weeks running pays no new cost. Universe: universe.SECTOR_TOP5 (55 stocks: today's leaders, so a winners'
  list for past years) and, as the hindsight-free check, universe.CONTROL_SECTOR_OF (2016's largest companies).
- Costs: 5 bps a side (10 bps as the stress), short borrow 0.5% a year.
- News variant: longs only where the sector's news (alpacabot.news, last 5 sessions) is NOT negative, shorts only where it IS; a dropped
  slot sits in cash. Read on the last 24 months (Alpaca's news archive is fetched month by month).
- Control: the same stock rule on a random sector ranking each week, 20 seeds.
- Pass: 2017-01-01..today at 10 bps: net return > 0, Sharpe >= 0.5, beats 18 of 20 random rankings, and the control universe is also
  positive. The news variant must also beat the plain sleeve over its 24 months. The last 2 weeks and 3 months are shown for context.
- If it passes: paper sleeve at 5% of the account's equity (owner's cap)."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

LOOK, TOP, COST_BPS, BORROW = 21, 3, 5.0, 0.005


def frames(daily: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(closes, opens) on one date index (naive dates) from {symbol: daily bars}."""
    def norm(s):
        s = s.astype(float).copy()
        s.index = pd.DatetimeIndex([pd.Timestamp(x).tz_convert("America/New_York").normalize().tz_localize(None) if pd.Timestamp(x).tzinfo
                                    else pd.Timestamp(x).normalize() for x in s.index])
        return s[~s.index.duplicated(keep="last")]
    c = pd.DataFrame({k: norm(d["close"]) for k, d in daily.items()}).sort_index()
    o = pd.DataFrame({k: norm(d["open"]) for k, d in daily.items()}).sort_index()
    return c, o


def rebalance_rows(index: pd.DatetimeIndex) -> list[int]:
    wk = index.to_period("W")
    return [i for i in range(1, len(index)) if wk[i] != wk[i - 1]]


def picks(c: pd.DataFrame, r: int, sector_of: dict, rng=None, news_neg: dict | None = None) -> dict:
    """{symbol: +1 / -1} for the rebalance at row r (data up to row r-1)."""
    if r - 1 - LOOK < 0:
        return {}
    ret = c.iloc[r - 1] / c.iloc[r - 1 - LOOK] - 1
    etfs = sorted({e for e in sector_of.values() if e in c and np.isfinite(ret.get(e, np.nan))})
    if len(etfs) < 2 * TOP:
        return {}
    order = sorted(etfs, key=lambda e: ret[e], reverse=True)
    if rng is not None:
        order = list(rng.permutation(order))
    day = c.index[r - 1]
    out = {}
    for side, secs in ((1, order[:TOP]), (-1, order[-TOP:])):
        for e in secs:
            if news_neg is not None:
                neg = bool(news_neg.get(e, pd.Series(dtype=bool)).get(day, False))
                if (side == 1 and neg) or (side == -1 and not neg):
                    continue
            rel = {s: ret[s] - ret[e] for s, se in sector_of.items() if se == e and s in c and np.isfinite(ret.get(s, np.nan))}
            if rel:
                out[(max if side == 1 else min)(rel, key=rel.get)] = side
    return out


def run(c: pd.DataFrame, o: pd.DataFrame, sector_of: dict, a: date, b: date, cost_bps: float = COST_BPS, seed=None, news_neg=None) -> pd.DataFrame:
    """Weekly rows: date, ret (sleeve), long, short (leg contributions), names."""
    rng = np.random.default_rng(seed) if seed is not None else None
    rows = [r for r in rebalance_rows(c.index) if a <= c.index[r].date() <= b]
    out, held = [], {}
    for n, r in enumerate(rows):
        nxt = rows[n + 1] if n + 1 < len(rows) else None
        p = picks(c, r, sector_of, rng, news_neg)
        cost = sum(1 for s in set(p) | set(held) if p.get(s) != held.get(s)) * cost_bps / 1e4 / (2 * TOP)
        held = p
        if nxt is None:
            break
        legs = {1: 0.0, -1: 0.0}
        for s, side in p.items():
            o0, o1 = o[s].iloc[r], o[s].iloc[nxt]
            if np.isfinite(o0) and np.isfinite(o1) and o0 > 0:
                legs[side] += side * (o1 / o0 - 1) / (2 * TOP)
        days = (c.index[nxt] - c.index[r]).days
        borrow = sum(1 for v in p.values() if v == -1) / (2 * TOP) * BORROW * days / 365
        out.append({"date": c.index[r], "ret": legs[1] + legs[-1] - cost - borrow, "long": legs[1], "short": legs[-1],
                    "names": " ".join(f"{'+' if v == 1 else '-'}{s}" for s, v in sorted(p.items(), key=lambda kv: -kv[1]))})
    return pd.DataFrame(out)


def stats(w: pd.DataFrame) -> dict:
    if w.empty:
        return {"weeks": 0, "total": 0.0, "sharpe": 0.0, "maxdd": 0.0, "long": 0.0, "short": 0.0}
    eq = (1 + w["ret"]).cumprod()
    sd = w["ret"].std()
    return {"weeks": len(w), "total": float(eq.iloc[-1] - 1), "sharpe": float(w["ret"].mean() / sd * np.sqrt(52)) if sd > 0 else 0.0,
            "maxdd": float((eq / eq.cummax() - 1).min()), "long": float((1 + w["long"]).prod() - 1), "short": float((1 + w["short"]).prod() - 1)}


def fmt(s: dict) -> str:
    return (f"{s['weeks']} weeks, {s['total']:+.1%}, Sharpe {s['sharpe']:.2f}, maxDD {s['maxdd']:.1%} "
            f"(long leg {s['long']:+.1%}, short leg {s['short']:+.1%})")


def report(daily: dict, sector_of: dict, control: tuple[dict, dict] | None, today: date, news_neg: dict | None = None,
           start: date = date(2017, 1, 1), seeds: int = 20) -> list[str]:
    """daily = {symbol: bars} for the stocks and the 11 sector ETFs; control = (daily, sector_of) of the hindsight-free list."""
    c, o = frames(daily)
    out = [f"## sector_heatmap: weekly long / short sleeve, 3 hottest vs 3 coldest sectors (21-session heat), {start}..{today}"]
    ret = c.iloc[-1] / c.iloc[-1 - LOOK] - 1
    etfs = sorted({e for e in sector_of.values() if e in c}, key=lambda e: -ret[e])
    out.append("- heat map today (21 sessions): " + ", ".join(f"{e} {ret[e]:+.1%}" for e in etfs))
    now = picks(c, len(c), sector_of) if len(c) > LOOK + 1 else {}
    out.append("- this week's picks: " + (" ".join(f"{'LONG' if v == 1 else 'SHORT'} {s}" for s, v in now.items()) or "none"))
    full = {bp: run(c, o, sector_of, start, today, bp) for bp in (COST_BPS, 2 * COST_BPS)}
    rnd = [stats(run(c, o, sector_of, start, today, 2 * COST_BPS, seed=s))["total"] for s in range(seeds)]
    s10 = stats(full[2 * COST_BPS])
    beat = sum(s10["total"] > x for x in rnd)
    out.append(f"- full period, 5 bps: {fmt(stats(full[COST_BPS]))}")
    out.append(f"- full period, 10 bps: {fmt(s10)} | random sector rankings: median {np.median(rnd):+.1%}, beaten {beat}/{seeds}")
    w = full[COST_BPS]
    if not w.empty:
        yr = w.groupby(w["date"].dt.year)["ret"].apply(lambda r: (1 + r).prod() - 1)
        out.append("- by year (5 bps): " + ", ".join(f"{y} {v:+.1%}" for y, v in yr.items()))
    for label, a in (("last 2 weeks", today - timedelta(days=14)), ("last 3 months", today - timedelta(days=92))):
        out.append(f"- {label}: {fmt(stats(run(c, o, sector_of, a, today)))}")
    ctrl_ok = None
    if control is not None:
        cc, co = frames(control[0])
        cs = stats(run(cc, co, control[1], start, today, 2 * COST_BPS))
        ctrl_ok = cs["total"] > 0
        out.append(f"- control universe (2016's largest companies), 10 bps: {fmt(cs)}")
    ok = s10["total"] > 0 and s10["sharpe"] >= 0.5 and beat >= round(0.9 * seeds) and ctrl_ok is not False
    out.append(f"- plain heat-map sleeve: {'PASS' if ok else 'fail'}")
    if news_neg is not None:
        a = today - timedelta(days=730)
        p_, n_ = stats(run(c, o, sector_of, a, today, 2 * COST_BPS)), stats(run(c, o, sector_of, a, today, 2 * COST_BPS, news_neg=news_neg))
        rn = [stats(run(c, o, sector_of, a, today, 2 * COST_BPS, seed=s, news_neg=news_neg))["total"] for s in range(seeds)]
        bn = sum(n_["total"] > x for x in rn)
        okn = n_["total"] > 0 and n_["sharpe"] >= 0.5 and bn >= round(0.9 * seeds) and n_["total"] > p_["total"]
        out.append(f"- last 24 months, 10 bps: plain {fmt(p_)} | + news {fmt(n_)} | news on random rankings beaten {bn}/{seeds} | "
                   f"news variant: {'PASS' if okn else 'fail'}")
        nw = run(c, o, sector_of, today - timedelta(days=92), today, news_neg=news_neg)
        out.append(f"- + news, last 3 months: {fmt(stats(nw))}")
    else:
        out.append("- news variant: news feed unavailable, not tested")
    return out
