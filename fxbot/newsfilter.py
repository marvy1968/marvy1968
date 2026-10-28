"""News filter for the two live OANDA setups (owner 2026-10-28: "let it use news ... both").

PRE-DECLARED before any run:
- News = the three US releases that move every major (all 7 are USD pairs): FOMC statements (14:00 New York, the Fed's published
  calendar, FOMC below), the jobs report and CPI (08:30 New York, release dates read from the BLS release archives on the VM; if BLS
  cannot be read, the jobs report falls back to the BLS timing rule and CPI is left out, and the report says so).
- Setups: lien_h1 (LienFader(break_pips=20), fxbot.engine.simulate) and retest_h1 (fxbot.playbook, PlaybookRules(be_at_r=0.0)), 1-hour
  OANDA bid/ask on the 7 majors, financing per rollover held.
- Variants: A as live; B no new entries from 2 hours before to 1 hour after a release; C = B and any open trade is closed at the open
  of the bar that starts 1 hour or less before the release (stand aside through the number).
- Control: the same filter on fake release times (the real times of day moved to random weekdays at least 2 days from a real release),
  20 seeds. A variant passes when, over 2023-01-01..today, it changes 20+ trades, adds R at 1x AND 2x spread, beats 18 of the 20 fake
  calendars, and does not lose R against A in the last 92 days. The last 2 weeks and 3 months are shown, but hold only a handful of releases each.
"""

import re
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

NY = "America/New_York"
FOMC = ["2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
        "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
        "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
        "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]
BLS = {"nfp": "https://www.bls.gov/bls/news-release/empsit.htm", "cpi": "https://www.bls.gov/bls/news-release/cpi.htm"}
BLS_RE = {"nfp": re.compile(r"empsit_(\d{8})\.htm"), "cpi": re.compile(r"cpi_(\d{8})\.htm")}
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"}
BEFORE_H, AFTER_H, FLAT_H = 2.0, 1.0, 1.0


def parse_bls(kind: str, html: str) -> list[date]:
    """Release dates from a BLS archive page: archived releases are named <name>_MMDDYYYY.htm (the release date)."""
    out = set()
    for s in BLS_RE[kind].findall(html):
        try:
            out.add(date(int(s[4:]), int(s[:2]), int(s[2:4])))
        except ValueError:
            continue
    return sorted(out)


def nfp_rule(y: int, m: int) -> date:
    """Fallback: the jobs report for month m-1 comes out on the third Friday after the week holding the 12th (Sun-Sat) ends."""
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    d12 = date(py, pm, 12)
    sat = d12 + timedelta(days=(5 - d12.weekday()) % 7)
    return sat + timedelta(days=6 + 14)


def calendar(start: date, end: date, fetch=None) -> tuple[list[tuple[pd.Timestamp, str]], list[str]]:
    """[(UTC release time, kind)] in [start, end] and notes on the sources used. fetch(url) -> html (None = no network)."""
    notes, ev = [], []
    for kind in ("nfp", "cpi"):
        days = []
        if fetch is not None:
            try:
                days = parse_bls(kind, fetch(BLS[kind]))
            except Exception as exc:
                notes.append(f"BLS {kind} archive unreadable ({type(exc).__name__}: {str(exc)[:80]})")
        if len(days) >= 12:
            notes.append(f"{kind}: {len(days)} release dates from the BLS archive")
        elif kind == "nfp":
            days = [nfp_rule(y, m) for y in range(start.year, end.year + 1) for m in range(1, 13)]
            notes.append("nfp: BLS timing rule (archive not read)")
        else:
            days = []
            notes.append("cpi: left out (archive not read)")
        ev += [(d, "08:30", kind) for d in days]
    ev += [(date.fromisoformat(d), "14:00", "fomc") for d in FOMC]
    out = sorted((pd.Timestamp(f"{d} {hm}").tz_localize(NY).tz_convert("UTC"), k) for d, hm, k in ev if start <= d <= end)
    return out, notes


def fake_calendar(events: list, start: date, end: date, seed: int) -> list:
    """The same release times of day on random weekdays at least 2 days from any real release."""
    rng = np.random.default_rng(seed)
    real = {e[0].tz_convert(NY).date() for e in events}
    pool = [d.date() for d in pd.bdate_range(start, end) if all(abs((d.date() - r).days) >= 2 for r in real)]
    days = rng.choice(len(pool), size=min(len(events), len(pool)), replace=False)
    out = []
    for (t, k), di in zip(events, days):
        ny = t.tz_convert(NY)
        out.append((pd.Timestamp(datetime.combine(pool[di], ny.time())).tz_localize(NY).tz_convert("UTC"), k))
    return sorted(out)


def lien_trades(inst: str, df: pd.DataFrame, a: date, b: date) -> list[dict]:
    from .engine import FIN_PER_NIGHT, simulate
    from .strategies import LienFader
    out = []
    for t in simulate(inst, df, LienFader(break_pips=20)):
        if a <= t.entry_time.date() <= b:
            fin = t.nights * FIN_PER_NIGHT * t.entry_px / t.stop_dist
            out.append({"inst": inst, "side": t.side, "entry": t.entry_time, "exit": t.exit_time, "fill": t.entry_px, "risk": t.stop_dist,
                        "r": t.side * (t.exit_px - t.entry_px) / t.stop_dist - fin, "fin": fin})
    return out


def retest_trades(inst: str, df: pd.DataFrame, a: date, b: date) -> list[dict]:
    from .engine import FIN_PER_NIGHT, _nights
    from .playbook import PlaybookRules, simulate_playbook
    out = []
    for t in simulate_playbook(inst, df, PlaybookRules(be_at_r=0.0), a, b):
        side = 1 if t["side"] == "long" else -1
        fill = side * t["fill"]
        fin = _nights(t["entry"], t["exit"]) * FIN_PER_NIGHT * abs(fill) / t["risk"]
        out.append({"inst": inst, "side": side, "entry": t["entry"], "exit": t["exit"], "fill": fill, "risk": t["risk"], "r": t["r"] - fin, "fin": fin})
    return out


def apply(trades: list[dict], events: list, bars: dict, flat: bool) -> list[dict]:
    """Variant B (flat=False) or C (flat=True) applied to trades; bars = {inst: bid/ask frame} for the exit price of C."""
    times = np.array(sorted(e[0].value for e in events), dtype=np.int64)
    out = []
    for t in trades:
        e = pd.Timestamp(t["entry"])
        e_ns = e.value
        k = np.searchsorted(times, e_ns - int(AFTER_H * 3.6e12))       # first release later than entry - 1 h
        if k < len(times) and times[k] - int(BEFORE_H * 3.6e12) <= e_ns:
            continue                                                     # entry inside [release - 2 h, release + 1 h]
        if flat:
            x = pd.Timestamp(t["exit"]).value
            k2 = np.searchsorted(times, e_ns)
            if k2 < len(times) and times[k2] <= x + int(FLAT_H * 3.6e12) and times[k2] - int(FLAT_H * 3.6e12) < x:
                df = bars[t["inst"]]
                cut = pd.Timestamp(times[k2] - int(FLAT_H * 3.6e12), tz="UTC")
                j = df.index.searchsorted(cut)
                if j < len(df) and df.index[j] > e and df.index[j].value < x:
                    px = float(df["bo"].iloc[j] if t["side"] == 1 else df["ao"].iloc[j])
                    frac = (df.index[j].value - e_ns) / max(x - e_ns, 1)
                    t = {**t, "exit": df.index[j], "r": t["side"] * (px - t["fill"]) / t["risk"] - t["fin"] * frac, "why": "news"}
        out.append(t)
    return out


def summary(tr: list[dict]) -> dict:
    rs = np.array([t["r"] for t in tr], float)
    if not len(rs):
        return {"trades": 0, "net": 0.0, "pf": 0.0, "win": 0.0}
    w, lo = rs[rs > 0].sum(), -rs[rs <= 0].sum()
    return {"trades": len(rs), "net": float(rs.sum()), "pf": float(w / lo) if lo > 0 else 99.0, "win": float((rs > 0).mean())}


def fmt(s: dict) -> str:
    return f"{s['trades']} trades, win {s['win']:.0%}, PF {s['pf']:.2f}, net {s['net']:+.1f}R"


def near_news(tr: list[dict], events: list) -> tuple[list, list]:
    """(trades open across a release or entered within 2 h before / 1 h after one, the rest)."""
    times = np.array(sorted(e[0].value for e in events), dtype=np.int64)
    a, b = [], []
    for t in tr:
        e, x = pd.Timestamp(t["entry"]).value, pd.Timestamp(t["exit"]).value
        k = np.searchsorted(times, e - int(AFTER_H * 3.6e12))
        hit = k < len(times) and times[k] - int(BEFORE_H * 3.6e12) <= x
        (a if hit else b).append(t)
    return a, b


def report(h1: dict, today: date, fetch=None, seeds: int = 20, start: date = date(2023, 1, 1)) -> list[str]:
    """h1 = {inst: 1-hour bid/ask from before `start`}. Lines for the owner."""
    from .exitlab import _spread
    events, notes = calendar(start - timedelta(days=5), today + timedelta(days=2), fetch)
    by = {k: sum(1 for e in events if e[1] == k) for k in ("nfp", "cpi", "fomc")}
    out = [f"## fx_news: news filter on the live forex setups ({start}..{today}); releases: {by}; " + "; ".join(notes),
           f"- B = no entries {BEFORE_H:g} h before to {AFTER_H:g} h after a release; C = B + close open trades {FLAT_H:g} h before it. "
           f"Pass: 20+ trades changed, adds R at 1x and 2x, beats 18/{seeds} fake calendars, not worse than A in the last 92 days"]
    nxt = [e for e in events if e[0] >= pd.Timestamp.now(tz="UTC")][:4]
    out.append("- next releases: " + ", ".join(f"{k} {t.tz_convert(NY):%a %b %d %H:%M} ET" for t, k in nxt))
    sims = {"lien_h1": lien_trades, "retest_h1": retest_trades}
    for name, fn in sims.items():
        out.append(f"### {name}")
        base = {sm: [t for i, d in h1.items() for t in fn(i, _spread(d, sm), start, today)] for sm in (1.0, 2.0)}
        bars = {sm: {i: _spread(d, sm) for i, d in h1.items()} for sm in (1.0, 2.0)}
        nn, rest = near_news(base[1.0], events)
        out.append(f"- trades near a release: {fmt(summary(nn))} | the rest: {fmt(summary(rest))}")
        for vname, flat in (("B skip entries", False), ("C skip + stand aside", True)):
            gain = {sm: summary(apply(base[sm], events, bars[sm], flat))["net"] - summary(base[sm])["net"] for sm in (1.0, 2.0)}
            fake = [summary(apply(base[1.0], fake_calendar(events, start, today, s), bars[1.0], flat))["net"] - summary(base[1.0])["net"]
                    for s in range(seeds)]
            beat = sum(gain[1.0] > f for f in fake)
            win = {}
            for label, a in (("last 14 days", today - timedelta(days=14)), ("last 92 days", today - timedelta(days=92))):
                b_ = [t for t in base[1.0] if pd.Timestamp(t["entry"]).date() >= a]
                v_ = apply(b_, events, bars[1.0], flat)
                win[label] = (summary(b_), summary(v_))
            v1 = apply(base[1.0], events, bars[1.0], flat)
            changed = len(base[1.0]) - len(v1) + sum(1 for t in v1 if t.get("why") == "news")
            ok = changed >= 20 and gain[1.0] > 0 and gain[2.0] > 0 and beat >= round(0.9 * seeds) and win["last 92 days"][1]["net"] >= win["last 92 days"][0]["net"]
            out.append(f"- {vname}: full period A {fmt(summary(base[1.0]))} -> {fmt(summary(apply(base[1.0], events, bars[1.0], flat)))} | "
                       f"{changed} trades changed, gain {gain[1.0]:+.1f}R (2x spread {gain[2.0]:+.1f}R) | fake calendars: gain {np.median(fake):+.1f}R median, "
                       f"beaten {beat}/{seeds} | " + " | ".join(f"{k}: {a_['net']:+.1f}R -> {v_['net']:+.1f}R ({v_['trades']} t)" for k, (a_, v_) in win.items())
                       + f" | {'PASS' if ok else 'fail'}")
    return out
