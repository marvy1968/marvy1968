"""python -m alpacabot download|backtest ...   (run on the VM: needs Alpaca data keys in the environment)"""

import argparse
import json
import os
from datetime import date
from pathlib import Path

from .data import load
from .engine import Costs, Portfolio, split_days
from .research import GRIDS, verdict, walk_forward
from .strategies import DualRegime

INTRADAY = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "AMD"]
SWING = INTRADAY + ["DIA", "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "GLD", "TLT"]


def _d(s: str) -> date:
    return date.fromisoformat(s)


def load_env():
    for p in (os.environ.get("MARV_ENV"), "/opt/marv-bot/.env", ".env"):
        if p and Path(p).exists():
            for line in Path(p).read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.split(" #")[0].strip().strip('"').strip("'"))


def main(argv=None) -> int:
    load_env()
    ap = argparse.ArgumentParser(prog="alpacabot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("download", "backtest"):
        p = sub.add_parser(name)
        p.add_argument("--family", choices=list(GRIDS), required=name == "backtest")
        p.add_argument("--start", type=_d, default=date(2016, 1, 1))
        p.add_argument("--end", type=_d, default=date.today())
        p.add_argument("--cache", default=os.environ.get("ALPACA_CACHE", "state/alpaca_cache"))
        p.add_argument("--symbols", nargs="*")
    bt = sub.choices["backtest"]
    bt.add_argument("--train-end", type=_d, default=date(2021, 12, 31))
    bt.add_argument("--slip-bps", type=float, default=2.0)
    bt.add_argument("--equity", type=float, default=100_000.0)
    bt.add_argument("--placebo", type=int, default=20)
    bt.add_argument("--out", default="state/reports")
    a = ap.parse_args(argv)
    cache = Path(a.cache)
    if a.cmd == "download":
        for sym in a.symbols or SWING:
            load(sym, "1Day", a.start, a.end, cache)
            if sym in INTRADAY:
                load(sym, "1Min", a.start, a.end, cache)
            print("ok", sym, flush=True)
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
