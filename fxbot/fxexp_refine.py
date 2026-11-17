"""Owner 2026-11-17 ("refine and optimize the experts that are losing or not so profitable"): the 3-year check of
four pre-registered refinements to the fx-experts bot's weak systems. Research only: it runs the bot's own strategy
code (fxexp.backtest.Sim, same as fxbot.fxexp_entry) on OANDA H1 candles; nothing here touches the live bot.

Why these four (fx-experts reports/SUMMARY.md, 10 years, untuned): Crabel and Soup are positive before costs
(gross PF 1.13 / 1.27) and lose to costs because their stops are small -> a cost filter and a session filter.
Turtle (Faith) loses everywhere except USD/JPY -> USD/JPY only. Fader loses on AUD/USD and NZD/USD -> drop those.
Each item is judged on its own against the unfiltered system, one change at a time.
"""
import datetime as dt
import random

from . import fxexp_entry as fe

RULE = ("pre-registered 2026-11-17, before any result. Items (filter on the system's own trades, one change each): "
        "A cost filter: skip a trade when (typical spread + slippage) / stop distance > 10%  [crabel-idnr4, raschke-soup]; "
        "B session filter: keep entries 07:00-17:00 UTC  [crabel-idnr4, raschke-soup, lien-fader]; "
        "C USD/JPY only  [faith-turtle]; D drop AUD/USD and NZD/USD  [lien-fader]. "
        "Window: last 3 years of OANDA H1, 9 pairs, halves by entry date. An item PASSES only if all hold: "
        "30+ kept trades; kept PF >= base PF in half 1 and > base PF in half 2 (unseen); kept PF >= 1.0 at 2x spread; "
        "kept avg R above the 95th percentile of 300 seeded random removals of the same number of trades. "
        "A PASS is only a lead: ADOPT needs the owner's install of a bot change. Otherwise FAIL, nothing changes.")

COST_MAX = 0.10
SESSION = (7, 17)
ITEMS = [
    ("A1", "crabel-idnr4", "cost<=10% of stop"), ("A2", "raschke-soup", "cost<=10% of stop"),
    ("B1", "crabel-idnr4", "07-17 UTC"), ("B2", "raschke-soup", "07-17 UTC"), ("B3", "lien-fader", "07-17 UTC"),
    ("C1", "faith-turtle", "USD_JPY only"), ("D1", "lien-fader", "no AUD/NZD"),
]
SYSTEMS = ("crabel-idnr4", "raschke-soup", "lien-fader", "faith-turtle")


def keep_cost(t, spread_fn, slip_fn):
    """True when the round-trip cost is at most COST_MAX of the stop distance."""
    return (spread_fn(t.pair) + slip_fn(t.pair)) / t.risk <= COST_MAX if t.risk > 0 else False


def keep_session(t):
    h = int(str(t.entry_time)[11:13]) if len(str(t.entry_time)) >= 13 else -1
    return SESSION[0] <= h < SESSION[1]


def keep_pair(t, allowed=None, banned=()):
    return (allowed is None or t.pair in allowed) and t.pair not in banned


def filter_for(label, spread_fn, slip_fn):
    kind = label[0]
    if kind == "A":
        return lambda t: keep_cost(t, spread_fn, slip_fn)
    if kind == "B":
        return keep_session
    if label == "C1":
        return lambda t: keep_pair(t, allowed=("USD_JPY",))
    return lambda t: keep_pair(t, banned=("AUD_USD", "NZD_USD"))


def random_removal_p95(rs, n_keep, draws=300, seed=7):
    """95th percentile of avg R when n_keep trades are kept at random (a coin-flip filter)."""
    rnd = random.Random(seed)
    avgs = sorted(sum(rnd.sample(rs, n_keep)) / n_keep for _ in range(draws)) if 0 < n_keep < len(rs) else [0.0]
    return avgs[int(0.95 * (len(avgs) - 1))]


def judge(base_trades, kept_trades, wide_kept, cut):
    """base_trades / kept_trades at 1x spread, wide_kept at 2x (all lists of Trade); cut = ISO split date."""
    def half(ts, first):
        return fe.stats(t.r for t in ts if (str(t.entry_time)[:10] < cut) == first)
    b1, b2, k1, k2 = half(base_trades, True), half(base_trades, False), half(kept_trades, True), half(kept_trades, False)
    kall, ball, kwide = fe.stats(t.r for t in kept_trades), fe.stats(t.r for t in base_trades), fe.stats(t.r for t in wide_kept)
    p95 = random_removal_p95([t.r for t in base_trades], len(kept_trades))
    checks = {
        "30+ kept trades": kall["n"] >= 30,
        "kept PF >= base PF, half 1": k1["pf"] >= b1["pf"],
        "kept PF > base PF, half 2 (unseen)": k2["pf"] > b2["pf"],
        "kept PF >= 1.0 at 2x spread": kwide["pf"] >= 1.0,
        "kept avg R beats random removal (p95)": kall["avg"] > p95,
    }
    return {"base": ball, "kept": kall, "wide": kwide, "h1": (b1, k1), "h2": (b2, k2), "p95": p95, "checks": checks,
            "pass": all(checks.values())}


def report(h1, today):
    """h1: {pair: fxbot H1 frame}. Returns report lines."""
    fe._fxexp()  # puts forex_bots/fx-experts on sys.path
    import fxexp.instruments as ins
    start = today - dt.timedelta(days=3 * 365)
    cut = (start + (today - start) / 2).isoformat()
    frames = {p: fe.to_fxexp(df[df.index.date >= start]) for p, df in h1.items() if df is not None and len(df)}
    base = fe.run_all(frames, SYSTEMS)
    wide = fe.run_all(frames, SYSTEMS, spread_mult=2.0)
    spread_fn, slip_fn = ins.spread, (lambda pair: ins.SLIP_PIPS * ins.pip(pair))
    lines = ["FX-EXPERTS REFINEMENT TEST (weak systems)", RULE, f"window {start} .. {today}, halves split at {cut}", ""]
    lines.append("baselines (1x spread): " + "; ".join(
        f"{s} n={fe.stats(t.r for t in base[s])['n']} PF={fe.stats(t.r for t in base[s])['pf']:.2f}" for s in SYSTEMS))
    lines.append("")
    verdicts = []
    for label, sid, what in ITEMS:
        f = filter_for(label, spread_fn, slip_fn)
        j = judge(base[sid], [t for t in base[sid] if f(t)], [t for t in wide[sid] if f(t)], cut)
        verdicts.append((label, sid, j["pass"]))
        lines.append(f"{label} {sid} [{what}]  base {fe._fmt(j['base'])}")
        lines.append(f"     kept {fe._fmt(j['kept'])}   2x spread PF {j['wide']['pf']:.2f}   random-removal p95 avg {j['p95']:+.3f}R")
        lines += [f"     [{'x' if ok else ' '}] {k}" for k, ok in j["checks"].items()]
        lines.append(f"     -> {'PASS (a lead only; needs the owner to install a bot change)' if j['pass'] else 'FAIL, nothing changes'}")
    lines += ["", "VERDICT: " + (", ".join(f"{a} {b}" for a, b, ok in verdicts if ok) + " passed the short bar" if any(ok for *_, ok in verdicts)
                                else "no item passed; the weak systems stay as they are (or off, per v2)")]
    return lines
