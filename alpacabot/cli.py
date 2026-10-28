"""python -m alpacabot download|backtest ...   (run on the VM: needs Alpaca data keys in the environment)"""

import argparse
import json
import os
from datetime import date
from pathlib import Path

from .data import coverage, credentials, load
from .engine import Costs, Portfolio, split_days
from .experts import features_all, features_both
from .gates import HEAT_ETFS, add_gates
from .research import GRIDS, brain_walk_forward, exit_study, filters_study, limit_study, sector_td_robust, sector_td_study, swing_expert_study, swing_validate, symbol_check, verdict, walk_forward
from .strategies import DualRegime

from .universe import AS_OF as UNIVERSE_AS_OF, STOCKS as UNIVERSE_STOCKS
SECTOR_TD_THEIRS = ["AAPL", "NVDA", "MSFT", "AMD", "TSLA", "AMZN", "GOOGL", "META", "XLK", "QQQ"]
# 2016 large caps that are NOT in the list above (no hindsight pick of today's winners): many of them went sideways or down
SECTOR_TD_CONTROL = ["XOM", "JNJ", "GE", "JPM", "WFC", "T", "PG", "VZ", "KO", "WMT", "CVX", "PFE", "BAC", "INTC", "CSCO", "HD", "DIS", "MRK", "ORCL", "IBM", "C"]
INTRADAY = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "AMD"]
SWING = INTRADAY + ["DIA", "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLC", "GLD", "TLT"]


def _d(s: str) -> date:
    return date.fromisoformat(s)


def load_env():
    for p in (os.environ.get("MARV_ENV"), "/opt/marv-bot/.env", ".env"):
        if p and Path(p).exists():
            for line in Path(p).read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.split(" #")[0].strip().strip('"').strip("'"))


def run_all(a) -> int:
    if not all(credentials()):
        print("WAITING FOR KEYS: add ALPACA_DATA_KEY_ID and ALPACA_DATA_SECRET_KEY (or ALPACA_API_KEY and ALPACA_SECRET_KEY) to /opt/marv-bot/.env")
        return 2
    cache, out = Path(a.cache), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    start = date(2016, 1, 4)
    qtag = "_quick" if getattr(a, "quick", False) else ""
    mstart = start                                   # first day of 1-minute data (the memory-heavy part)
    if a.phase == "validate" and getattr(a, "weeks2", False):   # --weeks2 (owner 2026-10-21: "a 2-week test to make it faster"): two 1-week halves
        from datetime import timedelta as _td
        today = date.today()
        qtag, start, mstart = "_w2", today - _td(days=400), today - _td(days=30)
        train, test, end = (today - _td(days=15), today - _td(days=8)), (today - _td(days=7), today - _td(days=1)), today
    elif a.phase == "validate" and getattr(a, "month1", False):   # --month1 (owner: "a quick one-month test"): the last month, two halves
        from datetime import timedelta as _td
        today = date.today()
        qtag, start, mstart = "_m1", today - _td(days=400), today - _td(days=45)
        train, test, end = (today - _td(days=31), today - _td(days=16)), (today - _td(days=15), today - _td(days=1)), today
    elif a.phase == "validate" and getattr(a, "months3", False):   # --months3 (owner 2026-10-16: "keep tests short, like 3 months"):
        # the most recent 3 months, first half = train, second half = validate; daily history for the boxes / stages before that
        from datetime import timedelta as _td
        today = date.today()
        qtag, start, mstart = "_m3", today - _td(days=420), today - _td(days=100)
        train, test, end = (today - _td(days=92), today - _td(days=47)), (today - _td(days=46), today - _td(days=1)), today
    elif a.phase == "validate" and getattr(a, "year", False):   # --year: one year of 5-minute data (~1/7 of the memory), provisional
        qtag, start, mstart = "_year", date(2020, 6, 1), date(2021, 4, 1)
        train, test, end = (date(2021, 7, 1), date(2021, 12, 31)), (date(2022, 1, 3), date(2022, 6, 30)), date(2022, 6, 30)
    elif a.phase == "validate" and qtag:             # --quick: 2019-2022 only (provisional, about a third of the time); own checkpoints
        start = date(2018, 7, 2)
        train, test, end = (date(2019, 1, 1), date(2020, 12, 31)), (date(2021, 1, 1), date(2022, 12, 31)), date(2022, 12, 31)
    elif a.phase == "validate":
        train, test, end = (start, date(2020, 12, 31)), (date(2021, 1, 1), date(2022, 12, 31)), date(2022, 12, 31)
    else:
        train, test, end = (start, date(2022, 12, 31)), (date(2023, 1, 1), date.today()), date.today()
    costs, lines = Costs(slip_bps=a.slip_bps), [f"# Alpaca bot {a.phase} run: train {train[0]}..{train[1]}, test {test[0]}..{test[1]}\n"]
    for sym in SWING:
        load(sym, "1Day", start, end, cache)
    spy = load("SPY", "1Day", start, end, cache)
    print("daily data ready", flush=True)
    intraday_syms = list(getattr(a, "symbols", None) or INTRADAY)   # --symbols: test the intraday families on another universe (owner 2026-10-27)
    minute = {}
    default = ["swing_rsi", "swing_breakout", "day", "regime", "brain", "brain_ls", "scalp"]
    suffix = "" if list(a.families) == default else "_" + "_".join(a.families)     # a partial run never overwrites the full summary
    if len(suffix) > 80:                         # many families: a short stable name (a long list overflowed the file-name limit and crashed the run's summary)
        import hashlib
        suffix = "_" + hashlib.sha1(suffix.encode()).hexdigest()[:10]
    for fam in a.families:
        first = len(lines)
        try:
            if fam == "recentvote_fx":             # last-3-months vote test (fxbot.research.recent_vote_check)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import recent_vote_check
                today = _dt.date.today()
                a3 = today - _dt.timedelta(days=92)
                fxd = {i: fx_load(i, "H1", a3 - _dt.timedelta(days=60), today, Path("state/fx/cache")) for i in MAJORS}
                body, pick = recent_vote_check(fxd, a3, today)
                lines += [f"## recentvote_fx: confirmed box breakouts, last 3 months ({a3} to {today}, H1, bid/ask)"] + body
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "pristine_fx":               # owner 2026-10-22: Pristine / iFundTraders FX rules (fxbot.research.pristine_check)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import pristine_check
                today = _dt.date.today()
                for label, days_ in (("last 2 weeks", 14), ("last 3 months", 92)):
                    a_ = today - _dt.timedelta(days=days_)
                    dg = {g: {i: fx_load(i, g, a_ - _dt.timedelta(days=200 if g == "D" else 60), today, Path("state/fx/cache")) for i in MAJORS}
                          for g in ("H4", "H1")}
                    lines += [f"## pristine_fx: Pristine / iFundTraders FX rules, {label} ({a_} to {today}, bid/ask)"] + pristine_check(dg, a_, today)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam in ("hybrid_tune_fx", "hybrid_tune_fx12", "hybrid_tune_fx1"):   # owner 2026-10-22: tune hybrid_h1 with more Velez rules (fxbot.research.hybrid_tune)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import hybrid_tune
                today = _dt.date.today()
                a3 = today - _dt.timedelta(days={"hybrid_tune_fx12": 365, "hybrid_tune_fx1": 31}.get(fam, 92))
                h1 = {i: fx_load(i, "H1", a3 - _dt.timedelta(days=60), today, Path("state/fx/cache")) for i in MAJORS}
                mid_ = today - _dt.timedelta(days={"hybrid_tune_fx12": 120, "hybrid_tune_fx1": 15}.get(fam, 46))
                lines += [f"## hybrid_tune_fx: hybrid_h1 + Velez rules, train {a3}..{mid_}, validate {mid_}..{today}, H1 bid/ask"] + \
                    hybrid_tune(h1, (a3, mid_ - _dt.timedelta(days=1)), (mid_, today), (today - _dt.timedelta(days=14), today))
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "playbook_fx":               # owner 2026-10-25: the Break & Retest Trading Playbook as written (fxbot.playbook)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.playbook import PlaybookRules, playbook_report
                today = _dt.date.today()
                variants = [("as written (H4 zones, 1-hour entries, 2R min, break-even at +1R)", "H1", PlaybookRules()),
                            ("no break-even move", "H1", PlaybookRules(be_at_r=0.0)),
                            ("1.5R minimum target", "H1", PlaybookRules(min_rr=1.5)),
                            ("zones touched once allowed", "H1", PlaybookRules(require_two_touches=False)),
                            ("daily zones, 4-hour entries", "H4", PlaybookRules(htf="1D", zone_lookback=120))]
                for label, days_ in (("last 12 months", 365), ("last 3 years", 1095)):
                    a_ = today - _dt.timedelta(days=days_)
                    pb_cache = {}
                    lines.append(f"## playbook_fx: Break & Retest Trading Playbook on OANDA, {label} ({a_} to {today}, bid/ask, 7 majors)")
                    for name, g, rules in variants:
                        if g not in pb_cache:
                            pb_cache[g] = {i: fx_load(i, g, a_ - _dt.timedelta(days=150 if g == "H1" else 400), today, Path("state/fx/cache")) for i in MAJORS}
                        lines += playbook_report(pb_cache[g], a_, today, rules, name)
                    print("\n".join(lines[first:]), flush=True)
                    first = len(lines)
                continue
            if fam == "retest_diag_fx":            # owner 2026-10-25 "analyze why it failed" (fxbot.research.retest_diag)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import retest_diag
                from fxbot.strategies import BreakRetest, VelezFx
                today = _dt.date.today()
                a4, a1 = today - _dt.timedelta(days=1095), today - _dt.timedelta(days=365)
                h4 = {i: fx_load(i, "H4", a4 - _dt.timedelta(days=120), today, Path("state/fx/cache")) for i in MAJORS}
                lines += ["## retest_diag_fx: why the break-and-retest lost"]
                lines += retest_diag(h4, a4, today, lambda: BreakRetest(ranging=False, target_r=1.5), "4-hour, any box, 1.5R, last 3 years")
                h1 = {i: fx_load(i, "H1", a1 - _dt.timedelta(days=60), today, Path("state/fx/cache")) for i in MAJORS}
                lines += retest_diag(h1, a1, today, lambda: VelezFx(BreakRetest(ranging=False), env=True), "1-hour, any box, 2R + Velez trend, last 12 months")
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "retest_tf_fx":              # owner 2026-10-25: break-and-retest on 4-hour and daily bars (fxbot.research.retest_tf_check)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import retest_tf_check
                today = _dt.date.today()
                for g, warm in (("H4", 120), ("D", 400)):
                    for label, days_ in (("last 3 months", 92), ("last 12 months", 365), ("last 3 years", 1095)):
                        a_ = today - _dt.timedelta(days=days_)
                        dg = {i: fx_load(i, g, a_ - _dt.timedelta(days=warm), today, Path("state/fx/cache")) for i in MAJORS}
                        lines += [f"## retest_tf_fx: break-and-retest, {g} bars, {label} ({a_} to {today}, bid/ask)"] + retest_tf_check(dg, a_, today)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "session_pullback_fx":       # owner's paste 2026-10-26: EMA20/50 session pullback (fxbot.research.session_pullback_check)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import session_pullback_check
                today = _dt.date.today()
                for label, days_ in (("last 3 months", 92), ("last 12 months", 365), ("last 3 years", 1095)):
                    a_ = today - _dt.timedelta(days=days_)
                    h1 = {i: fx_load(i, "H1", a_ - _dt.timedelta(days=60), today, Path("state/fx/cache")) for i in MAJORS}
                    lines += [f"## session_pullback_fx: EMA20/50 session pullback, {label} ({a_} to {today}, H1, bid/ask)"] + session_pullback_check(h1, a_, today)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "experts_review_fx":         # owner 2026-10-27: every live OANDA expert, live vs own exits, 3 months / 12 months / 3 years
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import experts_review
                today = _dt.date.today()
                for label, days_ in (("last 3 months", 92), ("last 12 months", 365), ("last 3 years", 1095)):
                    a_ = today - _dt.timedelta(days=days_)
                    dg = {g: {i: fx_load(i, g, a_ - _dt.timedelta(days=(400 if g == "D" else 120)), today, Path("state/fx/cache")) for i in MAJORS}
                          for g in ("H1", "H4", "D")}
                    lines += [f"## experts_review_fx: every live OANDA expert, {label} ({a_} to {today}, bid/ask)"] + experts_review(dg, a_, today)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "fxexp_stages":              # owner 2026-10-27: the fx-experts four, staged 2 weeks -> 1 month -> 3 months (fxbot.fxexp_check.STAGES)
                import datetime as _dt
                import numpy as np
                from fxbot import fxexp_check as _fc
                from fxbot.data import load as fx_load
                today = _dt.date.today()
                _, _, instr, _ = _fc._bot()
                cache_ = Path("state/fx/cache")
                h1 = {p_: _fc._mid_frame(fx_load(p_, "H1", today - _dt.timedelta(days=92 + 160), today, cache_)) for p_ in instr.ALL_PAIRS}
                lines += ["## fxexp_stages: staged short tests of the fx-experts bot's four strategies (bot's own engine and costs at 2x spread; shuffled-data placebo)"]
                for sid in ("tharp-random", "williams-vbo", "faith-turtle"):
                    t2 = _fc.daily_trades(sid, h1, 2.0)
                    plc = [_fc.daily_trades(sid, {p_: _fc.shuffled_mid(h1[p_], k) for p_ in h1}, 2.0) for k in range(3)]
                    lines += _fc.stage_lines(sid, _fc.stage_table(t2, plc, today))
                z2, zp = [], {k: [] for k in range(3)}
                for p_ in instr.PAIRS:
                    m5 = fx_load(p_, "M5", today - _dt.timedelta(days=92 + 10), today, cache_)
                    z2 += _fc.zeros_trades(m5, p_, 2.0)
                    for k in zp:
                        zp[k] += _fc.zeros_trades(_fc.shuffled_bidask(m5, k), p_, 2.0)
                lines += _fc.stage_lines("lien-zeros", _fc.stage_table(z2, list(zp.values()), today))
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "trend_zones_stocks":        # owner 2026-10-28: trend experts' buy / sell / TP zones on 5 large, 5 mid, 5 small caps (alpacabot.trendzones)
                import datetime as _dt
                from .trendzones import GROUPS, report
                today_ = _dt.date.today()
                dd = {}
                for syms in GROUPS.values():
                    for s_ in syms:
                        try:
                            dd[s_] = load(s_, "1Day", today_ - _dt.timedelta(days=365 + 92 + 400), today_, cache)
                        except Exception as exc:
                            print(f"skip {s_}: {exc}", flush=True)
                lines += report(dd, today_)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "reversal_exit":             # owner 2026-10-28: flatten once the short-term trend turns against the trade (fxbot.strongtrend), 2 weeks
                import datetime as _dt
                from fxbot import strongtrend as _stg
                from fxbot.data import MAJORS, load as fx_load
                from .universe import STOCKS
                today_ = _dt.date.today()
                fc = Path("state/fx/cache")
                by = {g: {i: fx_load(i, g, today_ - _dt.timedelta(days=500 if g == "D" else 340), today_, fc) for i in MAJORS} for g in ("H1", "H4", "D")}
                lines += _stg.fx_reversal_report(by, today_)
                print("\n".join(lines[first:]), flush=True)
                first = len(lines)
                dd, f5 = {}, {}
                for s_ in sorted(set(STOCKS) | {"SPY", "QQQ"}):
                    try:
                        dd[s_] = load(s_, "1Day", today_ - _dt.timedelta(days=700), today_, cache)
                        f5[s_] = load(s_, "5Min", today_ - _dt.timedelta(days=40), today_, cache)
                    except Exception as exc:
                        print(f"skip {s_}: {exc}", flush=True)
                dd = {s: d for s, d in dd.items() if len(d) > 300}
                lines += _stg.stock_reversal_report(dd, {s: d for s, d in f5.items() if s in dd}, dd["SPY"]["close"], today_)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "two_filters":               # owner 2026-10-28: LONG trend filter (swing) + SHORT filter (hard move today, ride it today only), 2 weeks
                import datetime as _dt
                from fxbot import strongtrend as _stg
                from fxbot.data import MAJORS, load as fx_load
                from .universe import STOCKS
                today_ = _dt.date.today()
                fc = Path("state/fx/cache")
                by = {g: {i: fx_load(i, g, today_ - _dt.timedelta(days=500 if g == "D" else 340), today_, fc) for i in MAJORS} for g in ("H1", "H4", "D")}
                lines += _stg.fx_filters_report(by, today_)
                print("\n".join(lines[first:]), flush=True)
                first = len(lines)
                dd, f5 = {}, {}
                for s_ in sorted(set(STOCKS) | {"SPY", "QQQ"}):
                    try:
                        dd[s_] = load(s_, "1Day", today_ - _dt.timedelta(days=700), today_, cache)
                        f5[s_] = load(s_, "5Min", today_ - _dt.timedelta(days=40), today_, cache)
                    except Exception as exc:
                        print(f"skip {s_}: {exc}", flush=True)
                dd = {s: d for s, d in dd.items() if len(d) > 300}
                lines += _stg.stock_filters_report(dd, {s: d for s, d in f5.items() if s in dd}, dd["SPY"]["close"], today_)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "strong_trend":              # owner 2026-10-28: 2 of 3 strong-trend indicators before any expert fires, 2-week test (fxbot.strongtrend)
                import datetime as _dt
                from fxbot import strongtrend as _stg
                from fxbot.data import MAJORS, load as fx_load
                from .universe import STOCKS
                today_ = _dt.date.today()
                fc = Path("state/fx/cache")
                by = {g: {i: fx_load(i, g, today_ - _dt.timedelta(days=500 if g == "D" else 340), today_, fc) for i in MAJORS} for g in ("H1", "H4", "D")}
                lines += _stg.fx_report(by, today_)
                print("\n".join(lines[first:]), flush=True)
                dd = {}
                for s_ in sorted(set(STOCKS) | {"SPY", "QQQ"}):
                    try:
                        dd[s_] = load(s_, "1Day", today_ - _dt.timedelta(days=700), today_, cache)
                    except Exception as exc:
                        print(f"skip {s_}: {exc}", flush=True)
                first = len(lines)
                lines += _stg.stock_report({s: d for s, d in dd.items() if len(d) > 300}, dd["SPY"]["close"], today_)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "fx_news":                   # owner 2026-10-28: news filter on the live forex setups (fxbot.newsfilter, pre-declared)
                import datetime as _dt
                import requests as _rq
                from fxbot import newsfilter as _nf
                from fxbot.data import MAJORS, load as fx_load
                today = _dt.date.today()
                h1 = {i: fx_load(i, "H1", _dt.date(2022, 10, 1), today, Path("state/fx/cache")) for i in MAJORS}
                def _get(u):
                    r_ = _rq.get(u, headers=_nf.UA, timeout=30)
                    r_.raise_for_status()
                    return r_.text
                lines += _nf.report(h1, today, fetch=_get)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "sector_heatmap":            # owner 2026-10-28: sector heat-map long / short sleeve, with and without news (alpacabot.heatmap)
                import datetime as _dt
                from . import universe as _u
                from .heatmap import report as hm_report
                from .news import fetch as news_fetch, sector_news_negative
                today_ = _dt.date.today()
                syms = sorted(set(_u.STOCKS) | set(_u.SECTOR_ETFS) | set(_u.CONTROL_SECTOR_OF))
                dd = {}
                for s_ in syms:
                    try:
                        dd[s_] = load(s_, "1Day", _dt.date(2016, 9, 1), today_, cache)
                    except Exception as exc:
                        print(f"skip {s_}: {exc}", flush=True)
                main = {k: v for k, v in dd.items() if k in _u.SECTOR_OF or k in _u.SECTOR_ETFS}
                ctrl = ({k: v for k, v in dd.items() if k in _u.CONTROL_SECTOR_OF or k in _u.SECTOR_ETFS}, _u.CONTROL_SECTOR_OF)
                try:
                    days_ = dd["XLK"].index
                    neg = sector_news_negative(news_fetch(list(_u.STOCKS), today_ - _dt.timedelta(days=760), today_, cache / "news"), _u.SECTOR_OF,
                                               [d for d in days_ if d.date() >= today_ - _dt.timedelta(days=760)])
                except Exception as exc:
                    print(f"news unavailable: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
                    neg = None
                lines += hm_report(main, _u.SECTOR_OF, ctrl, today_, neg)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "trend_groups":              # owner 2026-10-28: 4 complementary trend groups, 3 of 4 must agree on buy / sell / TP (fxbot.consensus)
                import datetime as _dt
                import numpy as np
                import pandas as pd
                from fxbot import consensus as _cz
                from fxbot.data import MAJORS, load as fx_load, mid as fx_mid
                from fxbot.exitlab import _spread
                from fxbot.fxexp_check import shuffled_bidask
                today = _dt.date.today()
                t3a = today - _dt.timedelta(days=92)
                ca, cb = t3a - _dt.timedelta(days=365), t3a - _dt.timedelta(days=1)
                cache_ = Path("state/fx/cache")
                gmin = {"H1": 60, "H4": 240, "D": 1440}
                h1 = {i: fx_load(i, "H1", ca - _dt.timedelta(days=60), today, cache_) for i in MAJORS}
                bars = {g: {i: (h1[i] if g == "H1" else fx_load(i, g, ca - _dt.timedelta(days=300 if g == "D" else 60), today, cache_)) for i in MAJORS} for g in ("H1", "H4", "D")}
                zd = {i: _cz.zscore_daily(fx_mid(d))[0] for i, d in h1.items()}
                members = {n for ms in _cz.TREND_GROUPS.values() for n in ms}
                sig, tps = {}, {}
                for name, (make, g) in _cz.experts().items():
                    if name not in members:
                        continue
                    lz, sz_, tl, ts = [], [], [], []
                    for i in MAJORS:
                        try:
                            a_, b_ = _cz.signal_z(make, bars[g][i], zd[i], ca, cb, gmin[g])
                            c_, d_ = _cz.tp_zd(make, bars[g][i], zd[i], ca, cb)
                        except Exception as exc:
                            print(f"skip {name} {i}: {exc}", flush=True)
                            continue
                        lz += a_; sz_ += b_; tl += c_; ts += d_
                    sig[name], tps[name] = (lz, sz_), (tl, ts)
                G = _cz.group_bands(sig, tps)
                L = _cz.agreement_levels(G)
                lines += [f"## trend_groups: 4 complementary trend groups, {_cz.AGREE} of 4 must agree, calibrated {ca}..{cb} (daily zd, interquartile bands)"]
                fb = lambda r: "n/a" if r is None else f"{r[0]:+.2f}..{r[1]:+.2f}"   # noqa: E731
                for g, r in G.items():
                    lines.append(f"- {g.upper()} ({', '.join(_cz.TREND_GROUPS[g])}): buy {fb(r['buy'])} | sell {fb(r['sell'])} | long TP {fb(r['tp_long'])} | short TP {fb(r['tp_short'])}")
                if L is None:
                    lines.append("- fewer than 3 groups have every zone: no test")
                else:
                    bb, sb = _cz.agree_band(L["buy"]), _cz.agree_band(L["sell"])
                    lines.append(f"- 3-of-4 BUY band {fb(bb)} | 3-of-4 SELL band {fb(sb)} | take profit long at zd >= {L['tp_long_at']:+.2f}, short at <= {L['tp_short_at']:+.2f} | "
                                 f"out early long at <= {L['bail_long_at']:+.2f}, short at >= {L['bail_short_at']:+.2f}")
                    lines.append("### Today's agreed zones in price (20-day average + zd x daily ATR14)")
                    for i in MAJORS:
                        d = _cz.daily_from_h1(fx_mid(h1[i]))
                        pc = d["close"].shift(1)
                        atr = float(pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1).rolling(14).mean().iloc[-2])
                        ma = float(d["close"].rolling(20).mean().iloc[-2])
                        p_ = lambda v: f"{ma + v * atr:.5g}"   # noqa: E731
                        lines.append(f"- {i}: last {float(fx_mid(h1[i])['close'].iloc[-1]):.5g} | BUY {p_(bb[0]) + '-' + p_(bb[1]) if bb else 'n/a'} TP {p_(L['tp_long_at'])} "
                                     f"out {p_(L['bail_long_at'])} | SELL {p_(sb[0]) + '-' + p_(sb[1]) if sb else 'n/a'} TP {p_(L['tp_short_at'])} out {p_(L['bail_short_at'])}")
                    for label, a in (("last 21 days", today - _dt.timedelta(days=21)), ("last 92 days", t3a)):
                        lines.append(f"### {label} ({a}..{today}), 7 majors, 1-hour bid/ask, financing")
                        for sm in (1.0, 2.0):
                            tr = [t for i in MAJORS for t in _cz.trade_agree(_spread(h1[i], sm), L, a, today, i)]
                            why = {w: sum(1 for t in tr if t["why"] == w) for w in ("tp", "sell zone", "stop", "time", "end")}
                            lines.append(f"- spread x{sm:g}: all {_cz.fmt(_cz.summary(tr))} | longs {_cz.fmt(_cz.summary([t for t in tr if t['side'] == 1]))} | "
                                         f"shorts {_cz.fmt(_cz.summary([t for t in tr if t['side'] == -1]))} | exits {why}")
                        plc = [_cz.summary([t for i in MAJORS for t in _cz.trade_agree(shuffled_bidask(h1[i], k), L, a, today, i)]) for k in range(3)]
                        lines.append("  placebo (shuffled bars, 3 seeds): " + " | ".join(f"{p['trades']}t avg {p['avg_r']:+.3f}R net {p['net_r']:+.1f}R" for p in plc))
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "trend_tp_zones":            # owner 2026-10-28: trend experts' buy / sell / take-profit zones, 3-week test (fxbot.consensus)
                import datetime as _dt
                import numpy as np
                import pandas as pd
                from fxbot import consensus as _cz
                from fxbot.data import MAJORS, load as fx_load, mid as fx_mid
                from fxbot.exitlab import _spread
                from fxbot.fxexp_check import shuffled_bidask
                today = _dt.date.today()
                ta = today - _dt.timedelta(days=21)
                ca, cb = ta - _dt.timedelta(days=365), ta - _dt.timedelta(days=1)
                cache_ = Path("state/fx/cache")
                gmin = {"H1": 60, "H4": 240, "D": 1440}
                h1 = {i: fx_load(i, "H1", ca - _dt.timedelta(days=60), today, cache_) for i in MAJORS}
                bars = {g: {i: (h1[i] if g == "H1" else fx_load(i, g, ca - _dt.timedelta(days=300 if g == "D" else 60), today, cache_)) for i in MAJORS} for g in ("H1", "H4", "D")}
                zd = {i: _cz.zscore_daily(fx_mid(d))[0] for i, d in h1.items()}
                sig, tps = {}, {}
                for name, (make, g) in _cz.experts().items():
                    if name not in _cz.TRENDING:
                        continue
                    lz, sz_, tl, ts = [], [], [], []
                    for i in MAJORS:
                        try:
                            a_, b_ = _cz.signal_z(make, bars[g][i], zd[i], ca, cb, gmin[g])
                            c_, d_ = _cz.tp_zd(make, bars[g][i], zd[i], ca, cb)
                        except Exception as exc:
                            print(f"skip {name} {i}: {exc}", flush=True)
                            continue
                        lz += a_; sz_ += b_; tl += c_; ts += d_
                    sig[name], tps[name] = (lz, sz_), (tl, ts)
                Z = _cz.trend_tp_zones(sig, tps)
                lines += [f"## trend_tp_zones: TREND experts' buy / sell / take-profit zones (daily zd), calibrated {ca}..{cb}, 3-week test {ta}..{today}"]
                for name in sig:
                    md = lambda x: f"{np.median(x):+.2f} ({len(x)})" if len(x) >= _cz.MIN_SIGNALS else f"too few ({len(x)})"   # noqa: E731
                    lines.append(f"- {name}: buys {md(sig[name][0])} | sells {md(sig[name][1])} | winning longs exit {md(tps[name][0])} | winning shorts exit {md(tps[name][1])}")
                fz = lambda k: (f"{Z[k][0][0]:+.2f}..{Z[k][0][1]:+.2f} ({Z[k][1]} experts)" if Z[k][0] else "n/a")   # noqa: E731
                lines.append(f"- BUY ZONE {fz('buy')} | SELL ZONE {fz('sell')} | LONG TP ZONE {fz('tp_long')} | SHORT TP ZONE {fz('tp_short')}")
                bz, sz, tpl, tps_ = Z["buy"][0], Z["sell"][0], Z["tp_long"][0], Z["tp_short"][0]
                if all((bz, sz, tpl, tps_)):
                    lines.append("### Today's zones in price (20-day average + zd x daily ATR14, last completed day)")
                    for i in MAJORS:
                        d = _cz.daily_from_h1(fx_mid(h1[i]))
                        pc = d["close"].shift(1)
                        atr = float(pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1).rolling(14).mean().iloc[-2])
                        ma = float(d["close"].rolling(20).mean().iloc[-2])
                        pz = lambda zz: f"{ma + zz[0] * atr:.5g}-{ma + zz[1] * atr:.5g}"   # noqa: E731
                        lines.append(f"- {i}: last {float(fx_mid(h1[i])['close'].iloc[-1]):.5g} | buy {pz(bz)} -> TP {pz(tpl)} | sell {pz(sz)} -> TP {pz(tps_)}")
                    lines.append(f"### 3-week test {ta}..{today}, 7 majors, 1-hour bid/ask, financing, stop {_cz.DAILY_STOP_ATR} daily ATR")
                    for sm in (1.0, 2.0):
                        tr = [t for i in MAJORS for t in _cz.trade_trend_tp(_spread(h1[i], sm), bz, sz, tpl, tps_, ta, today, i)]
                        why = {w: sum(1 for t in tr if t["why"] == w) for w in ("tp", "sell zone", "stop", "time", "end")}
                        lines.append(f"- spread x{sm:g}: all {_cz.fmt(_cz.summary(tr))} | longs {_cz.fmt(_cz.summary([t for t in tr if t['side'] == 1]))} | "
                                     f"shorts {_cz.fmt(_cz.summary([t for t in tr if t['side'] == -1]))} | exits {why}")
                        if sm == 1.0:
                            lines.append("  by pair: " + " | ".join(f"{i} {sum(t['r'] for t in tr if t['pair'] == i):+.1f}R ({sum(1 for t in tr if t['pair'] == i)})" for i in MAJORS))
                    plc = [_cz.summary([t for i in MAJORS for t in _cz.trade_trend_tp(shuffled_bidask(h1[i], k), bz, sz, tpl, tps_, ta, today, i)]) for k in range(3)]
                    lines.append("  placebo (shuffled bars, 3 seeds): " + " | ".join(f"{p['trades']}t avg {p['avg_r']:+.3f}R net {p['net_r']:+.1f}R" for p in plc))
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "consensus_daily_wf":        # owner 2026-10-28: rolling walk-forward of the daily-range trend / reversion zones (fxbot.consensus)
                import datetime as _dt
                import numpy as np
                import pandas as pd
                from fxbot import consensus as _cz
                from fxbot.data import MAJORS, load as fx_load, mid as fx_mid
                from fxbot.exitlab import _spread
                from fxbot.fxexp_check import shuffled_bidask
                today = _dt.date.today()
                wf_first = _dt.date(2022, 1, 1)
                start = wf_first - _dt.timedelta(days=365 + 60)
                cache_ = Path("state/fx/cache")
                gmin = {"H1": 60, "H4": 240, "D": 1440}
                h1 = {i: fx_load(i, "H1", start, today, cache_) for i in MAJORS}
                bars = {g: {i: (h1[i] if g == "H1" else fx_load(i, g, start - _dt.timedelta(days=300 if g == "D" else 60), today, cache_)) for i in MAJORS} for g in ("H1", "H4", "D")}
                zd = {i: _cz.zscore_daily(fx_mid(d))[0] for i, d in h1.items()}
                sig = {}
                for name, (make, g) in _cz.experts().items():
                    ls, ss = [], []
                    for i in MAJORS:
                        try:
                            lt, st = _cz.signal_times(make, bars[g][i], gmin[g])
                        except Exception as exc:
                            print(f"skip {name} {i}: {exc}", flush=True)
                            continue
                        ls.append(_cz.z_at(lt, zd[i]))
                        ss.append(_cz.z_at(st, zd[i]))
                    sig[name] = (pd.concat(ls) if ls else pd.Series(dtype=float), pd.concat(ss) if ss else pd.Series(dtype=float))
                shuf = {k: {i: shuffled_bidask(h1[i], k) for i in MAJORS} for k in range(3)}
                lines += [f"## consensus_daily_wf: rolling walk-forward, zones re-set each quarter from the 12 months before it, {wf_first}..{today}, 7 majors, 1-hour bid/ask"]
                tot = {g: {"1x": [], "2x": [], "plc": {k: [] for k in range(3)}, "q": []} for g in ("trend", "reversion")}
                ma2 = []                                 # the plain 20-day-average trend rule, 2x spread (control for the trend zones)
                for qa, qb in _cz.quarters(wf_first, today):
                    ca, cb = qa - _dt.timedelta(days=365), qa - _dt.timedelta(days=1)
                    sel = lambda s_: [float(v) for t, v in s_.items() if ca <= t.date() <= cb]   # noqa: E731
                    per = {n: (sel(l_), sel(s_)) for n, (l_, s_) in sig.items()}
                    G = _cz.group_zones(per)
                    row = []
                    win = lambda d_: d_[(d_.index >= pd.Timestamp(qa - _dt.timedelta(days=45), tz="UTC")) & (d_.index <= pd.Timestamp(qb + _dt.timedelta(days=30), tz="UTC"))]   # noqa: E731
                    for grp in ("trend", "reversion"):
                        bz, sz = G[grp]["buy"], G[grp]["sell"]
                        if not (bz and sz):
                            row.append(f"{grp}: no zones")
                            continue
                        t1 = [t for i in MAJORS for t in _cz.trade_group(win(h1[i]), grp, bz, sz, qa, qb, i)]
                        t2 = [t for i in MAJORS for t in _cz.trade_group(_spread(win(h1[i]), 2.0), grp, bz, sz, qa, qb, i)]
                        tot[grp]["1x"] += t1
                        tot[grp]["2x"] += t2
                        tot[grp]["q"].append((qa, sum(t["r"] for t in t2)))
                        for k in range(3):
                            tot[grp]["plc"][k] += [t for i in MAJORS for t in _cz.trade_group(win(shuf[k][i]), grp, bz, sz, qa, qb)]
                        s2 = _cz.summary(t2)
                        row.append(f"{grp} buy {bz[0]:+.2f}..{bz[1]:+.2f} sell {sz[0]:+.2f}..{sz[1]:+.2f}: {s2['trades']}t net {s2['net_r']:+.1f}R at 2x")
                    mq = [t for i in MAJORS for t in _cz.trade_group(_spread(win(h1[i]), 2.0), "trend", _cz.MA_RULE[0], _cz.MA_RULE[1], qa, qb, i)]
                    ma2 += mq
                    row.append(f"plain MA rule {len(mq)}t net {sum(t['r'] for t in mq):+.1f}R at 2x")
                    lines.append(f"- {qa:%Y} Q{(qa.month - 1) // 3 + 1}: " + " | ".join(row))
                for grp, T in tot.items():
                    s1, s2 = _cz.summary(T["1x"]), _cz.summary(T["2x"])
                    plc = [_cz.summary(v)["avg_r"] for v in T["plc"].values()]
                    qpos = sum(1 for _, r in T["q"] if r > 0)
                    last4 = sum(r for _, r in T["q"][-4:])
                    sma2 = _cz.summary(ma2)
                    ok = (s2["trades"] >= 100 and (s2["pf"] or 0) >= 1.15 and qpos > len(T["q"]) / 2 and last4 > 0 and all(s2["avg_r"] > p for p in plc)
                          and (grp != "trend" or s2["avg_r"] > sma2["avg_r"]))
                    lines.append(f"### {grp.upper()}: normal spread {_cz.fmt(s1)} | 2x spread {_cz.fmt(s2)} | quarters profitable at 2x {qpos}/{len(T['q'])} | "
                                 f"last 4 quarters {last4:+.1f}R | placebo avg R {', '.join(f'{p:+.3f}' for p in plc)} | {'PASS' if ok else 'reject'}")
                    lines.append(f"  {grp} at 2x: longs {_cz.fmt(_cz.summary([t for t in T['2x'] if t['side'] == 1]))} | shorts {_cz.fmt(_cz.summary([t for t in T['2x'] if t['side'] == -1]))} | "
                                 f"avg nights held {np.mean([t['nights'] for t in T['2x']]) if T['2x'] else 0:.1f}")
                    lines.append(f"  {grp} at 2x by pair: " + " | ".join(f"{i} {sum(t['r'] for t in T['2x'] if t['pair'] == i):+.1f}R ({sum(1 for t in T['2x'] if t['pair'] == i)})" for i in MAJORS))
                lines.append(f"### CONTROL plain 20-day-average rule (long above / short below, same stop, 2x): {_cz.fmt(_cz.summary(ma2))}")
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "consensus_daily":           # owner 2026-10-28: separate TREND and REVERSION zones on the daily range (fxbot.consensus block 2)
                import datetime as _dt
                import numpy as np
                import pandas as pd
                from fxbot import consensus as _cz
                from fxbot.data import MAJORS, load as fx_load, mid as fx_mid
                from fxbot.exitlab import _spread
                from fxbot.fxexp_check import shuffled_bidask
                today = _dt.date.today()
                t3a = today - _dt.timedelta(days=92)
                ca, cb = t3a - _dt.timedelta(days=365), t3a - _dt.timedelta(days=1)
                cache_ = Path("state/fx/cache")
                warm = {"H1": 60, "H4": 120, "D": 400}
                gmin = {"H1": 60, "H4": 240, "D": 1440}
                h1 = {i: fx_load(i, "H1", ca - _dt.timedelta(days=60), today, cache_) for i in MAJORS}
                zd = {i: _cz.zscore_daily(fx_mid(d))[0] for i, d in h1.items()}
                bars = {g: {i: (h1[i] if g == "H1" else fx_load(i, g, ca - _dt.timedelta(days=warm[g]), today, cache_)) for i in MAJORS} for g in ("H1", "H4", "D")}
                per = {}
                for name, (make, g) in _cz.experts().items():
                    lz, sz_ = [], []
                    for i in MAJORS:
                        try:
                            a_, b_ = _cz.signal_z(make, bars[g][i], zd[i], ca, cb, gmin[g])
                        except Exception as exc:
                            print(f"skip {name} {i}: {exc}", flush=True)
                            continue
                        lz += a_
                        sz_ += b_
                    per[name] = (lz, sz_)
                G = _cz.group_zones(per)
                lines += [f"## consensus_daily: separate trend / reversion zones on the DAILY range, calibrated {ca}..{cb} (zd = (close - 20-day avg) / daily ATR14)"]
                lines += ["### Each expert on the daily yardstick (median zd at its buys | sells, signals)"]
                for name, (lz, sz_) in per.items():
                    tag = "TREND" if name in _cz.TRENDING else "reversion"
                    f_ = lambda x: f"{np.median(x):+.2f} ({len(x)})" if len(x) >= _cz.MIN_SIGNALS else f"too few ({len(x)})"   # noqa: E731
                    lines.append(f"- {name} [{tag}]: buys {f_(lz)} | sells {f_(sz_)}")
                for grp, zz in G.items():
                    bz, sz = zz["buy"], zz["sell"]
                    lines.append(f"- {grp.upper()} zones: buy zd {bz[0]:+.2f}..{bz[1]:+.2f} ({zz['n_buy']} experts) | sell zd {sz[0]:+.2f}..{sz[1]:+.2f} ({zz['n_sell']} experts)"
                                 if bz and sz else f"- {grp}: not enough experts")
                lines.append("### Today's zones in price (20-day average + zd x daily ATR14, last completed day)")
                for i in MAJORS:
                    d = _cz.daily_from_h1(fx_mid(h1[i]))
                    pc = d["close"].shift(1)
                    atr = float(pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1).rolling(14).mean().iloc[-2])
                    ma = float(d["close"].rolling(20).mean().iloc[-2])
                    last = float(fx_mid(h1[i])["close"].iloc[-1])
                    pz = lambda zz: f"{ma + zz[0] * atr:.5g}-{ma + zz[1] * atr:.5g}" if zz else "n/a"   # noqa: E731
                    lines.append(f"- {i}: last {last:.5g} | reversion buy {pz(G['reversion']['buy'])} sell {pz(G['reversion']['sell'])} | "
                                 f"trend buy {pz(G['trend']['buy'])} sell {pz(G['trend']['sell'])}")
                for label, (ta, tb) in (("last 14 days", (today - _dt.timedelta(days=14), today)), ("last 92 days (context)", (t3a, today))):
                    lines.append(f"### Retest, {label} ({ta}..{tb}), 7 majors, 1-hour bid/ask, stop {_cz.DAILY_STOP_ATR} daily ATR, max 20 days")
                    for grp in ("reversion", "trend"):
                        bz, sz = G[grp]["buy"], G[grp]["sell"]
                        if not (bz and sz):
                            continue
                        ok = (bz[1] < sz[0]) if grp == "reversion" else (sz[1] < bz[0])
                        if not ok:
                            lines.append(f"- {grp}: zones do not line up (buy {bz[0]:+.2f}..{bz[1]:+.2f}, sell {sz[0]:+.2f}..{sz[1]:+.2f}): no trades")
                            continue
                        for sm in (1.0, 2.0):
                            tr = [t for i in MAJORS for t in _cz.trade_group(_spread(h1[i], sm), grp, bz, sz, ta, tb)]
                            lines.append(f"- {grp} | spread x{sm:g}: all {_cz.fmt(_cz.summary(tr))} | longs {_cz.fmt(_cz.summary([t for t in tr if t['side'] == 1]))} | "
                                         f"shorts {_cz.fmt(_cz.summary([t for t in tr if t['side'] == -1]))}")
                        plc = [_cz.summary([t for i in MAJORS for t in _cz.trade_group(shuffled_bidask(h1[i], k), grp, bz, sz, ta, tb)]) for k in range(3)]
                        lines.append(f"  {grp} placebo (shuffled bars, 3 seeds): " + " | ".join(f"{p['trades']}t avg {p['avg_r']:+.3f}R net {p['net_r']:+.1f}R" for p in plc))
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "consensus_zones":           # owner 2026-10-28: average where all experts buy / trending experts sell, retest 2 weeks (fxbot.consensus)
                import datetime as _dt
                import numpy as _np
                import pandas as pd
                from fxbot import consensus as _cz
                from fxbot.data import MAJORS, load as fx_load, mid as fx_mid
                from fxbot.fxexp_check import shuffled_bidask
                today = _dt.date.today()
                w = _cz.windows(today)
                (ca, cb), (ta, tb) = w["calibrate"], w["test"]
                cache_ = Path("state/fx/cache")
                warm = {"H1": 40, "H4": 120, "D": 400}
                gmin = {"H1": 60, "H4": 240, "D": 1440}
                h1 = {i: fx_load(i, "H1", ca - _dt.timedelta(days=40), today, cache_) for i in MAJORS}
                z1 = {i: _cz.zscore(fx_mid(d)) for i, d in h1.items()}
                bars = {g: {i: (h1[i] if g == "H1" else fx_load(i, g, ca - _dt.timedelta(days=warm[g]), today, cache_)) for i in MAJORS} for g in ("H1", "H4", "D")}
                per = {}
                for name, (make, g) in _cz.experts().items():
                    lz, sz_ = [], []
                    for i in MAJORS:
                        try:
                            a_, b_ = _cz.signal_z(make, bars[g][i], z1[i], ca, cb, gmin[g])
                        except Exception as exc:
                            print(f"skip {name} {i}: {exc}", flush=True)
                            continue
                        lz += a_
                        sz_ += b_
                    per[name] = (lz, sz_)
                Z = _cz.zones(per)
                lines += [f"## consensus_zones: calibrated {ca}..{cb}, retest {ta}..{tb} (1-hour z = (close - 20h avg) / ATR14; fxbot.consensus)"]
                lines += ["### Each expert: where it buys / sells (z: 25th pct / median / 75th pct, signals)"]
                for name, r in Z["experts"].items():
                    b = r.get("buy")
                    s_ = r.get("sell")
                    tag = "TREND" if name in _cz.TRENDING else "reversion/range"
                    lines.append(f"- {name} [{tag}]: buys " + (f"{b[0]:+.2f} / {b[1]:+.2f} / {b[2]:+.2f} ({b[3]})" if b else f"too few ({len(per[name][0])})")
                                 + (f" | sells {s_[0]:+.2f} / {s_[1]:+.2f} / {s_[2]:+.2f} ({s_[3]})" if s_ else ""))
                bz, sz, sza = Z["buy_zone"], Z["sell_zone"], Z["sell_zone_all"]
                lines.append(f"- OPTIMAL BUY ZONE (avg of {Z['n_buy_experts']} experts): z {bz[0]:+.2f} to {bz[1]:+.2f}" if bz else "- no buy zone")
                lines.append(f"- OPTIMAL SELL ZONE, trending experts (avg of {Z['n_sell_experts']}): z {sz[0]:+.2f} to {sz[1]:+.2f}" if sz else "- no trend sell zone")
                lines.append(f"- variant B sell zone, all experts (avg of {Z['n_sell_all']}): z {sza[0]:+.2f} to {sza[1]:+.2f}" if sza else "- no all-expert sell zone")
                lines.append("### Today's zones in price (20-hour average + z x ATR14 on the last 1-hour bar)")
                for i in MAJORS:
                    m = fx_mid(h1[i])
                    c = m["close"]
                    pc = c.shift(1)
                    atr = float(pd.concat([m["high"] - m["low"], (m["high"] - pc).abs(), (m["low"] - pc).abs()], axis=1).max(axis=1).rolling(14).mean().iloc[-1])
                    ma = float(c.rolling(20).mean().iloc[-1])
                    pz = lambda zz: f"{ma + zz[0] * atr:.5g} - {ma + zz[1] * atr:.5g}" if zz else "n/a"   # noqa: E731
                    lines.append(f"- {i}: last {float(c.iloc[-1]):.5g} | buy zone {pz(bz)} | sell zone (trend) {pz(sz)} | sell zone (all) {pz(sza)}")
                lines.append(f"### Retest, last 14 days ({ta}..{tb}), 7 majors, 1-hour bid/ask, stop {_cz.STOP_ATR} ATR, max {_cz.MAX_BARS} bars")
                for label, sell in (("A: buy zone -> trend sell zone", sz), ("B: buy zone -> all-expert sell zone", sza)):
                    if not (bz and sell):
                        continue
                    if bz[1] >= sell[0]:
                        lines.append(f"- {label}: the sell zone ({sell[0]:+.2f}..{sell[1]:+.2f}) is not above the buy zone ({bz[0]:+.2f}..{bz[1]:+.2f}): nothing to sell into, no trades")
                        continue
                    for sm in (1.0, 2.0):
                        from fxbot.exitlab import _spread
                        tr = [t for i in MAJORS for t in _cz.trade_zones(_spread(h1[i], sm), bz, sell, ta, tb)]
                        lng = _cz.summary([t for t in tr if t["side"] == 1])
                        sht = _cz.summary([t for t in tr if t["side"] == -1])
                        lines.append(f"- {label} | spread x{sm:g}: all {_cz.fmt(_cz.summary(tr))} | longs {_cz.fmt(lng)} | shorts {_cz.fmt(sht)}")
                    plc = []
                    for k in range(3):
                        tr = [t for i in MAJORS for t in _cz.trade_zones(shuffled_bidask(h1[i], k), bz, sell, ta, tb)]
                        plc.append(_cz.summary(tr)["avg_r"])
                    lines.append(f"  placebo (bars shuffled, 3 seeds): avg R {', '.join(f'{x:+.3f}' for x in plc)}")
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "direction_check":           # owner 2026-10-28: "it's got to be a buy and sell issue" (research.direction_pooled)
                import datetime as _dt
                import numpy as _np
                from fxbot import live as _fxlive
                from fxbot.data import MAJORS, load as fx_load, mid as fx_mid
                from .funnel import frame as funnel_frame
                from .research import direction_pooled
                from .strategies import SectorTopDown
                from .universe import SECTOR_OF
                today = _dt.date.today()
                lines += ["## direction_check: do the entry signals point the right way? signed forward return (+ = the signal's way), hit rate, mean in ATR, z vs 50% (research.direction_pooled)"]
                lines += ["### OANDA strategies, last 3 years, 7 majors (all signals pooled)"]
                horizons = {"H1": (1, 6, 24), "H4": (1, 6, 18), "D": (1, 5, 10)}
                for name, spec in _fxlive.STRATS.items():
                    if not spec.get("make"):
                        continue
                    parts = []
                    for inst in MAJORS:
                        m = fx_mid(fx_load(inst, spec["gran"], today - _dt.timedelta(days=3 * 365 + 120), today, Path("state/fx/cache")))
                        f = spec["make"]().frame(m)
                        parts.append((m, f["long"].fillna(False).to_numpy(bool), f["short"].fillna(False).to_numpy(bool)))
                    lines += direction_pooled(name, parts, horizons[spec["gran"]])
                lines += ["### Stock swing strategies, 2016 to now (today's list, longs)"]
                spy = load("SPY", "1Day", date(2015, 1, 2), today, cache)
                rules = {"funnel": {"stop_frac": 0.5, "tp_r": 1.25, "v_tail": True}, "oversold": {"oversold": True, "os_stage": None, "tp_r": 1.25, "stop_frac": 0.75}}
                stock_parts = {"Sector Top-Down": [], "funnel": [], "oversold": []}
                for sym in SECTOR_OF:
                    d = load(sym, "1Day", date(2015, 1, 2), today, cache)
                    if len(d) < 300:
                        continue
                    stock_parts["Sector Top-Down"].append((d, SectorTopDown(spy["close"]).frame(d)["entry"].fillna(False).to_numpy(bool), _np.zeros(len(d), bool)))
                    for k in ("funnel", "oversold"):
                        stock_parts[k].append((d, funnel_frame(d, rules[k])["long_entry"].fillna(False).to_numpy(bool), _np.zeros(len(d), bool)))
                for k, parts in stock_parts.items():
                    lines += direction_pooled(k, parts, (1, 5, 20))
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "fxexp_rest":                # owner 2026-10-28 ("I refuse to believe that's all experts failed"): the bot's other DAILY systems, staged then long
                import datetime as _dt
                import numpy as np
                from fxbot import fxexp_check as _fc
                from fxbot.data import load as fx_load
                today = _dt.date.today()
                _, _, instr, _ = _fc._bot()
                cache_ = Path("state/fx/cache")
                sids = ("lien-fader", "raschke-soup", "bollinger-squeeze", "crabel-idnr4", "ponsi-trend")
                short = {p_: _fc._mid_frame(fx_load(p_, "H1", today - _dt.timedelta(days=92 + 160), today, cache_)) for p_ in instr.ALL_PAIRS}
                lines += ["## fxexp_rest: the other daily fx-experts systems, staged short tests first (2x spread, shuffled-data placebo), long test only for a system that clears all three stages"]
                go = []
                for sid in sids:
                    t2 = _fc.daily_trades(sid, short, 2.0)
                    plc = [_fc.daily_trades(sid, {p_: _fc.shuffled_mid(short[p_], k) for p_ in short}, 2.0) for k in range(3)]
                    rows = _fc.stage_table(t2, plc, today)
                    lines += _fc.stage_lines(sid, rows)
                    if all(r["pass"] for r in rows):
                        go.append(sid)
                lines.append(f"- systems cleared for the long test: {go or 'none'}")
                if go:
                    h1 = {p_: _fc._mid_frame(fx_load(p_, "H1", today - _dt.timedelta(days=5 * 365 + 150), today, cache_)) for p_ in instr.ALL_PAIRS}
                    wd = _fc.windows(today, True)
                    for sid in go:
                        best = []
                        for seed in range(5):
                            tr_ = [t for t in _fc.daily_trades(sid, {p_: _fc.shuffled_mid(h1[p_], seed) for p_ in h1}) if wd["OOS last 1y"][0] <= t["date"] <= wd["OOS last 1y"][1]]
                            if tr_:
                                best.append(float(np.mean([t["r"] for t in tr_])))
                        ref = float(np.max(best)) if best else None
                        lines += _fc.lines(sid, _fc.evaluate(_fc.daily_trades(sid, h1), _fc.daily_trades(sid, h1, 2.0), wd, ref), ref)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "fxexp_check":               # owner 2026-10-27: "test the four fx-experts strategies" (fxbot.fxexp_check, bot code in forex_bots/fx-experts)
                import datetime as _dt
                import numpy as np
                from fxbot import fxexp_check as _fc
                from fxbot.data import load as fx_load
                today = _dt.date.today()
                _, _, instr, _ = _fc._bot()
                cache_ = Path("state/fx/cache")
                h1 = {p_: _fc._mid_frame(fx_load(p_, "H1", today - _dt.timedelta(days=5 * 365 + 150), today, cache_)) for p_ in instr.ALL_PAIRS}
                wd = _fc.windows(today, True)
                lines += ["## fxexp_check: the fx-experts bot's four strategies, 3y / 1y / 1y windows, bot's own engine and costs (H1 mid), pre-declared bar (fxbot.fxexp_check)"]
                evs, refs = {}, {}
                for sid in ("tharp-random", "williams-vbo", "faith-turtle"):
                    plc = []
                    for seed in range(5):
                        hs = {p_: _fc.shuffled_mid(h1[p_], seed) for p_ in h1}
                        tr_ = [t for t in _fc.daily_trades(sid, hs) if wd["OOS last 1y"][0] <= t["date"] <= wd["OOS last 1y"][1]]
                        if tr_:
                            plc.append(float(np.mean([t["r"] for t in tr_])))
                    refs[sid] = float(np.max(plc)) if plc else None
                    evs[sid] = _fc.evaluate(_fc.daily_trades(sid, h1), _fc.daily_trades(sid, h1, 2.0), wd, refs[sid])
                    lines += _fc.lines(sid, evs[sid], refs[sid])
                wz = _fc.windows(today, False)
                z1, z2, zp = [], [], {k: [] for k in range(3)}
                for p_ in instr.PAIRS:
                    m5 = fx_load(p_, "M5", today - _dt.timedelta(days=3 * 365 + 10), today, cache_)
                    z1 += _fc.zeros_trades(m5, p_)
                    z2 += _fc.zeros_trades(m5, p_, 2.0)
                    for k in zp:
                        zp[k] += _fc.zeros_trades(_fc.shuffled_bidask(m5, k), p_)
                a_, b_ = wz["OOS last 1y"]
                pl = [float(np.mean([t["r"] for t in v if a_ <= t["date"] <= b_])) for v in zp.values() if any(a_ <= t["date"] <= b_ for t in v)]
                refz = float(np.max(pl)) if pl else None
                lines += _fc.lines("lien-zeros", _fc.evaluate(z1, z2, wz, refz), refz)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "fx_exits":                  # owner 2026-10-27: every exit type on every OANDA strategy, short test then long test (fxbot.exitlab)
                import datetime as _dt
                from fxbot import exitlab as _fxl
                from fxbot import live as _fxlive
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                today = _dt.date.today()
                strats = {k: (v["make"], v["gran"]) for k, v in _fxlive.STRATS.items() if v.get("make")}
                warm = {"H1": 60, "H4": 150, "D": 400}
                grans = sorted({g for _, g in strats.values()})
                dg = {g: {i: fx_load(i, g, today - _dt.timedelta(days=92 + warm[g]), today, Path("state/fx/cache")) for i in MAJORS} for g in grans}
                sh = _fxl.short_test(strats, dg, today)
                lines += ["## fx_exits SHORT test: last 3 months, halves at 2x spread, bid/ask, 7 majors (live = 0.5R cut / 1.25R target)"] + _fxl.short_lines(sh)
                picks = _fxl.best_passers(sh)
                lines.append(f"- passers taken to the long test: {picks}")
                if any(picks.values()):
                    need = sorted({strats[k][1] for k, v in picks.items() if v})
                    dl = {g: {i: fx_load(i, g, today - _dt.timedelta(days=93 + 365 * 5 + warm[g]), today, Path("state/fx/cache")) for i in MAJORS} for g in need}
                    lines += ["## fx_exits LONG test: 5 years up to the short window, last 12 months sequestered, 2x spread"]
                    lines += _fxl.long_lines(_fxl.long_test(strats, dl, today, picks))
                else:
                    lines.append("- nothing passed the short test: no long test")
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "strict_fx":                 # owner + Gemini r5 promotion bar for methods_fx candidates (3y train / 1y select / 1y OOS)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import strict_check
                today = _dt.date.today()
                dg = {g: {i: fx_load(i, g, today - _dt.timedelta(days=365 * 5 + 150), today, Path("state/fx/cache")) for i in MAJORS} for g in ("H4",)}
                lines += ["## strict_fx: 3y train / 1y select / 1y OOS, bid/ask, 7 majors"] + strict_check(
                    dg, [("inside_bar_breakout", "H4"), ("carter_ttm_squeeze", "H4"), ("raschke_holy_grail", "H4")], today)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "methods_fx":                # owner 2026-10-27: book day-trade / scalp / swing methods (fxbot.methods) on M5..D, train 12 months, test the last 3
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import methods_check
                today = _dt.date.today()
                test_ = (today - _dt.timedelta(days=92), today)
                train_ = (today - _dt.timedelta(days=92 + 365), today - _dt.timedelta(days=93))
                warm = {"M5": 10, "M15": 20, "H1": 60, "H4": 150, "D": 400}
                dg = {g: {i: fx_load(i, g, train_[0] - _dt.timedelta(days=warm[g]), today, Path("state/fx/cache")) for i in MAJORS} for g in warm}
                lines += [f"## methods_fx: book methods, train {train_[0]}..{train_[1]}, TEST {test_[0]}..{test_[1]} (bid/ask, 7 majors)"] + methods_check(dg, train_, test_)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "expert_tune_fx":            # owner 2026-10-27: every OANDA expert x H1/H4/D x 13 variants, chosen on 12 months, confirmed on the last 3
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import expert_tune
                today = _dt.date.today()
                test_ = (today - _dt.timedelta(days=92), today)
                train_ = (today - _dt.timedelta(days=92 + 365), today - _dt.timedelta(days=93))
                dg = {g: {i: fx_load(i, g, train_[0] - _dt.timedelta(days=(400 if g == "D" else 150)), today, Path("state/fx/cache")) for i in MAJORS}
                      for g in ("H1", "H4", "D")}
                lines += [f"## expert_tune_fx: chosen on {train_[0]}..{train_[1]}, confirmed on {test_[0]}..{test_[1]} (bid/ask, 7 majors)"] + expert_tune(dg, train_, test_)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "fx_carry":                  # Gemini round 4 (2026-10-27): 200-day trend-filtered carry on the 7 majors (fxbot.research.carry_trend_study)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.data import mid as fx_mid
                from fxbot.research import carry_trend_study, fred_rates
                today = _dt.date.today()
                try:
                    rates = fred_rates()
                except Exception as exc:
                    lines += [f"## fx_carry: could not fetch the FRED rate series ({str(exc)[:120]}); nothing run"]
                    print("\n".join(lines[first:]), flush=True)
                    continue
                cl = {i: fx_mid(fx_load(i, "D", _dt.date(2004, 1, 1), today, Path("state/fx/cache")))["close"] for i in MAJORS}
                lines += [f"## fx_carry: rates from FRED, daily bars {min(d.index[0] for d in cl.values()).date()} to {today}; train to 2015-12-31, validate 2016 on"]
                for drag in (0.0, 0.015):
                    lines += [f"(rollover drag {drag:.1%} a year)"] + carry_trend_study(cl, rates, _dt.date(2015, 12, 31), drag=drag)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "gap_fade_fx":               # Gemini idea 3 (2026-10-26): fade a big Asian-session move at the London open
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import gap_fade_check
                today = _dt.date.today()
                for label, days_ in (("last 3 months", 92), ("last 12 months", 365), ("last 3 years", 1095)):
                    a_ = today - _dt.timedelta(days=days_)
                    h1 = {i: fx_load(i, "H1", a_ - _dt.timedelta(days=60), today, Path("state/fx/cache")) for i in MAJORS}
                    lines += [f"## gap_fade_fx: London-open fade of the Asian move, {label} ({a_} to {today}, H1, bid/ask)"] + gap_fade_check(h1, a_, today)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "daily_anomalies":           # Gemini round 2 (2026-10-26): five multi-day ideas on daily bars (research.daily_anomalies_study)
                import datetime as _dt
                from .research import daily_anomalies_study
                from .universe import SECTOR_ETFS, STOCKS
                syms = sorted(set(STOCKS) | set(SECTOR_ETFS) | {"SPY"})
                dd = {s: load(s, "1Day", _dt.date(2014, 1, 1), _dt.date.today(), cache) for s in syms}
                dd = {s: d for s, d in dd.items() if len(d) > 300}
                lines += ["## daily_anomalies: Gemini round-2 ideas on daily bars (train to 2020-12-31, validate 2021 on; costs 5 bps/side stocks, 2 bps ETFs)"]
                lines += daily_anomalies_study(dd, [s for s in SECTOR_ETFS if s in dd], _dt.date(2020, 12, 31))
                # survivorship check (owner 2026-10-26): the same five on the hindsight-free list, 2016's largest companies (many laggards since)
                cd = {s: load(s, "1Day", _dt.date(2014, 1, 1), _dt.date.today(), cache) for s in SECTOR_TD_CONTROL}
                cd = {s: d for s, d in cd.items() if len(d) > 300}
                keep = {s: d for s, d in dd.items() if s in SECTOR_ETFS or s == "SPY"}
                lines += ["## daily_anomalies on the HINDSIGHT-FREE list (2016's 21 largest companies, no look-ahead on today's winners; A and B repeat)"]
                lines += daily_anomalies_study({**keep, **cd}, [s for s in SECTOR_ETFS if s in keep], _dt.date(2020, 12, 31))
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "topdown_rank":              # Gemini round 8 (2026-10-27): 90-day vol-adjusted sector RS ranking for Sector Top-Down (research.RANK_SPEC)
                import datetime as _dt
                from .research import topdown_rank_lines
                from .universe import CONTROL_SECTOR_OF, SECTOR_ETFS, SECTOR_OF
                today_ = _dt.date.today()
                ld = lambda syms: {s: d for s, d in ((s, load(s, "1Day", _dt.date(2015, 1, 2), today_, cache)) for s in syms) if len(d) > 300}   # noqa: E731
                unis = {"today's list": ld(SECTOR_OF), "hindsight-free 2016 list": ld(CONTROL_SECTOR_OF)}
                lines += ["## topdown_rank: pre-declared (research.RANK_SPEC)"] + topdown_rank_lines(
                    unis, {"today's list": SECTOR_OF, "hindsight-free 2016 list": CONTROL_SECTOR_OF}, ld(SECTOR_ETFS), ld(["SPY"])["SPY"], today_)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "topdown_sizing":            # owner 2026-10-27: "increase risk per trade on the profitable set ups" (research.SIZING_SPEC)
                import datetime as _dt
                from .research import topdown_sizing_lines
                from .universe import STOCKS
                today_ = _dt.date.today()
                ld = lambda syms: {s: d for s, d in ((s, load(s, "1Day", _dt.date(2015, 1, 2), today_, cache)) for s in syms) if len(d) > 300}   # noqa: E731
                unis = {"today's list": ld(STOCKS), "hindsight-free 2016 list": ld(SECTOR_TD_CONTROL)}
                lines += ["## topdown_sizing: pre-declared (research.SIZING_SPEC)"] + topdown_sizing_lines(unis, ld(["SPY"])["SPY"], today_)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "credit_gate":               # Gemini round 7 (2026-10-27): HYG/IEF credit-stress entry gate on Sector Top-Down (research.CREDIT_SPEC)
                import datetime as _dt
                from .research import credit_gate_lines
                from .universe import STOCKS
                today_ = _dt.date.today()
                ld = lambda syms: {s: d for s, d in ((s, load(s, "1Day", _dt.date(2015, 1, 2), today_, cache)) for s in syms) if len(d) > 300}   # noqa: E731
                ex = ld(["SPY", "HYG", "IEF"])
                unis = {"today's list": ld(STOCKS), "hindsight-free 2016 list": ld(SECTOR_TD_CONTROL)}
                lines += ["## credit_gate: Gemini round 7, pre-declared (research.CREDIT_SPEC)"] + credit_gate_lines(unis, ex["SPY"], ex["HYG"], ex["IEF"], today_)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "swing_exits_base":          # owner 2026-10-27 follow-up: each swing strategy's OWN exits over 2016-now, by year (no exit changes)
                import datetime as _dt
                from . import exitlab as _xl
                from .universe import STOCKS
                today_ = _dt.date.today()
                dd = {s: load(s, "1Day", _dt.date(2015, 1, 1), today_, cache) for s in sorted(set(STOCKS) | {"SPY", "QQQ"})}
                dd = {s: d for s, d in dd.items() if len(d) > 300}
                spy_c = dd["SPY"]["close"]
                own = {st: ["own"] for st in _xl.STRATEGIES}
                lines += ["## swing_exits_base: own exits, 2016 to 3 months ago (last 12 months = OOS), 10 bps a side"]
                lines += _xl.long_lines(_xl.long_test(dd, spy_c, today_, own), "today's list")
                cd = {s: load(s, "1Day", _dt.date(2015, 1, 1), today_, cache) for s in SECTOR_TD_CONTROL}
                cd = {s: d for s, d in cd.items() if len(d) > 300}
                lines += _xl.long_lines(_xl.long_test({**cd, "SPY": dd["SPY"]}, spy_c, today_, {k: v for k, v in own.items() if k != "index200"}),
                                        "the hindsight-free 2016 list")
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "swing_exits":               # owner 2026-10-27: every exit type on every swing strategy, short test then long test (exitlab)
                import datetime as _dt
                from . import exitlab as _xl
                from .universe import STOCKS
                today_ = _dt.date.today()
                dd = {s: load(s, "1Day", _dt.date(2015, 1, 1), today_, cache) for s in sorted(set(STOCKS) | {"SPY", "QQQ"})}
                dd = {s: d for s, d in dd.items() if len(d) > 300}
                spy_c = dd["SPY"]["close"]
                sh = _xl.short_test(dd, spy_c, today_)
                lines += ["## swing_exits SHORT test: last 3 months, two halves, 10 bps a side"] + _xl.short_lines(sh)
                picks = _xl.best_passers(sh)
                lines.append(f"- passers taken to the long test: {picks}")
                if any(picks.values()):
                    lg = _xl.long_test(dd, spy_c, today_, picks)
                    lines += ["## swing_exits LONG test: 2016 to 3 months ago, last 12 months sequestered (OOS)"] + _xl.long_lines(lg, "today's list")
                    cd = {s: load(s, "1Day", _dt.date(2015, 1, 1), today_, cache) for s in SECTOR_TD_CONTROL}
                    cd = {s: d for s, d in cd.items() if len(d) > 300}
                    stock_picks = {k: v for k, v in picks.items() if k != "index200"}
                    if any(stock_picks.values()):
                        lg2 = _xl.long_test({**cd, "SPY": dd["SPY"]}, spy_c, today_, stock_picks)
                        lines += _xl.long_lines(lg2, "the hindsight-free 2016 list")
                else:
                    lines.append("- nothing passed the short test: no long test")
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "trend_sleeve":              # owner 2026-10-28: cross-asset trend sleeve on Alpaca ETFs (research.TREND_SLEEVE, pre-declared)
                import datetime as _dt
                from .research import TREND_SLEEVE, trend_sleeve_lines
                dd = {}
                for s_ in TREND_SLEEVE["assets"]:
                    try:
                        d_ = load(s_, "1Day", _dt.date(2005, 1, 1), _dt.date.today(), cache)
                        if len(d_) > 400:
                            dd[s_] = d_
                    except Exception as exc:
                        print(f"skip {s_}: {exc}", flush=True)
                lines += ["## trend_sleeve: cross-asset trend (12-month time-series momentum, inverse-vol, monthly), pre-declared"] + trend_sleeve_lines(dd)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "vol_target":                # Gemini round 6 (2026-10-27): volatility-targeted 200-day rule vs the plain rule
                import datetime as _dt
                from .research import vol_target_lines
                dd = {s: load(s, "1Day", _dt.date(2004, 1, 1), _dt.date.today(), cache) for s in ("SPY", "QQQ")}
                lines += ["## vol_target: SPY / QQQ, train to 2015-12-31, validate 2016 on"] + vol_target_lines(dd, _dt.date(2015, 12, 31))
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "gap_drift":                 # Gemini round 3 (2026-10-26): the 200-SMA benchmark + the power-earnings-gap proxy (research.power_gap_lines)
                import datetime as _dt
                from .research import power_gap_lines, trend_benchmark_lines
                from .universe import SECTOR_ETFS, STOCKS
                _split = _dt.date(2020, 12, 31)
                allsyms = sorted(set(STOCKS) | set(SECTOR_TD_CONTROL) | {"SPY", "QQQ"})
                dd = {s: load(s, "1Day", _dt.date(2014, 1, 1), _dt.date.today(), cache) for s in allsyms}
                dd = {s: d for s, d in dd.items() if len(d) > 300}
                lines += ["## gap_drift: benchmark and power-earnings-gap proxy (train to 2020-12-31, validate 2021 on)"] + trend_benchmark_lines(dd, _split)
                lines += power_gap_lines({s: d for s, d in dd.items() if s in SECTOR_TD_CONTROL or s in ("SPY", "QQQ")}, _split, "the HINDSIGHT-FREE list (2016's 21 largest companies)")
                lines += power_gap_lines({s: d for s, d in dd.items() if s in STOCKS or s in ("SPY", "QQQ")}, _split, "today's top holdings of each sector ETF (survivorship-biased)")
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "exit_time_fx":              # owner 2026-10-25: time exits (his manual-close pattern) vs the 0.5R cut / 1.25R take-profit
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import exit_time_check
                today = _dt.date.today()
                for label, days_ in (("last 3 months", 92), ("last 12 months", 365)):
                    a_ = today - _dt.timedelta(days=days_)
                    h1 = {i: fx_load(i, "H1", a_ - _dt.timedelta(days=60), today, Path("state/fx/cache")) for i in MAJORS}
                    lines += [f"## exit_time_fx: time exits vs live exits, {label} ({a_} to {today}, H1, bid/ask)"] + exit_time_check(h1, a_, today)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "zone_learn":                # owner 2026-10-25: learn each instrument's buy / sell zones, graded on touches it never saw
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.data import mid as fx_mid
                from . import universe as _u
                from .zonelearn import zone_report
                today = _dt.date.today()
                fxs = lambda i: 0.5 if "JPY" in i else 0.005                          # noqa: E731  (50 pips / half-figure levels)
                for g, years in (("H4", 5), ("D", 9)):
                    a_ = today - _dt.timedelta(days=365 * years)
                    dd = {i: fx_mid(fx_load(i, g, a_, today, Path("state/fx/cache"))) for i in MAJORS}
                    lines += zone_report(dd, f"FOREX {g} bars, {years} years, 7 majors", {i: fxs(i) for i in MAJORS})
                    print("\n".join(lines[first:]), flush=True)
                    first = len(lines)
                a_ = today - _dt.timedelta(days=365 * 8)
                for label, syms in (("LARGE-cap stocks", list(_u.STOCKS)), ("MID / SMALL-cap stocks", list(_u.MID_CAPS) + list(_u.SMALL_CAPS))):
                    dd = {}
                    for s_ in syms:
                        try:
                            dd[s_] = load(s_, "1Day", a_, today, cache)
                        except Exception as exc:
                            print(f"skip {s_}: {exc}", flush=True)
                    steps = {k: (1.0 if v["close"].iloc[-1] < 50 else 5.0 if v["close"].iloc[-1] < 500 else 10.0) for k, v in dd.items() if len(v)}
                    lines += zone_report(dd, f"{label}, daily bars, 8 years", steps)
                    print("\n".join(lines[first:]), flush=True)
                    first = len(lines)
                continue
            if fam == "range_map_fx":              # owner 2026-10-25: the range-trading decision map on OANDA (fxbot.research.range_map_check)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import range_map_check
                today = _dt.date.today()
                for label, days_ in (("last 2 weeks", 14), ("last 3 months", 92), ("last 12 months", 365)):
                    a_ = today - _dt.timedelta(days=days_)
                    h1 = {i: fx_load(i, "H1", a_ - _dt.timedelta(days=60), today, Path("state/fx/cache")) for i in MAJORS}
                    lines += [f"## range_map_fx: range decision map on OANDA, {label} ({a_} to {today}, H1, bid/ask)"] + range_map_check(h1, a_, today)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "velez_fx2":                 # owner 2026-10-22: Velez setups as forex experts + as confirmation (fxbot.research.velez_fx2_check)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import velez_fx2_check
                today = _dt.date.today()
                for label, days_ in (("last 2 weeks", 14), ("last 3 months", 92)):
                    a_ = today - _dt.timedelta(days=days_)
                    h1 = {i: fx_load(i, "H1", a_ - _dt.timedelta(days=60), today, Path("state/fx/cache")) for i in MAJORS}
                    lines += [f"## velez_fx2: Velez setups on OANDA, {label} ({a_} to {today}, H1, live exits, bid/ask)"] + velez_fx2_check(h1, a_, today)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "velez_fx":                  # owner 2026-10-22: Velez rules on the OANDA strategies (fxbot.research.velez_fx_check)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import velez_fx_check
                today = _dt.date.today()
                for label, days_ in (("last 2 weeks", 14), ("last 3 months", 92)):
                    a_ = today - _dt.timedelta(days=days_)
                    h1 = {i: fx_load(i, "H1", a_ - _dt.timedelta(days=60), today, Path("state/fx/cache")) for i in MAJORS}
                    dd = {i: fx_load(i, "D", a_ - _dt.timedelta(days=330), today, Path("state/fx/cache")) for i in MAJORS}
                    lines += [f"## velez_fx: Velez rules on the forex strategies, {label} ({a_} to {today}, live exits, bid/ask)"] + velez_fx_check(h1, dd, a_, today)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "conf_fx":                   # owner 2026-10-22: the oversold confluence on OANDA (fxbot.research.confluence_check)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import confluence_check
                today = _dt.date.today()
                for label, days_ in (("last 2 weeks", 14), ("last 3 months (context)", 92)):
                    a_ = today - _dt.timedelta(days=days_)
                    dg = {g: {i: fx_load(i, g, a_ - _dt.timedelta(days=200 if g == "D" else 60), today, Path("state/fx/cache")) for i in MAJORS}
                          for g in ("D", "H4", "H1")}
                    lines += [f"## conf_fx: oversold confluence (3 of 4 + tick volume), {label} ({a_} to {today}, bid/ask, pessimistic)"] + confluence_check(dg, a_, today)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "scalp_fx":                  # cut 0.10R + all out at 0.30R on the live strategies (fxbot.research.scalp_check)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import scalp_check
                today = _dt.date.today()
                for label, days_ in (("last 3 months", 92), ("last 12 months", 365)):
                    a_ = today - _dt.timedelta(days=days_)
                    fxd = {i: fx_load(i, "H1", a_ - _dt.timedelta(days=90), today, Path("state/fx/cache")) for i in MAJORS}
                    lines += [f"## scalp_fx: the owner's 0.10R cut / +0.30R close-out on the live strategies, {label} ({a_} to {today}, H1, bid/ask, pessimistic)"] + scalp_check(fxd, a_, today)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "losscut_fx":                # the live strategies with a stop at 10% / 25% / 50% of the normal one (fxbot.research.losscut_check)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import losscut_check
                today = _dt.date.today()
                for label, days_ in (("last 3 months", 92), ("last 12 months", 365)):
                    a_ = today - _dt.timedelta(days=days_)
                    fxd = {i: fx_load(i, "H1", a_ - _dt.timedelta(days=90), today, Path("state/fx/cache")) for i in MAJORS}
                    lines += [f"## losscut_fx: the live strategies with the owner's loss cut, {label} ({a_} to {today}, H1, bid/ask, pessimistic)"] + losscut_check(fxd, a_, today)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "inbox_fx":                  # the range experts inside the owner's range box (fxbot.research.inbox_check), 3 and 12 months
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import inbox_check
                today = _dt.date.today()
                for label, days_ in (("last 3 months", 92), ("last 12 months", 365)):
                    a_ = today - _dt.timedelta(days=days_)
                    dbg = {g: {i: fx_load(i, g, a_ - _dt.timedelta(days=90), today, Path("state/fx/cache")) for i in MAJORS} for g in ("H1", "H4")}
                    lines += [f"## inbox_fx: the owner's range experts inside the range box, {label} ({a_} to {today}, bid/ask)"] + inbox_check(dbg, a_, today)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "votecheck_fx":              # 60% / 80% / 100% indicator vote for confirmed breakouts (fxbot.research.vote_check)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import vote_check
                fxd = {i: fx_load(i, "H1", _dt.date(2015, 1, 1), _dt.date(2022, 12, 31), Path("state/fx/cache")) for i in MAJORS}
                lines += ["## votecheck_fx: confirmed box breakouts by indicator vote (H1)"] + vote_check(fxd)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "confirmcheck_fx":           # confirmed-breakout settings, train vs validate (fxbot.research.confirm_check)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import confirm_check
                fxd = {i: fx_load(i, "H1", _dt.date(2015, 1, 1), _dt.date(2022, 12, 31), Path("state/fx/cache")) for i in MAJORS}
                lines += ["## confirmcheck_fx: confirmed box breakouts (H1)"] + confirm_check(fxd)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam == "boxdiag_fx":                # why the owner's OANDA range box loses (fxbot.research.box_diagnosis)
                import datetime as _dt
                from fxbot.data import MAJORS
                from fxbot.data import load as fx_load
                from fxbot.research import box_diagnosis
                fxd = {i: fx_load(i, "H1", _dt.date(2015, 1, 1), _dt.date(2022, 12, 31), Path("state/fx/cache")) for i in MAJORS}
                lines += ["## boxdiag_fx: the owner's OANDA range box, trade by trade (H1, 2016-2022, bid/ask)"] + box_diagnosis(fxd)
                print("\n".join(lines[first:]), flush=True)
                continue
            if fam.startswith("fx"):               # OANDA forex research (fxbot, practice data only): "fx" = every family, "fx_<family>" = one
                from fxbot.cli import main as fx_main
                from fxbot.research import FAMILIES as FX_FAMILIES
                fams = list(FX_FAMILIES) if fam == "fx" else [fam]
                code = fx_main(["run-all", "--families", *fams])
                summary = Path("state/fx/reports/summary.md")
                res = {"family": fam, "verdict": "see state/fx/reports/summary.md" if code == 0 else "WAITING FOR KEYS (OANDA_API_TOKEN)"}
                if summary.exists() and code == 0:
                    lines.append(summary.read_text())
            elif fam == "funnel_shorts":          # owner 2026-10-17 "retest first": the funnel's short side in several versions
                from datetime import timedelta as _td
                from .funnel import backtest as funnel_bt, market_down
                from .universe import STOCKS as _ST
                end2 = date.today()
                fb = {}
                for s_ in _ST:
                    try:
                        fb[s_] = load(s_, "1Day", end2 - _td(days=700), end2, cache)
                    except Exception as exc:
                        print(f"skip {s_}: {exc}", flush=True)
                spy_d = load("SPY", "1Day", end2 - _td(days=700), end2, cache)
                mdown = market_down(spy_d)
                from .news import fetch as news_fetch, sector_news_negative, sector_trend_negative
                from .universe import SECTOR_ETFS as _ETF, SECTOR_OF as _SOF
                etf_neg = sector_trend_negative({e: load(e, "1Day", end2 - _td(days=700), end2, cache) for e in _ETF})
                try:                                     # owner: shorts only when the sector's news is negative (Alpaca/Benzinga headlines)
                    arts = news_fetch(list(_ST), end2 - _td(days=380), end2, cache / "news")
                    news_neg = sector_news_negative(arts, _SOF, spy_d.index)
                    lines.append(f"- news: {len(arts)} articles on the 55 stocks; sector-negative days per sector: "
                                 + ", ".join(f"{e} {int(v.sum())}" for e, v in sorted(news_neg.items())))
                except Exception as exc:
                    news_neg = None
                    lines.append(f"- news feed unavailable ({type(exc).__name__}: {str(exc)[:120]}): variants J/K skipped")
                by_stock = lambda m: {s_: m.get(_SOF.get(s_, "")) for s_ in fb}           # noqa: E731
                variants = {
                    "A. mirror of the longs (as built)": {},
                    "B. only when the market falls (SPY under a falling 50-day)": {"short_ok": "mdown"},
                    "C. all 4 of 4 must agree": {"short_rules": {"need": 4}},
                    "D. B + C": {"short_ok": "mdown", "short_rules": {"need": 4}},
                    "E. no sell vote (stop or 1.5R only)": {"short_exit_vote": False},
                    "F. take-profit 1.0R": {"short_rules": {"tp_r": 1.0}},
                    "G. stop at 2-3 ATR (wider)": {"short_rules": {"stop_min_atr": 2.0}},
                    "I. sector negative (sector ETF under a falling 50-day)": {"short_ok": by_stock(etf_neg)},
                    "I + C (sector trend negative, 4 of 4)": {"short_ok": by_stock(etf_neg), "short_rules": {"need": 4}},
                }
                import pandas as _pd
                from .funnel import trend_strength_down, weakest_per_sector
                ts_ = {s_: trend_strength_down(d_, spy_d) for s_, d_ in fb.items() if len(d_) > 200}
                strong3 = {s_: _pd.Series((t_["votes"] == 3).to_numpy(bool), index=_pd.DatetimeIndex(_pd.to_datetime(t_.index.date)))
                           for s_, t_ in ts_.items()}
                weak3 = weakest_per_sector(ts_, _SOF, 3)
                both_ = lambda a, b: {s_: (a[s_] & b[s_].reindex(a[s_].index).fillna(False)) if a.get(s_) is not None and b.get(s_) is not None
                                      else None for s_ in fb}                                  # noqa: E731
                variants["L. trend strength strongly negative (ADX>=25 down, 150-day falling, weaker than SPY)"] = {"short_ok": strong3}
                variants["M. L + one of the 3 weakest stocks in its sector"] = {"short_ok": both_(strong3, weak3)}
                variants["N. M + sector trend negative (I)"] = {"short_ok": both_(both_(strong3, weak3), by_stock(etf_neg))}
                if news_neg is not None:
                    variants["J. sector NEWS negative (headlines, last 5 sessions)"] = {"short_ok": by_stock(news_neg)}
                    variants["O. M + sector NEWS negative (J)"] = {"short_ok": both_(both_(strong3, weak3), by_stock(news_neg))}
                    nb, eb = by_stock(news_neg), by_stock(etf_neg)
                    variants["K. J + I (news AND sector trend negative)"] = {"short_ok": {
                        s_: (nb[s_] & eb[s_].reindex(nb[s_].index).fillna(False)) if nb[s_] is not None and eb[s_] is not None else None
                        for s_ in fb}}
                variants["P. + S/R (at strong resistance) + RSI 45-60 turning down: 4 of 6"] = {"short_rules": {"sr_rsi": True}}
                variants["Q. M + P (trend strength strongly negative, 3 weakest, 4 of 6)"] = {"short_ok": both_(strong3, weak3), "short_rules": {"sr_rsi": True}}
                res = {"family": fam, "verdict": "see the table"}
                long_variants = {"as built (4 votes, 3 of 4)": {},
                                 "+ S/R (at strong support) + RSI 40-55 turning up: 4 of 6": {"rules": {"sr_rsi": True}},
                                 "+ S/R + RSI: 5 of 6": {"rules": {"sr_rsi": True, "need6": 5}}}
                for label, a_ in (("last 3 months", end2 - _td(days=92)), ("last 12 months", end2 - _td(days=365))):
                    lines.append(f"### longs, {label}")
                    for name, kw in long_variants.items():
                        m_ = funnel_bt({k: v.copy() for k, v in fb.items()}, a_, end2 - _td(days=1), sides=("long",), **kw)
                        lines.append(f"- {name}: {m_['trades']} trades, win {m_['win_rate']}, PF {m_['profit_factor']}, avg R {m_['avg_r']}, "
                                     f"total {m_['longs_r']:+}R, exits {m_['exits']}")
                        res[f"longs {label} | {name}"] = {k: v for k, v in m_.items() if k != "list"}
                    lines.append(f"### shorts, {label}")
                    for name, kw in variants.items():
                        kw = dict(kw)
                        if kw.get("short_ok") == "mdown":
                            kw["short_ok"] = mdown
                        m_ = funnel_bt({k: v.copy() for k, v in fb.items()}, a_, end2 - _td(days=1), sides=("short",), **kw)
                        lines.append(f"- {name}: {m_['trades']} trades, win {m_['win_rate']}, PF {m_['profit_factor']}, avg R {m_['avg_r']}, "
                                     f"total {m_['shorts_r']:+}R, exits {m_['exits']}")
                        res[f"{label} | {name}"] = {k: v for k, v in m_.items() if k != "list"}
            elif fam == "swing_pristine":          # owner 2026-10-22: Pristine rules on the swing bot (daily stock bars, longs)
                from datetime import timedelta as _td
                from .funnel import backtest as funnel_bt
                from .universe import STOCKS as _ST
                end2 = date.today()
                fb = {}
                for s_ in _ST:
                    try:
                        fb[s_] = load(s_, "1Day", end2 - _td(days=700), end2, cache)
                    except Exception as exc:
                        print(f"skip {s_}: {exc}", flush=True)
                res = {"family": fam}
                live_funnel = {"stop_frac": 0.5, "tp_r": 1.25, "v_tail": True}
                live_os = {"oversold": True, "os_stage": None, "tp_r": 1.25, "stop_frac": 0.75}
                var = {"FUNNEL live": live_funnel, "FUNNEL + COG confirm": {**live_funnel, "pr_confirm": "cog"},
                       "FUNNEL + PBS confirm": {**live_funnel, "pr_confirm": "pbs"},
                       "OVERSOLD live": live_os, "OVERSOLD + COG confirm": {**live_os, "pr_confirm": "cog"},
                       "OVERSOLD + CBS confirm": {**live_os, "pr_confirm": "cbs"}}
                for kind in ("pbs", "cbs", "cog", "pbo"):
                    for tr in ("stop", "bar", "ma20"):
                        var[f"PRISTINE {kind} trail {tr}"] = {"pristine": kind, "pr_trail": tr, "stop_frac": 1.0, "tp_r": 1.25}
                for label, a_ in (("2 weeks", end2 - _td(days=14)), ("3 months", end2 - _td(days=92)), ("12 months", end2 - _td(days=365))):
                    for nm, rules in var.items():
                        m_ = funnel_bt(fb, a_, end2 - _td(days=1), sides=("long",), rules=rules)
                        lines.append(f"- {label} | {nm}: {m_['trades']} trades, win {m_['win_rate']}, PF {m_['profit_factor']}, total {m_['longs_r']:+}R, "
                                     f"return {m_['return_pct']:+}% at 0.5% risk")
                        res[f"{label} | {nm}"] = {k: v for k, v in m_.items() if k != "list"}
            elif fam == "swing_open_delay":         # owner 2026-10-22: oversold (and funnel) swing entries at 10:00 instead of the open
                from datetime import timedelta as _td
                from .funnel import backtest as funnel_bt
                from .universe import STOCKS as _ST
                end2 = date.today()
                fb, mb = {}, {}
                for s_ in _ST:
                    try:
                        fb[s_] = load(s_, "1Day", end2 - _td(days=700), end2, cache)
                        mb[s_] = load(s_, "1Min", end2 - _td(days=95), end2, cache)
                    except Exception as exc:
                        print(f"skip {s_}: {exc}", flush=True)
                res = {"family": fam}
                lines.append(f"- {len(mb)} stocks with 1-minute bars (entries without them are left out of every row, so rows compare like for like)")
                rules_ = {"OVERSOLD live": {"oversold": True, "os_stage": None, "tp_r": 1.25, "stop_frac": 0.75},
                          "FUNNEL live": {"stop_frac": 0.5, "tp_r": 1.25, "v_tail": True}}
                for label, a_ in (("1 month", end2 - _td(days=31)), ("3 months", end2 - _td(days=92))):
                    for nm, rules in rules_.items():
                        for when in ("09:31", "10:00", "10:30"):
                            m_ = funnel_bt({k: v for k, v in fb.items() if k in mb}, a_, end2 - _td(days=1), sides=("long",), rules=rules,
                                           entry_at=when, minute=mb)
                            lines.append(f"- {label} | {nm} | enter {when}: {m_['trades']} trades, win {m_['win_rate']}, PF {m_['profit_factor']}, "
                                         f"total {m_['longs_r']:+}R, return {m_['return_pct']:+}%, exits {m_['exits']}")
                            res[f"{label} | {nm} | {when}"] = {k: v for k, v in m_.items() if k != "list"}
            elif fam == "velez_swing_redesign":     # owner 2026-10-22: the Velez-style swing trader (funnel velez_swing), large / mid / small caps
                from datetime import timedelta as _td
                from .funnel import backtest as funnel_bt
                from . import universe as _u
                end2 = date.today()
                res = {"family": fam}
                groups = {"large": list(_u.STOCKS), "mid": list(_u.MID_CAPS), "small": list(_u.SMALL_CAPS)}
                rules_v = {"velez_swing": True, "tp_r": 2.0}
                for g, syms in groups.items():
                    fb = {}
                    for s_ in syms:
                        try:
                            fb[s_] = load(s_, "1Day", end2 - _td(days=700), end2, cache)
                        except Exception as exc:
                            print(f"skip {s_}: {exc}", flush=True)
                    lines.append(f"### {g} caps ({len(fb)} stocks loaded)")
                    for label, a_ in (("1 month", end2 - _td(days=31)), ("3 months (context)", end2 - _td(days=92)),
                                      ("12 months (context)", end2 - _td(days=365))):
                        for nm, rules in (("VELEZ SWING 2R", rules_v), ("VELEZ SWING 1.5R", {**rules_v, "tp_r": 1.5}),
                                          ("FUNNEL live (compare)", {"stop_frac": 0.5, "tp_r": 1.25, "v_tail": True})):
                            m_ = funnel_bt(fb, a_, end2 - _td(days=1), sides=("long",), rules=rules)
                            lines.append(f"- {g} | {label} | {nm}: {m_['trades']} trades, win {m_['win_rate']}, PF {m_['profit_factor']}, "
                                         f"total {m_['longs_r']:+}R, return {m_['return_pct']:+}% at 0.5% risk")
                            res[f"{g} | {label} | {nm}"] = {k: v for k, v in m_.items() if k != "list"}
            elif fam == "swing_velez":             # owner 2026-10-22: Velez rules on the swing longs (funnel + oversold), 2 weeks / 3 / 12 months
                from datetime import timedelta as _td
                from .funnel import backtest as funnel_bt
                from .universe import STOCKS as _ST
                end2 = date.today()
                fb = {}
                for s_ in _ST:
                    try:
                        fb[s_] = load(s_, "1Day", end2 - _td(days=700), end2, cache)
                    except Exception as exc:
                        print(f"skip {s_}: {exc}", flush=True)
                res = {"family": fam}
                live_funnel = {"stop_frac": 0.5, "tp_r": 1.25}
                live_os = {"oversold": True, "os_stage": None, "tp_r": 1.25, "stop_frac": 0.75}
                filt = {"as live": {}, "+ trend (20 over 200)": {"v_env": True}, "+ look left": {"v_look_left": True},
                        "+ no chase": {"v_no_chase": True}, "+ tail bar": {"v_tail": True}, "+ 3-5 bar dip": {"v_bars35": True},
                        "+ trend + look left": {"v_env": True, "v_look_left": True}, "+ look left + no chase": {"v_look_left": True, "v_no_chase": True}}
                for strat, base in (("FUNNEL", live_funnel), ("OVERSOLD", live_os)):
                    for label, a_ in (("2 weeks", end2 - _td(days=14)), ("3 months", end2 - _td(days=92)), ("12 months", end2 - _td(days=365))):
                        for nm, fk in filt.items():
                            m_ = funnel_bt(fb, a_, end2 - _td(days=1), sides=("long",), rules={**base, **fk})
                            lines.append(f"- {strat} | {label} | {nm}: {m_['trades']} trades, win {m_['win_rate']}, PF {m_['profit_factor']}, "
                                         f"total {m_['longs_r']:+}R, return {m_['return_pct']:+}% at 0.5% risk")
                            res[f"{strat} | {label} | {nm}"] = {k: v for k, v in m_.items() if k != "list"}
            elif fam == "oversold_swing":         # owner 2026-10-19: majority-oversold confluence as a daily swing long, vs the live funnel
                from datetime import timedelta as _td
                from .funnel import backtest as funnel_bt
                from .universe import STOCKS as _ST
                end2 = date.today()
                fb = {}
                for s_ in _ST:
                    try:
                        fb[s_] = load(s_, "1Day", end2 - _td(days=700), end2, cache)
                    except Exception as exc:
                        print(f"skip {s_}: {exc}", flush=True)
                res = {"family": fam, "verdict": "adopt only if clearly better than the live funnel on 3 AND 12 months"}
                os_ = {"oversold": True}
                variants = (("LIVE funnel (as built)", {}),
                            ("oversold 3/4, stage 2 only", {**os_}),
                            ("oversold 3/4, stages 1-2", {**os_, "os_stage": (1, 2)}),
                            ("oversold 3/4, any stage (as pasted)", {**os_, "os_stage": None}),
                            ("oversold 3/4 vwap 4th vote, stage 2", {**os_, "os_fourth": "vwap"}),
                            ("oversold 2/4, stage 2 only", {**os_, "os_need": 2}),
                            ("oversold 3/4, stage 2, tp 1.25R, stop 1.0", {**os_, "tp_r": 1.25}),
                            ("oversold 3/4, stage 2, stop 0.5, tp 1.25R", {**os_, "tp_r": 1.25, "stop_frac": 0.5}),
                            ("oversold 3/4, any stage, stop 0.75, tp 1.25R (owner 10-19 script)", {**os_, "os_stage": None, "tp_r": 1.25, "stop_frac": 0.75}),
                            ("oversold 3/4, any stage, stop 1.0, tp 1.25R (live)", {**os_, "os_stage": None, "tp_r": 1.25}))
                for label, a_ in (("last 2 weeks", end2 - _td(days=14)), ("last 3 months", end2 - _td(days=92)), ("last 12 months", end2 - _td(days=365))):
                    for nm, rules in variants:
                        m_ = funnel_bt(fb, a_, end2 - _td(days=1), sides=("long",), rules=rules)
                        lines.append(f"- {label} | {nm}: {m_['trades']} trades, win {m_['win_rate']}, PF {m_['profit_factor']}, avg R {m_['avg_r']}, "
                                     f"total {m_['longs_r']:+}R, return {m_['return_pct']:+}% at 0.5% risk, exits {m_['exits']}")
                        res[f"{label} | {nm}"] = {k: v for k, v in m_.items() if k != "list"}
            elif fam == "funnel_r":               # the live daily funnel (longs) at several stop / target pairs, 3 and 12 months
                from datetime import timedelta as _td
                from .funnel import backtest as funnel_bt
                from .universe import STOCKS as _ST
                end2 = date.today()
                fb = {}
                for s_ in _ST:
                    try:
                        fb[s_] = load(s_, "1Day", end2 - _td(days=700), end2, cache)
                    except Exception as exc:
                        print(f"skip {s_}: {exc}", flush=True)
                res = {"family": fam}
                for label, a_ in (("last 3 months", end2 - _td(days=92)), ("last 12 months", end2 - _td(days=365))):
                    for nm, rules in (("stop 1.0 / out 1.5 (before 2026-10-19)", {}), ("stop 0.5 / out 1.25 (owner 10-19)", {"stop_frac": 0.5, "tp_r": 1.25}),
                                      ("stop 1.0 / out 1.25", {"tp_r": 1.25}), ("stop 0.75 / out 1.25", {"stop_frac": 0.75, "tp_r": 1.25}),
                                      ("stop 0.5 / out 1.5", {"stop_frac": 0.5})):
                        m_ = funnel_bt(fb, a_, end2 - _td(days=1), sides=("long",), rules=rules)
                        lines.append(f"- {label} | {nm}: {m_['trades']} trades, win {m_['win_rate']}, PF {m_['profit_factor']}, avg R {m_['avg_r']}, "
                                     f"total {m_['longs_r']:+}R, return {m_['return_pct']:+}% at 0.5% risk, exits {m_['exits']}")
                        res[f"{label} | {nm}"] = {k: v for k, v in m_.items() if k != "list"}
            elif fam == "funnel_swing":           # owner's Stock Funnel as a daily swing strategy (alpacabot.funnel), last 3 months + 12 for context
                from datetime import timedelta as _td
                from .funnel import backtest as funnel_bt
                from .universe import STOCKS as _ST
                end2 = date.today()
                fb = {}
                for s_ in _ST:
                    try:
                        fb[s_] = load(s_, "1Day", end2 - _td(days=700), end2, cache)
                    except Exception as exc:
                        print(f"skip {s_}: {exc}", flush=True)
                res = {"family": fam, "verdict": "live-paper rule: it goes live at 0.5% unless clearly harmful"}
                for label, a_ in (("last 3 months", end2 - _td(days=92)), ("last 12 months (context)", end2 - _td(days=365))):
                    m_ = funnel_bt(fb, a_, end2 - _td(days=1))
                    lines.append(f"- {label}: {m_['trades']} trades (longs {m_['longs']} {m_['longs_r']:+}R, shorts {m_['shorts']} {m_['shorts_r']:+}R), "
                                 f"win {m_['win_rate']}, PF {m_['profit_factor']}, avg R {m_['avg_r']}, return {m_['return_pct']:+}% at 0.5% risk, exits {m_['exits']}")
                    res[label] = {k: v for k, v in m_.items() if k != "list"}
                    if label == "last 3 months":
                        for t in m_["list"]:
                            lines.append(f"  - {t['symbol']} {t['side']} {t['entry_date']} -> {t['exit_date']} {t['r']:+}R ({t['reason']})")
            elif fam == "sector_td":               # the supplied frozen rules, tested on untouched years and on a hindsight-free control list
                end2 = date.today()
                spy2 = load("SPY", "1Day", start, end2, cache)
                uni = {}
                for name, syms in (("their 10", SECTOR_TD_THEIRS), ("2016 large caps (control)", SECTOR_TD_CONTROL)):
                    got = {}
                    for s in syms:
                        try:
                            d = load(s, "1Day", start, end2, cache)
                            if len(d) > 300:
                                got[s] = d
                        except Exception as exc:
                            print("skip", s, exc, flush=True)
                    uni[name] = got
                periods = {"2016-2019 (untouched by their research)": (date(2016, 1, 4), date(2019, 12, 31)),
                           "2020-2022": (date(2020, 1, 2), date(2022, 12, 31)), "2023-today": (date(2023, 1, 3), end2)}
                res = sector_td_study(uni, spy2, periods, Costs(slip_bps=5.0), Portfolio(risk_pct=0.0035, max_positions=3, max_pos_frac=0.333))
                res["verdict"] = "see the table: compare each row with buy-and-hold of the same names"
            elif fam == "sector_td_robust":      # leave-one-out, nearby parameters, sizing, block bootstrap: 2016-today, both universes
                end2 = date.today()
                spy2 = load("SPY", "1Day", start, end2, cache)
                res = {"family": fam, "verdict": "see the table", "universes": {}}
                for name, syms in (("their 10", SECTOR_TD_THEIRS), ("2016 large caps (control)", SECTOR_TD_CONTROL),
                                   (f"top 5 per sector ({UNIVERSE_AS_OF} caps, 55)", UNIVERSE_STOCKS)):
                    d_ = {}
                    for s in syms:
                        try:
                            d_[s] = load(s, "1Day", start, end2, cache)
                        except Exception as exc:
                            print(f"skip {s}: {exc}")
                    res["universes"][name] = sector_td_robust({k: v for k, v in d_.items() if len(v) > 300}, spy2, date(2016, 3, 1), end2,
                                                              Costs(slip_bps=5.0))
            elif fam == "swing_validate":        # the three pre-declared daily-bar candidates, ONE look at 2021-2022
                data = {s: load(s, "1Day", start, end, cache) for s in SWING}
                res = swing_validate(data, test, costs, spy_daily=spy)
            elif fam == "breadth_gate":          # pre-declared sector-breadth entry gate on Sector Top-Down (research.BREADTH_SPEC)
                from .research import breadth_gate_study
                from .universe import CONTROL_SECTOR_OF, SECTOR_OF as TOP5_SECTOR_OF
                end2 = date(2022, 12, 31)
                spy2 = load("SPY", "1Day", date(2015, 1, 2), end2, cache)
                unis = {}
                for name, smap in (("2016 large caps (control)", CONTROL_SECTOR_OF), (f"top 5 per sector ({UNIVERSE_AS_OF} caps)", TOP5_SECTOR_OF)):
                    d_ = {}
                    for sym in smap:
                        try:
                            d_[sym] = load(sym, "1Day", date(2015, 1, 2), end2, cache)
                        except Exception as exc:
                            print(f"skip {sym}: {exc}")
                    unis[name] = ({k: v for k, v in d_.items() if len(v)}, smap)
                res = breadth_gate_study(unis, spy2, Costs(slip_bps=5.0))
            elif fam == "zones_swing":           # owner's trade zones on Sector Top-Down, control + top-55, train + validation
                from .research import zones_swing_study
                from .universe import CONTROL_SECTOR_OF, SECTOR_OF as TOP5_SECTOR_OF
                end2 = date(2022, 12, 31)
                spy2 = load("SPY", "1Day", date(2015, 1, 2), end2, cache)
                unis = {}
                for name, smap in (("2016 large caps (control)", CONTROL_SECTOR_OF), (f"top 5 per sector ({UNIVERSE_AS_OF} caps)", TOP5_SECTOR_OF)):
                    d_ = {}
                    for sym in smap:
                        try:
                            d_[sym] = load(sym, "1Day", date(2015, 1, 2), end2, cache)
                        except Exception as exc:
                            print(f"skip {sym}: {exc}")
                    unis[name] = {k: v for k, v in d_.items() if len(v)}
                res = zones_swing_study(unis, spy2, Costs(slip_bps=5.0))
            elif fam == "swing_short":           # owner's "follow the trend down": a short side for the swing bot (research.swing_short_study)
                from .research import swing_short_study
                from .universe import CONTROL_SECTOR_OF, SECTOR_OF as TOP5_SECTOR_OF
                end2 = date(2022, 12, 31)
                spy2 = load("SPY", "1Day", date(2015, 1, 2), end2, cache)
                unis = {}
                for name, smap in (("2016 large caps (control)", CONTROL_SECTOR_OF), (f"top 5 per sector ({UNIVERSE_AS_OF} caps)", TOP5_SECTOR_OF)):
                    d_ = {}
                    for sym in smap:
                        try:
                            d_[sym] = load(sym, "1Day", date(2015, 1, 2), end2, cache)
                        except Exception as exc:
                            print(f"skip {sym}: {exc}")
                    unis[name] = {k: v for k, v in d_.items() if len(v)}
                res = swing_short_study(unis, spy2, Costs(slip_bps=5.0))
            elif fam == "frozen_systems":        # the owner's frozen Book A and Elite-20, pre-declared (research.FROZEN_SPEC)
                from .research import frozen_systems_study
                from .universe import CONTROL_SECTOR_OF, SECTOR_OF as TOP5_SECTOR_OF
                end2 = date(2022, 12, 31)
                unis = {}
                for name, smap in (("2016 large caps (control)", CONTROL_SECTOR_OF), (f"top 5 per sector ({UNIVERSE_AS_OF} caps)", TOP5_SECTOR_OF)):
                    d_ = {}
                    for sym in smap:
                        try:
                            d_[sym] = load(sym, "1Day", date(2015, 1, 2), end2, cache)
                        except Exception as exc:
                            print(f"skip {sym}: {exc}")
                    unis[name] = ({k: v for k, v in d_.items() if len(v)}, smap)
                res = frozen_systems_study(unis, Costs(slip_bps=5.0))
            elif fam == "rsi2_validate":         # connors_rsi2 (passed train, never validated), ONE look at 2021-2022
                from .research import RSI2_CANDIDATES
                data = {s: load(s, "1Day", start, end, cache) for s in SWING}
                res = swing_validate(data, test, costs, spy_daily=spy, candidates=RSI2_CANDIDATES)
                res["family"] = "rsi2_validate"
            elif fam == "swing_experts":           # the 10 experts on daily bars, multi-day holds, TRAIN years only
                data = {s: load(s, "1Day", start, end, cache) for s in SWING}
                res = swing_expert_study(data, train, costs, spy_daily=spy)
            elif fam in ("swing_rsi", "swing_breakout"):
                data = {s: load(s, "1Day", start, end, cache) for s in SWING}
                res = walk_forward(fam, data, train, test, costs, Portfolio(risk_pct=0.01), spy_daily=spy)
            else:
                if not minute:
                    minute = {s: load(s, "1Min", mstart, end, cache) for s in intraday_syms}
                if fam == "brain":
                    days = split_days(minute, minutes=5, feature_fn=features_all)
                    res = brain_walk_forward(days, train, test, costs, Portfolio(risk_pct=0.01, max_positions=6), spy_daily=spy)
                elif fam == "filters":        # every brain filter combination, TRAIN years only
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    res = filters_study(days, train, costs, ckpt=out / f"ckpt_{a.phase}_filters.json")
                elif fam == "limit":          # market vs limit entries per expert, TRAIN years only
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    res = limit_study(days, train, costs, shorts=True, ckpt=out / f"ckpt_{a.phase}_limit.json")
                elif fam == "exits":          # staircase vs each expert's own exits, TRAIN years only
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    res = exit_study(days, train, costs, shorts=True, ckpt=out / f"ckpt_{a.phase}_exits.json")
                elif fam == "zones_intraday":  # owner's trade zones on the paper brain, train + validation (research.ZONES_BAR)
                    from .research import zones_intraday_study
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    from .zonemap import attach
                    days = attach(days, {s: load(s, "1Day", start, end, cache) for s in intraday_syms})
                    res = zones_intraday_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}_zones.json")
                elif fam == "ladder_intraday":  # owner's drawn exit plan vs alternatives (research.LADDER_VARIANTS), chosen by train ROI
                    from .research import ladder_intraday_study
                    from .zonemap import attach
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    days = attach(days, {s: load(s, "1Day", start, end, cache) for s in intraday_syms})
                    res = ladder_intraday_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_ladder.json", quick=bool(qtag))
                elif fam == "sector_mom":       # Gemini idea 1 (2026-10-26): cross-sectional sector-confirmed momentum, 10:15 entry, ~15:45 exit
                    from .research import sector_momentum_study
                    from .universe import SECTOR_OF, SECTOR_ETFS, STOCKS
                    msyms = sorted(set(STOCKS) | set(SECTOR_ETFS))
                    sm = {s: load(s, "1Min", mstart, end, cache) for s in msyms}
                    days = split_days(sm, minutes=5)
                    res = sector_momentum_study(days, SECTOR_OF, train, test, costs)
                elif fam == "eod_squeeze":      # Gemini idea 2 (2026-10-26): end-of-day short squeeze, 15:10 long, ~35-minute hold
                    from .research import eod_squeeze_study
                    days = split_days(minute, minutes=5, feature_fn=features_both)
                    res = eod_squeeze_study(days, train, test, costs)
                elif fam == "orb_candidate":    # owner's "trading the open": opening-range breakout as an 11th expert
                    from .research import orb_candidate_study
                    from .zonemap import attach
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    days = attach(days, {s: load(s, "1Day", start, end, cache) for s in intraday_syms})
                    res = orb_candidate_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_orb.json")
                elif fam == "mtf_confirm":      # owner's multi-timeframe trend confirmation (research.mtf_confirm_study)
                    from .research import mtf_confirm_study
                    from .zonemap import attach
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    days = attach(days, {s: load(s, "1Day", start, end, cache) for s in intraday_syms})
                    res = mtf_confirm_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_mtf.json")
                elif fam == "profile_assign":   # dual_regime / opening-range on the range profile? (research.profile_assign_study)
                    from .research import profile_assign_study
                    from .zonemap import attach
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    days = attach(days, {s: load(s, "1Day", start, end, cache) for s in intraday_syms})
                    res = profile_assign_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_pa.json")
                elif fam == "reversal_confirm":     # mean-reversion fix (research.reversal_confirm_study)
                    from .research import reversal_confirm_study
                    from .zonemap import attach
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    days = attach(days, {s: load(s, "1Day", start, end, cache) for s in intraday_syms})
                    res = reversal_confirm_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_revc.json")
                elif fam == "trend_stairs":         # owner's trend_stairs design (research.trend_stairs_study)
                    from .research import trend_stairs_study
                    from .zonemap import attach
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    days = attach(days, {s: load(s, "1Day", start, end, cache) for s in intraday_syms})
                    res = trend_stairs_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_tstairs.json")
                elif fam in ("trend_stairs58", "trend_stairs58_opt", "no_stops", "fixed_r", "oversold_gate", "scalper", "velez", "velez2", "velez3", "velez4", "velez_hybrid", "winrate_tune", "winrate_live", "velez_all", "velez_repair", "velez_redesign"):   # owner's drawings on all 58 stocks (+ the no-stop retest)
                    from datetime import timedelta as _td
                    from . import gates as _g
                    from . import universe as _u
                    from .news import fetch as news_fetch, sector_news_negative, sector_trend_negative
                    from .paper import SYMBOLS as _PS
                    from .research import trend_stairs58_study
                    from .selection import for_session, lists
                    from .zonemap import attach
                    for s_ in _u.STOCKS:
                        _g.add_sector(s_, _u.SECTOR_OF[s_])
                    syms58 = sorted(set(_PS) | set(_u.STOCKS))
                    if fam == "velez_redesign":                     # owner 2026-10-22: large, mid and small caps
                        for s_, etf_ in {**_u.MID_CAPS, **_u.SMALL_CAPS}.items():
                            _g.add_sector(s_, etf_)
                        syms58 = sorted(set(syms58) | set(_u.MID_CAPS) | set(_u.SMALL_CAPS))
                    m58 = {}
                    for s_ in syms58:
                        try:
                            m58[s_] = minute[s_] if s_ in minute else load(s_, "1Min", mstart, end, cache)
                        except Exception as exc:
                            print(f"skip {s_}: {exc}", flush=True)
                    d58 = {s_: load(s_, "1Day", end - _td(days=700), end, cache) for s_ in m58}
                    days = add_gates(split_days(m58, minutes=int(getattr(a, "bar_min", 5)), feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    del m58
                    days = attach(days, d58)
                    spy_d = d58.get("SPY") if "SPY" in d58 else load("SPY", "1Day", end - _td(days=700), end, cache)
                    try:
                        neg = sector_news_negative(news_fetch(list(_u.STOCKS), end - _td(days=200), end, cache / "news"), _u.SECTOR_OF, spy_d.index)
                        src = "sector NEWS"
                    except Exception as exc:
                        neg = sector_trend_negative({e: load(e, "1Day", end - _td(days=700), end, cache) for e in _u.SECTOR_ETFS})
                        src = f"sector TREND (news unavailable: {type(exc).__name__})"
                    lok, sok = lists(d58, spy_d, _u.SECTOR_OF, neg)
                    alt = {"ts_ok2": lists(d58, spy_d, _u.SECTOR_OF, neg, need=2), "ts_ok5": lists(d58, spy_d, _u.SECTOR_OF, neg, top=5)}
                    nl = ns = nd = 0
                    for s_, frames in days.items():
                        for f_ in frames:
                            sd = f_.index[0].date()
                            f_.attrs["ts_ok"] = (bool(for_session(lok, s_, sd)), bool(for_session(sok, s_, sd)))
                            for k_, (l_, sh_) in alt.items():
                                f_.attrs[k_] = (bool(for_session(l_, s_, sd)), bool(for_session(sh_, s_, sd)))
                            nl += f_.attrs["ts_ok"][0]
                            ns += f_.attrs["ts_ok"][1]
                            nd += 1
                    lines.append(f"- daily lists from {src}: a stock was on the LONG list {nl} of {nd} stock-days, on the SHORT list {ns}")
                    if fam == "velez_redesign":
                        from .research import velez_redesign_study
                        groups = {"large": set(_u.STOCKS), "mid": set(_u.MID_CAPS), "small": set(_u.SMALL_CAPS)}
                        lines.append("- loaded: " + ", ".join(f"{g} {len([x for x in v if x in days])}" for g, v in groups.items()))
                        res = velez_redesign_study(days, train, test, groups, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_vzr.json",
                                                   neg_bars=2)                     # owner 2026-10-22: negative 10 minutes max, then out
                    elif fam == "velez_repair":
                        from .research import velez_repair_study
                        res = velez_repair_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_vrep.json")
                    elif fam == "velez_all":
                        from .research import velez_all_study
                        res = velez_all_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_vall_b{int(getattr(a, 'bar_min', 5))}.json")
                    elif fam == "winrate_live":
                        from .research import winrate_live_check
                        res = winrate_live_check(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_wl.json")
                    elif fam == "winrate_tune":
                        from .research import winrate_tune_study
                        res = winrate_tune_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_wr.json")
                    elif fam == "velez_hybrid":
                        from .research import velez_hybrid_study
                        res = velez_hybrid_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_vhy.json")
                    elif fam == "velez4":
                        from .research import velez4_study
                        res = velez4_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_velez4.json")
                    elif fam == "velez3":
                        from .research import velez3_study
                        res = velez3_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_velez3.json")
                    elif fam == "velez2":
                        from .research import velez2_study
                        res = velez2_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_velez2.json")
                    elif fam == "velez":
                        from .research import velez_study
                        res = velez_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_velez.json")
                    elif fam == "scalper":
                        from .research import scalper_study
                        res = scalper_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_scalper.json")
                    elif fam == "oversold_gate":
                        from .research import oversold_gate_study
                        res = oversold_gate_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_og.json")
                    elif fam == "fixed_r":
                        from .funnel import backtest as funnel_bt
                        from .research import fixed_r_study
                        res = fixed_r_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_fixedr.json")
                        fbd = {s_: d_ for s_, d_ in d58.items() if s_ in _u.STOCKS}
                        for label, a_ in (("last 3 months", end - _td(days=92)), ("last 12 months", end - _td(days=365))):
                            for nm, rules in (("as built (stop 1.0, out at 1.5)", {}), ("stop 0.5, out at 1.5", {"stop_frac": 0.5}),
                                              ("stop 0.5, out at 1.0", {"stop_frac": 0.5, "tp_r": 1.0}), ("stop 0.5, out at 1.25", {"stop_frac": 0.5, "tp_r": 1.25}), ("stop 0.25, out at 0.75", {"stop_frac": 0.25, "tp_r": 0.75}),
                                              ("stop 1.0, out at 3.0", {"tp_r": 3.0})):
                                m_ = funnel_bt(fbd, a_, end - _td(days=1), sides=("long",), rules=rules)
                                lines.append(f"- funnel longs (daily), {label}, {nm}: {m_['trades']} trades, win {m_['win_rate']}, PF {m_['profit_factor']}, "
                                             f"avg R {m_['avg_r']}, total {m_['longs_r']:+}R, exits {m_['exits']}")
                    elif fam == "no_stops":
                        from .funnel import backtest as funnel_bt
                        from .research import no_stop_study
                        res = no_stop_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_nostop.json")
                        fbd = {s_: d_ for s_, d_ in d58.items() if s_ in _u.STOCKS}
                        for label, a_ in (("last 3 months", end - _td(days=92)), ("last 12 months", end - _td(days=365))):
                            for nm, ns in (("WITH stops", False), ("NO stops", True)):
                                m_ = funnel_bt(fbd, a_, end - _td(days=1), sides=("long",), no_stop=ns)
                                lines.append(f"- funnel longs (daily), {label}, {nm}: {m_['trades']} trades, win {m_['win_rate']}, PF {m_['profit_factor']}, "
                                             f"avg R {m_['avg_r']}, total {m_['longs_r']:+}R, worst {min([t['r'] for t in m_['list']] or [0]):+.2f}R, exits {m_['exits']}")
                    elif fam == "trend_stairs58":
                        res = trend_stairs58_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_ts58.json")
                    else:
                        from .research import trend_stairs58_opt
                        res = trend_stairs58_opt(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_ts58opt.json")
                        lines.append(f"- best on train: {res['best_on_train']} | adopted: {res['adopted']} | {res['verdict']}")
                elif fam == "reversal_levels":      # owner 2026-10-17: at which indicator levels price usually reverses (alpacabot.levels)
                    from .engine import resample_bars
                    from .levels import report_lines, study
                    from . import universe as _u
                    from .paper import SYMBOLS as _PS
                    lv = {}
                    for s_ in sorted(set(_PS) | set(_u.STOCKS)):       # the live paper universe (58), not just the 10 backtest symbols
                        try:
                            lv[s_] = resample_bars(minute[s_] if s_ in minute else load(s_, "1Min", mstart, end, cache), 5)
                        except Exception as exc:
                            print(f"skip {s_}: {exc}", flush=True)
                    res = study(lv, train[0], test[1])
                    del lv
                    lines += report_lines(res)
                    (out / f"reversal_levels{qtag}.json").write_text(json.dumps(res, indent=1, default=str))
                elif fam == "expert_breakdown":     # each expert alone through the live configuration (research.expert_breakdown)
                    from .research import expert_breakdown
                    from .zonemap import attach
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    days = attach(days, {s: load(s, "1Day", start, end, cache) for s in intraday_syms})
                    res = expert_breakdown(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_experts.json")
                    lines.append(f"- DROP: {res['drop']} | KEEP: {res['keep']}")
                elif fam == "trend_continuation":   # candle / volume / volatility check on the trend experts (research.trend_continuation_study)
                    from .research import trend_continuation_study
                    from .zonemap import attach
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    days = attach(days, {s: load(s, "1Day", start, end, cache) for s in intraday_syms})
                    res = trend_continuation_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_tcont.json")
                elif fam == "box_confirm":      # candle + volume + volatility box on stocks (research.box_confirm_study)
                    from .research import box_confirm_study
                    from .zonemap import attach
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    days = attach(days, {s: load(s, "1Day", start, end, cache) for s in intraday_syms}, with_box=True)
                    res = box_confirm_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_boxc.json")
                elif fam == "box_diag":         # where the range box strategy's signals go (research.box_diag)
                    from .research import box_diag
                    from .zonemap import attach
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    days = attach(days, {s: load(s, "1Day", start, end, cache) for s in intraday_syms}, with_box=True)
                    res = {"family": "box_diag", "verdict": "counts: " + json.dumps(box_diag(days, train[0], test[1]))}
                elif fam == "box_strategy":     # the owner's range box as its own stock strategy (research.box_strategy_study)
                    from .research import box_strategy_study
                    from .zonemap import attach
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    days = attach(days, {s: load(s, "1Day", start, end, cache) for s in intraday_syms}, with_box=True)
                    res = box_strategy_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_boxs.json")
                elif fam == "box_range":        # the owner's OANDA range box on the stock range experts (research.box_range_study)
                    from .research import box_range_study
                    from .zonemap import attach
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    days = attach(days, {s: load(s, "1Day", start, end, cache) for s in intraday_syms}, with_box=True)
                    res = box_range_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_boxr.json")
                elif fam == "zones_params":     # tune the zones' own settings on the frozen grid (research.zones_params_study)
                    from .research import zones_params_study
                    from .zonemap import attach
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    days = attach(days, {s: load(s, "1Day", start, end, cache) for s in intraday_syms})
                    res = zones_params_study(days, train, test, costs, ckpt=out / f"ckpt_{a.phase}{qtag}_zparams.json")
                elif fam == "brain_ls":       # long + short brain with the sector/market heat map gate
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    res = brain_walk_forward(days, train, test, costs, Portfolio(risk_pct=0.01, max_positions=6), spy_daily=spy, shorts=True)
                elif fam == "regime":
                    days = split_days(minute, minutes=5, feature_fn=DualRegime.features)
                    res = walk_forward(fam, days, train, test, costs, Portfolio(risk_pct=0.01, max_positions=6), spy_daily=spy)
                else:
                    days = split_days(minute)
                    res = walk_forward(fam, days, train, test, costs, Portfolio(risk_pct=0.01, max_positions=6), spy_daily=spy)
            res.setdefault("verdict", verdict(res))
        except Exception as exc:  # one family failing must not lose the others
            res = {"family": fam, "verdict": f"ERROR {exc!r}"}
        (out / f"alpaca_{a.phase}{qtag}_{fam}.json").write_text(json.dumps(res, indent=1, default=str))
        m = res.get("test_metrics") or {}
        daily = m.get("daily_ret_pct_mean")
        lines.append(f"## {fam}: {res['verdict']}\n- chosen: {res.get('chosen') or res.get('selected_experts')}\n- test trades {m.get('trades')}, win {m.get('win_rate')}, "
                     f"avg R {m.get('avg_r')}, sharpe {m.get('sharpe')}, cagr {m.get('cagr')}, max DD {m.get('max_drawdown')}, avg daily % {daily}\n"
                     f"- placebo {res.get('placebo')}\n- SPY {res.get('benchmark_buy_hold_SPY')}\n- target 0.75-1.0%/day met: {bool(daily is not None and daily >= 0.75)}\n")
        if fam == "zones_params":
            for per, rows_ in res.get("periods", {}).items():
                for name, m_ in rows_.items():
                    lines.append(f"- {per} | {name}: {m_.get('trades')}t PF {m_.get('profit_factor') or 0:.2f} daily {m_.get('daily_ret_pct_mean') or 0:+.3f}% "
                                 f"maxDD {m_.get('max_drawdown') or 0:.1%}")
            lines.append(f"- chosen on train: {res.get('chosen_changes')}")
        if fam in ("ladder_intraday", "eod_squeeze", "sector_mom", "orb_candidate", "mtf_confirm", "profile_assign", "box_range", "box_strategy", "box_confirm", "trend_continuation", "expert_breakdown", "reversal_confirm", "trend_stairs", "trend_stairs58", "trend_stairs58_opt", "no_stops", "fixed_r", "oversold_gate", "scalper", "velez", "velez2", "velez3", "velez4", "velez_hybrid", "winrate_tune", "winrate_live", "velez_all", "velez_repair", "velez_redesign"):
            for per, rows_ in res.get("periods", {}).items():
                for name, m_ in rows_.items():
                    lines.append(f"- {per} | {name}: {m_.get('trades')}t win {m_.get('win_rate') or 0:.0%} PF {m_.get('profit_factor') or 0:.2f} "
                                 f"avgR {m_.get('avg_r') or 0:+.2f} daily {m_.get('daily_ret_pct_mean') or 0:+.3f}% CAGR {m_.get('cagr') or 0:+.1%} "
                                 f"maxDD {m_.get('max_drawdown') or 0:.1%}" + (f" | stopped {m_['stopped']}, never green {m_['never_green']}" if "stopped" in m_ else ""))
        if fam in ("zones_intraday", "zones_swing", "swing_short"):
            for r in res.get("rows", []):
                w, o = r["with"], r["without"]
                lines.append(f"- {r.get('universe', 'paper brain')} [{r.get('variant', 'zones')}] | {r['period']}: WITH {w.get('trades')}t PF {w.get('profit_factor') or 0:.2f} "
                             f"exp {w.get('expectancy_pct_notional') or 0:+.3f}% maxDD {w.get('max_drawdown') or 0:.1%} | WITHOUT {o.get('trades')}t "
                             f"PF {o.get('profit_factor') or 0:.2f} exp {o.get('expectancy_pct_notional') or 0:+.3f}% maxDD {o.get('max_drawdown') or 0:.1%}")
        if fam == "frozen_systems":
            for sysname, v in res.get("systems", {}).items():
                lines.append(f"### {sysname}: {'PASS' if v['passes'] else 'FAIL'}")
                for r in v["rows"]:
                    x = r["real"]
                    lines.append(f"- {r['universe']} | {r['period']}: {x.get('trades')}t win {x.get('win_rate') or 0:.0%} avgR {x.get('avg_r') or 0:+.2f} "
                                 f"PF {x.get('profit_factor') or 0:.2f} CAGR {x.get('cagr') or 0:+.1%} maxDD {x.get('max_drawdown') or 0:.1%} | "
                                 f"2.5x slip exp {r['slip_x2.5_expectancy'] or 0:+.3f}%"
                                 + (f" | placebo PF {r['placebo_pf_mean']:.2f}, beats {r['beats_placebo_share']:.0%}" if "beats_placebo_share" in r else "")
                                 + f" | PASS {r['passes']}")
        if fam == "breadth_gate":
            for r in res.get("rows", []):
                g, u = r["gated"], r["ungated"]
                lines.append(f"- {r['universe']} | {r['period']}: gated {g.get('trades')}t PF {g.get('profit_factor') or 0:.2f} CAGR {g.get('cagr') or 0:+.1%} "
                             f"maxDD {g.get('max_drawdown') or 0:.1%} vs ungated {u.get('trades')}t PF {u.get('profit_factor') or 0:.2f} "
                             f"CAGR {u.get('cagr') or 0:+.1%} maxDD {u.get('max_drawdown') or 0:.1%}"
                             + (f" | placebo PF {r['placebo_pf_mean']:.2f}, beats {r['beats_placebo_share']:.0%}" if "beats_placebo_share" in r else "")
                             + f" | PASS {r['passes']}")
        if fam == "filters" and res.get("table"):
            lines.append("| route | mtf | agree | sector | shorts | trades | win | exp %/trade | PF | %/day |\n|---|---|---|---|---|---|---|---|---|---|")
            for r in res["table"]:
                lines.append(f"| {r['route']} | {r['mtf']} | {r['consensus']} | {r['sector_filter']} | {r['shorts']} | {r['trades']} | {r['win_rate'] or 0:.0%} | "
                             f"{r['expectancy_pct_notional'] or 0:+.4f} | {r['profit_factor'] or 0:.2f} | {r['daily_ret_pct_mean'] or 0:+.3f} |")
        if fam == "sector_td_robust":
            for uname, u in res.get("universes", {}).items():
                b = u["base"]
                lines.append(f"### {uname} ({u['period'][0]}..{u['period'][1]}): base {b.get('trades')}t PF {b.get('profit_factor') or 0:.2f} "
                             f"CAGR {b.get('cagr') or 0:+.1%} maxDD {b.get('max_drawdown') or 0:.1%} sharpe {b.get('sharpe') or 0:.2f} | SPY CAGR {u['spy'].get('cagr', 0):+.1%}")
                lines.append("- leave one out (CAGR, PF): " + ", ".join(f"-{k} {v.get('cagr') or 0:+.1%}/{v.get('profit_factor') or 0:.2f}" for k, v in u["leave_one_out"].items()))
                lines.append(f"- 27 nearby parameter sets: {u['params_profitable_share'] or 0:.0%} profitable, PF range {u['params_pf_range']}")
                lines.append("- sizing (risk, slots -> CAGR, maxDD, sharpe): " + ", ".join(
                    f"{z['risk_pct']:.2%}/{z['max_positions']} -> {z.get('cagr') or 0:+.1%}, {z.get('max_drawdown') or 0:.1%}, {z.get('sharpe') or 0:.2f}" for z in u["sizing"]))
                bt = u["bootstrap"]
                if bt:
                    lines.append(f"- block bootstrap ({bt['paths']} paths, {bt['block_days']}-day blocks): CAGR p5/p50/p95 {bt['cagr_p5']:+.1%}/{bt['cagr_p50']:+.1%}/{bt['cagr_p95']:+.1%}, "
                                 f"profitable {bt['share_profitable']:.0%}, maxDD median {bt['maxdd_p50']:.1%}, 95th {bt['maxdd_p95']:.1%}, 99th {bt['maxdd_p99']:.1%}")
        if fam in ("swing_validate", "rsi2_validate"):
            for r in res.get("rows", []):
                x = r["real"]
                lines.append(f"- {r['expert']} hold<={r['hold_days']}d: {x.get('trades')}t win {x.get('win_rate') or 0:.0%} exp {x.get('expectancy_pct_notional') or 0:+.3f}% "
                             f"PF {x.get('profit_factor') or 0:.2f} sharpe {x.get('sharpe') or 0:.2f} cagr {x.get('cagr') or 0:+.1%} maxDD {x.get('max_drawdown') or 0:.1%} | "
                             f"2.5x slip exp {r['slip_x2.5_expectancy'] or 0:+.3f}% | placebo mean exp {r['placebo_mean_expectancy'] or 0:+.3f}%, real beats {r['real_beats_placebo_share'] or 0:.0%} | PASS {r['passes']}")
            if res.get("benchmark_buy_hold_SPY"):
                lines.append(f"- SPY buy-and-hold (same years): {res['benchmark_buy_hold_SPY']}")
        if fam == "sector_td":
            for r in res.get("rows", []):
                bh, sp = r["buy_hold_equal_weight"], r["spy"]
                lines.append(f"- {r['universe']} | {r['period']}: {r['trades']} trades, win {r['win_rate'] or 0:.0%}, avgR {r['avg_r'] or 0:+.2f}, PF {r['profit_factor'] or 0:.2f}, "
                             f"return {r['total_return'] or 0:+.1%} (CAGR {r['cagr'] or 0:+.1%}), maxDD {r['max_drawdown'] or 0:.1%} | buy&hold same names {bh.get('total_return', 0):+.1%} "
                             f"(DD {bh.get('max_drawdown', 0):.1%}) | SPY CAGR {sp.get('cagr', 0):+.1%} | by year {r['by_year']} | top P&L {r['top_symbols_pnl']}")
        if fam in ("limit", "swing_experts") and res.get("table"):
            for k, v in res["table"].items():
                if fam == "limit":
                    lines.append(f"- {k}: " + " | ".join(f"{m} {r.get('trades')}t exp {r.get('expectancy_pct_notional') or 0:+.4f}% PF {r.get('profit_factor') or 0:.2f}"
                                                         for m, r in v.items()))
                else:
                    lines.append(f"- {k}: {v.get('trades')}t win {v.get('win_rate') or 0:.0%} exp {v.get('expectancy_pct_notional') or 0:+.3f}% "
                                 f"PF {v.get('profit_factor') or 0:.2f} sharpe {v.get('sharpe') or 0:.2f} cagr {v.get('cagr') or 0:+.1%}")
            if res.get("benchmark_buy_hold_SPY"):
                lines.append(f"- SPY buy-and-hold (same years): {res['benchmark_buy_hold_SPY']}")
        if fam == "exits" and res.get("table"):
            lines.append("| expert / side | own exits: trades, win, exp %/trade | staircase: trades, win, exp %/trade |\n|---|---|---|")
            for k, v in res["table"].items():
                f = lambda r: f"{r.get('trades')}, {r.get('win_rate') or 0:.0%}, {r.get('expectancy_pct_notional') or 0:+.3f}"   # noqa: E731
                lines.append(f"| {k} | {f(v['own'])} | {f(v['staircase'])} |")
        (out / f"alpaca_{a.phase}{qtag}_summary{suffix}.md").write_text("\n".join(lines))
        print("\n".join(lines[first:]), flush=True)
    print("ALL DONE")
    return 0


def main(argv=None) -> int:
    load_env()
    ap = argparse.ArgumentParser(prog="alpacabot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("download", "backtest"):
        p = sub.add_parser(name)
        p.add_argument("--family", choices=list(GRIDS) + ["brain"], required=name == "backtest")
        p.add_argument("--start", type=_d, default=date(2016, 1, 1))
        p.add_argument("--end", type=_d, default=date.today())
        p.add_argument("--cache", default=os.environ.get("ALPACA_CACHE", "state/alpaca_cache"))
        p.add_argument("--symbols", nargs="*")
    ra = sub.add_parser("run-all", help="download data, then run every family on the VALIDATION split (train 2016-2020, validate 2021-2022); writes a summary")
    ra.add_argument("--cache", default=os.environ.get("ALPACA_CACHE", "state/alpaca_cache"))
    ra.add_argument("--out", default="state/reports")
    ra.add_argument("--phase", choices=["validate", "final"], default="validate",
                    help="validate: train 2016-2020, test 2021-2022 (design iterations allowed). final: train 2016-2022, test 2023-today, run ONCE after freezing")
    ra.add_argument("--weeks2", action="store_true", help="validate phase: the last two weeks only (two halves); fastest look, provisional")
    ra.add_argument("--bar-min", type=int, default=5, help="intraday bar length in minutes for the 58-stock studies (owner 2026-10-21: 2-minute Velez test)")
    ra.add_argument("--month1", action="store_true", help="validate phase: the last month only (two halves); quickest look, provisional")
    ra.add_argument("--months3", action="store_true", help="validate phase: the last 3 months only (train = first half, validate = second half); fast, provisional")
    ra.add_argument("--year", action="store_true", help="validate phase: ONE year of data (train 2021-H2, validate 2022-H1); small memory, provisional")
    ra.add_argument("--quick", action="store_true", help="validate phase: 2019-2022 only and fewer exit variants (provisional, ~1/3 of the time)")
    ra.add_argument("--families", nargs="*", default=["swing_rsi", "swing_breakout", "day", "regime", "brain", "brain_ls", "scalp"])
    ra.add_argument("--slip-bps", type=float, default=2.0)
    ra.add_argument("--symbols", nargs="*", help="intraday families: these symbols instead of the default liquid list (e.g. mid / small caps)")
    pp = sub.add_parser("paper", help="run the PAPER trading loop (Alpaca paper account only)")
    pp.add_argument("--state", default="state/alpaca_paper")
    cv = sub.add_parser("coverage", help="bars per year per symbol (is the history really there?)")
    cv.add_argument("--cache", default=os.environ.get("ALPACA_CACHE", "state/alpaca_cache"))
    cv.add_argument("--timeframe", default="1Day", choices=["1Day", "1Min"])
    cv.add_argument("--feed", default=None)
    cv.add_argument("--start", type=_d, default=date(2016, 1, 1))
    cv.add_argument("--end", type=_d, default=date.today())
    sc = sub.add_parser("symbol-check", help="should these stocks join the paper universe? (the current brain on each stock, 2016-2022)")
    sc.add_argument("--symbols", nargs="+", required=True)
    sc.add_argument("--sectors", nargs="*", default=[], help="SYMBOL=ETF pairs for stocks outside the built-in map, e.g. XOM=XLE")
    sc.add_argument("--cache", default=os.environ.get("ALPACA_CACHE", "state/alpaca_cache"))
    sw = sub.add_parser("swing", help="daily Sector Top-Down swing runner (PAPER): scan after the close, open before the open, status")
    sw.add_argument("action", choices=["scan", "open", "status", "close"])
    sw.add_argument("--state", default="state/alpaca_swing")
    co = sub.add_parser("close-others", help="close every position and cancel every order that is NOT the new bot's (dry run unless --execute)")
    co.add_argument("--execute", action="store_true")
    co.add_argument("--state", default="state/alpaca_paper")
    ps_ = sub.add_parser("positions", help="read-only: every open position and open order on the paper account")
    ps_.add_argument("--fx", action="store_true", help="the FOREX practice runner instead (fxbot; OANDA practice, separate book)")
    ps_.add_argument("--closed", help="also today's closed orders (OCO legs included) for these comma-separated symbols, e.g. GE,GOOGL")
    hs = sub.add_parser("history", help="read-only: which bot traded which symbols, P&L, win rate, hold times (paper account)")
    hs.add_argument("--days", type=int, default=365)
    hs.add_argument("--stocks-only", action="store_true", help="leave crypto out")
    sub.add_parser("check", help="are the keys present and do they open the PAPER account (values never printed)")
    sub.add_parser("paper-report").add_argument("--state", default="state/alpaca_paper")
    sub.add_parser("paper-telegram", help="resend today's report card to Telegram (delivery check)").add_argument("--state", default="state/alpaca_paper")
    pt = sub.add_parser("paper-trades", help="read-only: the intraday ledger rows, optionally for one expert tag (e.g. velez_rbi, S:velez_bt)")
    pt.add_argument("--expert"); pt.add_argument("--state", default="state/alpaca_paper")
    pt.add_argument("--by-expert", action="store_true", help="one line per expert: the stocks it traded, trades and P&L")
    sub.add_parser("paper-flatten", help="close the intraday bot's own positions now (the running service does it within 15 s)").add_argument(
        "--state", default="state/alpaca_paper")
    pz = sub.add_parser("paper-pause", help="on: the intraday bot takes no new entries (open trades keep their exits); off: resume")
    pz.add_argument("mode", choices=["on", "off"])
    pz.add_argument("--state", default="state/alpaca_paper")
    pz.add_argument("--reason", default="paused by the owner")
    sub.add_parser("paper-eod", help="take today's equity snapshot and send the report card now, if not already done").add_argument("--state", default="state/alpaca_paper")
    bt = sub.choices["backtest"]
    bt.add_argument("--train-end", type=_d, default=date(2021, 12, 31))
    bt.add_argument("--slip-bps", type=float, default=2.0)
    bt.add_argument("--equity", type=float, default=100_000.0)
    bt.add_argument("--placebo", type=int, default=20)
    bt.add_argument("--out", default="state/reports")
    a = ap.parse_args(argv)
    if a.cmd == "symbol-check":
        from . import gates
        from .paper import default_brain
        for pair in a.sectors:
            sym, etf = pair.split("=")
            gates.add_sector(sym.upper(), etf.upper())
        start, end = date(2016, 1, 4), date(2022, 12, 31)
        cache = Path(a.cache)
        syms = [x.upper() for x in a.symbols]
        minute = {s: load(s, "1Min", start, end, cache) for s in sorted(set(syms) | {"SPY"})}
        days = add_gates(split_days(minute, minutes=5, feature_fn=features_both), {s: load(s, "1Day", start, end, cache) for s in gates.HEAT_ETFS})
        b = default_brain("state/alpaca_paper")
        kw = {k: getattr(b, k) for k in ("experts", "short_experts", "priority", "route", "mtf", "consensus", "sector_filter", "short_gate", "max_trades", "cooldown")}
        res = symbol_check({s: days[s] for s in syms if s in days}, (start, date(2020, 12, 31)), (date(2021, 1, 1), end), Costs(), brain_kwargs=kw)
        for s, r in res.items():
            print(f"{s:6s} {'QUALIFIES' if r['qualifies'] else 'no':9s} train {r['train']}  validate {r['validate']}  2.5x slip {r['validate_slip_x2.5']}")
        return 0
    if a.cmd == "swing":
        from datetime import datetime as _dt

        from .paper import RestBroker
        from .swing_paper import ET as _ET, SwingRunner, summary
        if a.action == "status":
            print("\n".join(summary(a.state)) or "swing runner has not run yet")
            return 0
        if not all(credentials()):
            print("WAITING FOR KEYS")
            return 2
        setup = __import__("alpacabot.execution", fromlist=["setup_logging"]).setup_logging
        setup(Path(a.state) / "swing.log")
        r = SwingRunner(RestBroker(), state_dir=a.state)
        now = _dt.now(_ET)
        out = r.scan(now) if a.action == "scan" else r.close_all(now) if a.action == "close" else r.open(now)
        if a.action == "open":                           # owner 2026-10-23: deferred (oversold) entries at swing.late_entry.at (10:00)
            late = r.open(_dt.now(_ET), late=True)
            if late:
                out = {**(out or {}), **{f"late {k}": v for k, v in late.items()}}
        print(json.dumps(out, indent=1, default=str))
        print("\n".join(summary(a.state)))
        return 0
    if a.cmd == "close-others":
        import requests

        from .data import _headers
        from .exits import StateStore
        api = "https://paper-api.alpaca.markets/v2"
        h = _headers()
        own = {s: p["qty"] * p.get("side", 1) for s, p in StateStore(Path(a.state) / "positions.json").load().items()}
        swing_state = Path(a.state).parent / "alpaca_swing" / "state.json"
        if swing_state.exists():
            for s_, p_ in (json.loads(swing_state.read_text()).get("positions") or {}).items():
                own[s_] = own.get(s_, 0) + int(p_["qty"])
        pos = requests.get(f"{api}/positions", headers=h, timeout=30).json()
        orders = requests.get(f"{api}/orders", headers=h, params={"status": "open", "limit": 500}, timeout=30).json()
        cancel = [o for o in orders if not str(o.get("client_order_id") or "").startswith(("ab-", "sw-"))]
        close, keep, mixed = [], [], []
        for p in pos:
            sym, q = p["symbol"], abs(int(float(p["qty"]))) * (-1 if p.get("side") == "short" else 1)
            if sym not in own:
                close.append((sym, q))
            elif q == own[sym]:
                keep.append((sym, q))
            else:
                mixed.append((sym, q, own[sym]))
        print(f"new bot holds: {own or 'nothing'}")
        print(f"KEEP (the new bot's): {keep}")
        print(f"CANCEL {len(cancel)} orders not from the new bot: " + ", ".join(f"{o['symbol']} {o['side']} {o['type']}" for o in cancel))
        print(f"CLOSE {len(close)} positions not from the new bot: " + ", ".join(f"{s} {q:+d}" for s, q in close))
        if mixed:
            print(f"NOT TOUCHED (shared with the new bot, close the difference by hand): {mixed}")
        if not a.execute:
            print("DRY RUN: nothing was sent. Re-run with --execute to do it.")
            return 0
        for o in cancel:
            r = requests.delete(f"{api}/orders/{o['id']}", headers=h, timeout=30)
            print("cancel", o["symbol"], r.status_code)
        import time as _t
        _t.sleep(3)
        for sym, q in close:
            r = requests.delete(f"{api}/positions/{sym}", headers=h, timeout=30)
            print("close", sym, q, r.status_code, (r.text[:120] if r.status_code >= 300 else ""))
        return 0
    if a.cmd == "positions" and a.fx:                   # forex lives in fxbot; only routed through here for the VM link
        from fxbot.cli import main as fx_main
        rc = fx_main(["paper-status"])
        try:                                            # the REAL OANDA account (shared with other bots), read-only
            from fxbot.oanda import Oanda
            o = Oanda()
            sm = o.summary()
            print(f"\nOANDA ACCOUNT (all bots on it): NAV ${float(sm.get('NAV', 0)):,.2f}  balance ${float(sm.get('balance', 0)):,.2f}  "
                  f"unrealized ${float(sm.get('unrealizedPL', 0)):+,.2f}  realized P&L (lifetime) ${float(sm.get('pl', 0)):+,.2f}  "
                  f"open trades {sm.get('openTradeCount')}")
            for t in o.open_trades():
                tag = (t.get("clientExtensions") or {}).get("tag", "-")
                ce = t.get("clientExtensions") or {}
                sl, tp = (t.get("stopLossOrder") or {}).get("price", "-"), (t.get("takeProfitOrder") or {}).get("price", "-")
                print(f"  {t.get('instrument')} {t.get('currentUnits')} @ {t.get('price')} unrealized {float(t.get('unrealizedPL', 0)):+,.2f} tag {tag}"
                      f" | opened {str(t.get('openTime', '-'))[:16]} sl {sl} tp {tp} id {str(ce.get('id', '-'))[:40]} note {str(ce.get('comment', '-'))[:40]}")
        except Exception as exc:
            print(f"OANDA account read failed: {type(exc).__name__}: {str(exc)[:150]}")
        return rc
    if a.cmd == "positions":
        import requests

        from .data import _headers
        api = "https://paper-api.alpaca.markets/v2"
        acct = requests.get(f"{api}/account", headers=_headers(), timeout=30).json()
        pos = requests.get(f"{api}/positions", headers=_headers(), timeout=30).json()
        orders = requests.get(f"{api}/orders", headers=_headers(), params={"status": "open", "limit": 500}, timeout=30).json()
        print(f"equity ${float(acct.get('equity', 0)):,.2f}  cash ${float(acct.get('cash', 0)):,.2f}  long mv ${float(acct.get('long_market_value', 0)):,.2f}"
              f"  short mv ${float(acct.get('short_market_value', 0)):,.2f}")
        print(f"{len(pos)} open positions:")
        for p in sorted(pos, key=lambda p: -abs(float(p.get('market_value') or 0))):
            print(f"  {p['symbol']:10s} {p.get('side', ''):5s} qty {p['qty']:>12s}  value ${float(p.get('market_value') or 0):>10,.2f}  "
                  f"unrealized ${float(p.get('unrealized_pl') or 0):>9,.2f}")
        print(f"{len(orders)} open orders:")
        for o in orders:
            print(f"  {o['symbol']:10s} {o['side']:4s} {o.get('qty') or o.get('notional')} {o['type']} {o['status']}  id {str(o.get('client_order_id'))[:24]}")
        if getattr(a, "closed", None):                       # read-only: today's closed orders (OCO legs included) for these symbols
            done = requests.get(f"{api}/orders", headers=_headers(), timeout=30,
                                params={"status": "closed", "limit": 500, "nested": "true", "after": date.today().isoformat() + "T00:00:00Z",
                                        **({} if a.closed.upper() == "ALL" else {"symbols": a.closed})}).json()
            print(f"closed orders today for {a.closed}:")
            for o in done:
                for x in [o] + list(o.get("legs") or []):
                    print(f"  {x['symbol']:6s} {x['side']:4s} {x.get('qty')} {x['type']:10s} {x['status']:9s} filled {x.get('filled_qty')} @ "
                          f"{x.get('filled_avg_price')} at {str(x.get('filled_at'))[:19]}  stop {x.get('stop_price')} limit {x.get('limit_price')}  "
                          f"id {str(x.get('client_order_id'))[:24]}")
        return 0
    if a.cmd == "history":
        from .history import report
        print(report(a.days, a.stocks_only))
        return 0
    if a.cmd == "coverage":
        print(coverage(["SPY", "AAPL", "NVDA", "XLK", "XLC"], a.timeframe, a.start, a.end, Path(a.cache), a.feed))
        return 0
    if a.cmd == "check":
        from .paper import RestBroker
        key, secret = credentials()
        print("keys found in the environment:", bool(key and secret))
        if key and secret:
            try:
                print("paper account equity: $%s, market open: %s" % (format(RestBroker().equity(), ",.0f"), RestBroker().clock_open()))
            except Exception as exc:
                print("paper account check FAILED (live keys or a typo?):", type(exc).__name__, str(exc)[:120])
                return 1
        return 0 if key and secret else 2
    if a.cmd == "paper":
        from .paper import main as paper_main
        return paper_main(a)
    if a.cmd == "paper-flatten":
        f = Path(a.state) / "FLATTEN_NOW"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("flatten this bot's own intraday positions now (owner)\n")
        print("intraday bot will close its own positions within 15 s (swing positions are not touched)")
        return 0
    if a.cmd == "paper-pause":
        k = Path(a.state) / "KILL_SWITCH"
        if a.mode == "on":
            k.parent.mkdir(parents=True, exist_ok=True)
            k.write_text(a.reason + "\nDelete this file (or run: python -m alpacabot paper-pause off) to resume new entries.\n")
        elif k.exists():
            k.unlink()
        print(f"intraday entries {'PAUSED' if a.mode == 'on' else 'RESUMED'} (the running service picks this up within 15 s)")
        return 0
    if a.cmd == "paper-eod":
        from datetime import datetime as _dt

        from .paper import ET as _PET, PaperRunner, RestBroker
        r = PaperRunner(RestBroker(), state_dir=a.state)
        now = _dt.now(_PET)
        if r.reported == now.date():
            print("already sent today")
            return 0
        r.end_of_day(now)
        print((Path(a.state) / "report_card.txt").read_text() if (Path(a.state) / "report_card.txt").exists() else "no card written")
        return 0
    if a.cmd == "paper-trades":
        rows = [json.loads(l) for l in (Path(a.state) / "trades.jsonl").read_text().splitlines() if l.strip()]
        rows = [t for t in rows if a.expert is None or t.get("expert") == a.expert]
        keys = ("closed", "expert", "symbol", "side", "qty", "entry", "exit", "pnl", "r", "peak_r", "reason", "partial")
        if a.by_expert:                                   # one line per expert: its stocks (trades, P&L) and its total
            from collections import defaultdict
            agg = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
            for t in rows:
                if not t.get("partial"):
                    agg[t.get("expert")][t["symbol"]][0] += 1
                agg[t.get("expert")][t["symbol"]][1] += t["pnl"]
            for ex, syms in sorted(agg.items(), key=lambda kv: -sum(v[0] for v in kv[1].values())):
                n, pl = sum(v[0] for v in syms.values()), sum(v[1] for v in syms.values())
                print(f"{ex}: {n} trades, P&L ${pl:+,.2f} | " + ", ".join(f"{s} {v[0]} (${v[1]:+,.0f})" for s, v in sorted(syms.items(), key=lambda kv: -kv[1][0])))
        else:
            for t in rows:
                print(" ".join(f"{k}={t.get(k)}" for k in keys))
        print(f"{len(rows)} rows, P&L ${sum(t['pnl'] for t in rows):+,.2f}")
        return 0
    if a.cmd == "paper-telegram":                       # resend today's saved report card to the owner's chat (delivery check)
        from .paper import send_telegram
        card = Path(a.state) / "report_card.txt"
        print("sent" if send_telegram(card.read_text() if card.exists() else "Alpaca paper bot: Telegram test") else "NOT sent (see the log line above)")
        return 0
    if a.cmd == "paper-report":
        from .paper import report
        print(report(a.state))
        return 0
    if a.cmd == "run-all":
        return run_all(a)
    cache = Path(a.cache)
    if a.cmd == "download":
        for sym in a.symbols or SWING:
            load(sym, "1Day", a.start, a.end, cache)
            if sym in INTRADAY:
                load(sym, "1Min", a.start, a.end, cache)
            print("ok", sym, flush=True)
        return 0
    if a.family == "brain":
        syms = a.symbols or INTRADAY
        spy = load("SPY", "1Day", a.start, a.end, cache)
        data = split_days({s: load(s, "1Min", a.start, a.end, cache) for s in syms}, minutes=5, feature_fn=features_all)
        res = brain_walk_forward(data, (a.start, a.train_end), (date.fromordinal(a.train_end.toordinal() + 1), a.end), Costs(slip_bps=a.slip_bps),
                                 Portfolio(equity=a.equity, risk_pct=0.01, max_positions=6), placebo_runs=a.placebo, spy_daily=spy)
        Path(a.out).mkdir(parents=True, exist_ok=True)
        (Path(a.out) / "alpaca_brain.json").write_text(json.dumps(res, indent=1, default=str))
        print(json.dumps(res, indent=1, default=str))
        return 0
    intraday = a.family in ("scalp", "day", "regime")
    syms = a.symbols or (INTRADAY if intraday else SWING)
    spy = load("SPY", "1Day", a.start, a.end, cache)
    if intraday:
        regime = a.family == "regime"
        data = split_days({s: load(s, "1Min", a.start, a.end, cache) for s in syms}, minutes=5 if regime else 1,
                           feature_fn=DualRegime.features if regime else None)
    else:
        data = {s: load(s, "1Day", a.start, a.end, cache) for s in syms}
    res = walk_forward(a.family, data, (a.start, a.train_end), (date.fromordinal(a.train_end.toordinal() + 1), a.end),
                       Costs(slip_bps=a.slip_bps), Portfolio(equity=a.equity), placebo_runs=a.placebo, spy_daily=spy)
    res["verdict"] = verdict(res)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / f"alpaca_{a.family}.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res.get(k) for k in ("family", "train", "test", "chosen", "train_metrics", "test_metrics", "stress", "placebo",
                                              "benchmark_buy_hold_SPY", "verdict", "note")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
