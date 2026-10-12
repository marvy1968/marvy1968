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
from .research import GRIDS, brain_walk_forward, exit_study, verdict, walk_forward
from .strategies import DualRegime

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
    if a.phase == "validate":
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
    for fam in a.families:
        try:
            if fam in ("swing_rsi", "swing_breakout"):
                data = {s: load(s, "1Day", start, end, cache) for s in SWING}
                res = walk_forward(fam, data, train, test, costs, Portfolio(risk_pct=0.01), spy_daily=spy)
            else:
                if not minute:
                    minute = {s: load(s, "1Min", start, end, cache) for s in intraday_syms}
                if fam == "brain":
                    days = split_days(minute, minutes=5, feature_fn=features_all)
                    res = brain_walk_forward(days, train, test, costs, Portfolio(risk_pct=0.01, max_positions=6), spy_daily=spy)
                elif fam == "exits":          # staircase vs each expert's own exits, TRAIN years only
                    days = add_gates(split_days(minute, minutes=5, feature_fn=features_both),
                                     {s: load(s, "1Day", start, end, cache) for s in HEAT_ETFS})
                    res = exit_study(days, train, costs, shorts=True)
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
        (out / f"alpaca_{a.phase}_{fam}.json").write_text(json.dumps(res, indent=1, default=str))
        m = res.get("test_metrics") or {}
        daily = m.get("daily_ret_pct_mean")
        lines.append(f"## {fam}: {res['verdict']}\n- chosen: {res.get('chosen') or res.get('selected_experts')}\n- test trades {m.get('trades')}, win {m.get('win_rate')}, "
                     f"avg R {m.get('avg_r')}, sharpe {m.get('sharpe')}, cagr {m.get('cagr')}, max DD {m.get('max_drawdown')}, avg daily % {daily}\n"
                     f"- placebo {res.get('placebo')}\n- SPY {res.get('benchmark_buy_hold_SPY')}\n- target 0.75-1.0%/day met: {bool(daily is not None and daily >= 0.75)}\n")
        if fam == "exits" and res.get("table"):
            lines.append("| expert / side | own exits: trades, win, exp %/trade | staircase: trades, win, exp %/trade |\n|---|---|---|")
            for k, v in res["table"].items():
                f = lambda r: f"{r.get('trades')}, {r.get('win_rate') or 0:.0%}, {r.get('expectancy_pct_notional') or 0:+.3f}"   # noqa: E731
                lines.append(f"| {k} | {f(v['own'])} | {f(v['staircase'])} |")
        (out / f"alpaca_{a.phase}_summary.md").write_text("\n".join(lines))
        print(lines[-1], flush=True)
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
    sub.add_parser("check", help="are the keys present and do they open the PAPER account (values never printed)")
    sub.add_parser("paper-report").add_argument("--state", default="state/alpaca_paper")
    bt = sub.choices["backtest"]
    bt.add_argument("--train-end", type=_d, default=date(2021, 12, 31))
    bt.add_argument("--slip-bps", type=float, default=2.0)
    bt.add_argument("--equity", type=float, default=100_000.0)
    bt.add_argument("--placebo", type=int, default=20)
    bt.add_argument("--out", default="state/reports")
    a = ap.parse_args(argv)
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
