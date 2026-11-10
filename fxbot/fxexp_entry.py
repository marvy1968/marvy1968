"""Owner 2026-11-10 ("run the test"): do the fx-experts bot's STOP-ENTRY systems beat its
MARKET / LIMIT-entry systems?  Lead: the OANDA export for Nov 2-9 showed 21 stop-entry trades at
PF 2.64 (+8.2R) vs 14 market/limit trades at PF 0.85 (-1.0R). One week proves nothing, so this is the
3-year check, with the rule fixed before any result is seen.

Research only: it runs the bot's own strategy code (forex_bots/fx-experts, fxexp.backtest.Sim) on
OANDA H1 candles (bid/ask mid; Sim adds the typical spread, slippage, a widened rollover spread and
financing). The three intraday systems (brooks-range-fade, dalton-value, lien-zeros) need M5 data and
an intraday simulator that doesn't exist, so they are not in this test.
"""
import math
import sys
from pathlib import Path

ENTRY_RULE = (
    "pre-registered 2026-11-10, before any result: STOP group = lien-fader, raschke-soup, faith-turtle, "
    "crabel-idnr4, williams-vbo (resting stop entries); OTHER group = bollinger-squeeze, tharp-random "
    "(market), ponsi-trend (limit). Last 3 years of OANDA H1, the bot's 9 pairs, split into two halves "
    "by entry date. PASS ('switch the OTHER systems off') only if: both groups have 30+ trades; STOP PF > "
    "OTHER PF in BOTH halves; OTHER PF < 1.0 over the whole window; STOP PF >= 1.0 with the spread doubled; "
    "STOP avg R beats the tharp-random coin-flip control. Otherwise FAIL, nothing changes.")

STOP = ("lien-fader", "raschke-soup", "faith-turtle", "crabel-idnr4", "williams-vbo")
OTHER = ("bollinger-squeeze", "tharp-random", "ponsi-trend")
PAIRS = ("EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "USD_CHF", "NZD_USD", "EUR_JPY", "GBP_JPY")


def _fxexp():
    root = Path(__file__).resolve().parent.parent / "forex_bots" / "fx-experts"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import fxexp.backtest as bt
    import fxexp.data as data
    import fxexp.strategies as strategies
    return bt, data, strategies


def to_fxexp(h1):
    """fxbot H1 frame (bid/ask columns, UTC index) -> fxexp intraday frame (time + mid OHLC)."""
    import pandas as pd
    out = pd.DataFrame({k: (h1["b" + k[0]] + h1["a" + k[0]]) / 2 for k in ("open", "high", "low", "close")})
    out.insert(0, "time", pd.to_datetime(h1.index, utc=True))
    return out.reset_index(drop=True)


def stats(rs):
    rs = list(rs)
    w = sum(r for r in rs if r > 0)
    l = -sum(r for r in rs if r <= 0)
    return {"n": len(rs), "pf": (w / l) if l > 0 else (math.inf if w > 0 else float("nan")),
            "net": sum(rs), "avg": (sum(rs) / len(rs)) if rs else 0.0,
            "win": (sum(1 for r in rs if r > 0) / len(rs)) if rs else 0.0}


def run_all(frames, systems, spread_mult=1.0):
    """{sid: [Trade]} over every pair; spread_mult scales the typical spread the simulator charges."""
    bt, data, strategies = _fxexp()
    orig = bt.spread
    if spread_mult != 1.0:
        bt.spread = lambda pair: orig(pair) * spread_mult
    try:
        out = {}
        for sid in systems:
            trades = []
            for pair, df in frames.items():
                if df is None or not len(df):
                    continue
                trades += bt.Sim(strategies.BY_SID[sid](), pair, df, data.to_daily(df)).run()
            out[sid] = trades
        return out
    finally:
        bt.spread = orig


def verdict(base, wide, cut):
    """base / wide: {sid: [Trade]} at 1x / 2x spread; cut: ISO date string splitting the halves."""
    def grp(res, sids, half=None):
        rs = []
        for s in sids:
            for t in res.get(s, []):
                first = str(t.entry_time)[:10] < cut
                if half is None or (half == 1) == first:
                    rs.append(t.r)
        return stats(rs)
    g = {name: {h: grp(base, sids, h) for h in (None, 1, 2)} for name, sids in (("STOP", STOP), ("OTHER", OTHER))}
    stop_wide = grp(wide, STOP)
    control = stats(t.r for t in base.get("tharp-random", []))
    checks = {
        "30+ trades in each group": g["STOP"][None]["n"] >= 30 and g["OTHER"][None]["n"] >= 30,
        "STOP PF > OTHER PF, half 1": g["STOP"][1]["pf"] > g["OTHER"][1]["pf"],
        "STOP PF > OTHER PF, half 2 (unseen)": g["STOP"][2]["pf"] > g["OTHER"][2]["pf"],
        "OTHER PF < 1.0 over 3 years": g["OTHER"][None]["pf"] < 1.0,
        "STOP PF >= 1.0 at 2x spread": stop_wide["pf"] >= 1.0,
        "STOP avg R beats the coin-flip control": g["STOP"][None]["avg"] > control["avg"],
    }
    return g, stop_wide, control, checks


def _fmt(s):
    return f"n={s['n']:4d} win={s['win'] * 100:4.1f}% PF={s['pf']:5.2f} net={s['net']:+7.1f}R avg={s['avg']:+.3f}R"


def report(h1, today):
    """h1: {pair: fxbot H1 frame}. Returns report lines."""
    import datetime as dt
    start = today - dt.timedelta(days=3 * 365)
    cut = (start + (today - start) / 2).isoformat()
    frames = {p: to_fxexp(df[df.index.date >= start]) for p, df in h1.items() if df is not None and len(df)}
    systems = STOP + OTHER
    base = run_all(frames, systems)
    wide = run_all(frames, STOP, spread_mult=2.0)
    g, stop_wide, control, checks = verdict(base, wide, cut)
    lines = ["FX-EXPERTS ENTRY-TYPE TEST (stop entries vs market / limit entries)", ENTRY_RULE,
             f"window {start} .. {today}, halves split at {cut}; pairs with data: {', '.join(sorted(frames))}", ""]
    for name in ("STOP", "OTHER"):
        lines += [f"{name:5s} all    {_fmt(g[name][None])}", f"{name:5s} half 1 {_fmt(g[name][1])}",
                  f"{name:5s} half 2 {_fmt(g[name][2])}"]
    lines += [f"STOP at 2x spread {_fmt(stop_wide)}", f"coin-flip control (tharp-random) {_fmt(control)}", "", "per system (1x spread):"]
    for sid in systems:
        rs = [t.r for t in base.get(sid, [])]
        h1s = stats(t.r for t in base.get(sid, []) if str(t.entry_time)[:10] < cut)
        h2s = stats(t.r for t in base.get(sid, []) if str(t.entry_time)[:10] >= cut)
        lines.append(f"  {sid:18s} {'STOP ' if sid in STOP else 'OTHER'} {_fmt(stats(rs))}  halves PF {h1s['pf']:.2f} / {h2s['pf']:.2f}")
    lines += ["", "checks:"] + [f"  [{'x' if ok else ' '}] {k}" for k, ok in checks.items()]
    lines.append("VERDICT: " + ("PASS - propose switching the OTHER systems off (owner decides)" if all(checks.values())
                                else "FAIL - entry type is not the edge; nothing changes"))
    return lines
