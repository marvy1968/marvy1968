"""Command line. Orders are sent only with --paper, and only to the paper host."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .clock import ET
from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cos1_alpaca")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="one pass over the book")
    run_p.add_argument("--paper", action="store_true", help="place paper orders")
    run_p.add_argument("--dry-run", action="store_true", help="log signals only")
    run_p.add_argument("--config", default=None)

    bt = sub.add_parser("backtest", help="short walk-forward: synthetic, or IEX via --source alpaca")
    bt.add_argument("--source", choices=("synthetic", "alpaca"), default="synthetic")
    bt.add_argument("--months", type=int, default=0, help="0 uses 6 for synthetic and 3 for alpaca")
    bt.add_argument("--cost-mult", type=float, default=2.0, help="also report this cost multiple; 2 means x1 and x2")
    bt.add_argument("--out", default="")

    val = sub.add_parser("validate", help="pre-registered IEX pass/fail; does not enable --paper")
    val.add_argument("--out", default="")

    args = parser.parse_args(argv)
    if args.cmd == "backtest":
        from .backtest import run_backtest, write_report
        from .errors import MissingCredentials

        if args.source == "alpaca":
            months = min(6, max(1, args.months or 3))
        else:
            months = min(6, max(3, args.months or 6))
        try:
            result = run_backtest(
                source=args.source,
                months=months,
                oos_days=months * 21,
                intraday_days=min(63, months * 21),
                cost_mult=args.cost_mult,
            )
        except MissingCredentials:
            print("credentials: MissingCredentials", file=sys.stderr)
            return 2
        except Exception as exc:
            if args.source != "alpaca":
                raise
            print(f"backtest: {type(exc).__name__}", file=sys.stderr)
            return 2
        text = _table(result)
        print(text)
        if args.out:
            write_report(result, Path(args.out))
        return 0

    if args.cmd == "validate":
        from .validate import format_table, run_validation

        result = run_validation()
        print(format_table(result["rows"]))
        print("paper=no  switches are not changed by this command")
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps({"pass_rule": result["pass_rule"], "rows": result["rows"]}, indent=2, default=str))
        return 0

    if args.paper and getattr(args, "dry_run", False):
        print("choose only one of --paper or --dry-run", file=sys.stderr)
        return 2
    if not args.paper and not args.dry_run:
        print("orders disabled: pass --paper to place paper orders or --dry-run to log signals")
        return 0

    from .broker import AlpacaBroker
    from .datafeed import AlpacaData
    from .envfile import load_keys
    from .news import AlpacaNews
    from .runner import Flags, run

    try:
        api_key, secret = load_keys()
    except Exception as exc:
        print(f"credentials: {type(exc).__name__}", file=sys.stderr)
        return 2
    cfg = load_config(args.config)
    root = Path(cfg.paths.state_dir)
    broker = AlpacaBroker(api_key, secret, cfg)
    data = AlpacaData(api_key, secret, cfg)
    result = run(
        cfg,
        broker,
        data,
        Flags(paper=args.paper, dry_run=args.dry_run),
        datetime.now(ET),
        root / "cos1_state.json",
        root / "cos1.sqlite",
        root / "reports",
        root / "cos1.lock",
        news_fetcher=AlpacaNews(api_key, secret, cfg).snapshot,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _table(result: dict) -> str:
    lines = [
        f"feed={result['feed']} source={result['data_source']} oos_days={result['oos_days']} "
        f"universe={','.join(result['universe'])}",
        "cost slip_bps={slip_bps} fee_bps_sell={fee_bps_sell} commission={commission} "
        "max_pos_frac={max_pos_frac} max_positions={max_positions}".format(**result["cost_model"]),
        f"{'mode':<8} {'expert':<16} {'x':>4} {'trades':>6} {'pf':>8} {'roi':>8} {'maxDD':>8} {'avgCostR':>8} {'log':>5}",
    ]
    for row in result["rows"]:
        pf = row["profit_factor"]
        pf_s = "n/a" if pf is None else ("inf" if pf == float("inf") else f"{pf:.2f}")
        mult = row.get("cost_mult", 1)
        lines.append(
            f"{row['mode']:<8} {row['expert']:<16} {mult:>4.1f} {row['trades']:>6} {pf_s:>8} {row['roi']:>8.2%} "
            f"{row['max_dd']:>8.2%} {row['avg_cost_r']:>8.3f} {str(bool(row['log_only'])): >5}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
