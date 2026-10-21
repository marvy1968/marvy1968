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
    if a.phase == "validate" and getattr(a, "month1", False):   # --month1 (owner: "a quick one-month test"): the last month, two halves
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
    intraday_syms = INTRADAY
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
                for label, a_ in (("last 3 months", end2 - _td(days=92)), ("last 12 months", end2 - _td(days=365))):
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
                elif fam in ("trend_stairs58", "trend_stairs58_opt", "no_stops", "fixed_r", "oversold_gate", "scalper", "velez", "velez2", "velez3"):   # owner's drawings on all 58 stocks (+ the no-stop retest)
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
                    m58 = {}
                    for s_ in syms58:
                        try:
                            m58[s_] = minute[s_] if s_ in minute else load(s_, "1Min", mstart, end, cache)
                        except Exception as exc:
                            print(f"skip {s_}: {exc}", flush=True)
                    d58 = {s_: load(s_, "1Day", end - _td(days=700), end, cache) for s_ in m58}
                    days = add_gates(split_days(m58, minutes=5, feature_fn=features_both), {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
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
                    if fam == "velez3":
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
        if fam in ("ladder_intraday", "orb_candidate", "mtf_confirm", "profile_assign", "box_range", "box_strategy", "box_confirm", "trend_continuation", "expert_breakdown", "reversal_confirm", "trend_stairs", "trend_stairs58", "trend_stairs58_opt", "no_stops", "fixed_r", "oversold_gate", "scalper", "velez", "velez2", "velez3"):
            for per, rows_ in res.get("periods", {}).items():
                for name, m_ in rows_.items():
                    lines.append(f"- {per} | {name}: {m_.get('trades')}t win {m_.get('win_rate') or 0:.0%} PF {m_.get('profit_factor') or 0:.2f} "
                                 f"avgR {m_.get('avg_r') or 0:+.2f} daily {m_.get('daily_ret_pct_mean') or 0:+.3f}% CAGR {m_.get('cagr') or 0:+.1%} "
                                 f"maxDD {m_.get('max_drawdown') or 0:.1%}")
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
    ra.add_argument("--month1", action="store_true", help="validate phase: the last month only (two halves); quickest look, provisional")
    ra.add_argument("--months3", action="store_true", help="validate phase: the last 3 months only (train = first half, validate = second half); fast, provisional")
    ra.add_argument("--year", action="store_true", help="validate phase: ONE year of data (train 2021-H2, validate 2022-H1); small memory, provisional")
    ra.add_argument("--quick", action="store_true", help="validate phase: 2019-2022 only and fewer exit variants (provisional, ~1/3 of the time)")
    ra.add_argument("--families", nargs="*", default=["swing_rsi", "swing_breakout", "day", "regime", "brain", "brain_ls", "scalp"])
    ra.add_argument("--slip-bps", type=float, default=2.0)
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
    sub.add_parser("positions", help="read-only: every open position and open order on the paper account").add_argument(
        "--fx", action="store_true", help="the FOREX practice runner instead (fxbot; OANDA practice, separate book)")
    hs = sub.add_parser("history", help="read-only: which bot traded which symbols, P&L, win rate, hold times (paper account)")
    hs.add_argument("--days", type=int, default=365)
    hs.add_argument("--stocks-only", action="store_true", help="leave crypto out")
    sub.add_parser("check", help="are the keys present and do they open the PAPER account (values never printed)")
    sub.add_parser("paper-report").add_argument("--state", default="state/alpaca_paper")
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
                print(f"  {t.get('instrument')} {t.get('currentUnits')} @ {t.get('price')} unrealized {float(t.get('unrealizedPL', 0)):+,.2f} tag {tag}")
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
