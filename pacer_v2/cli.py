"""CLI for log-only pacer v2. Every path is a flag. No orders are sent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adjust import load_team_stats
from .metrics import evaluate, format_table
from .model import decide, default_model, fit_model, recalibrate, split_games
from .off import switched_off
from .parse import load_json, parse_row, read_jsonl, read_rows, write_json, write_jsonl


def main(argv: list[str] | None = None) -> int:
    if switched_off():
        print("pacer_v2: OFF, exiting")
        return 0
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "score":
            return cmd_score(args)
        if args.cmd == "report":
            return cmd_report(args)
        if args.cmd == "backtest":
            return cmd_backtest(args)
        if args.cmd == "recalib":
            return cmd_recalib(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"pacer_v2: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unknown command {args.cmd}")
    return 2


def cmd_score(args) -> int:
    _require_inputs(args.inputs)
    model = _load_model(args.model, create_note=True)
    stats = _stats(args.team_stats)
    out = Path(args.out)
    existing: dict[str, dict] = {}
    if out.exists():
        for obj, _origin in read_jsonl([out]):
            key = obj.get("key")
            if key:
                existing[str(key)] = obj
    for row in read_rows(args.inputs):
        decision = decide(row, model, stats)
        existing[decision["key"]] = decision
    ordered = sorted(existing.values(), key=lambda d: (d.get("ts") or "", d.get("key") or ""))
    write_jsonl(out, ordered)
    bets = sum(1 for d in ordered if d.get("action") == "bet")
    print(f"scored rows={len(ordered)} bets={bets} skips={len(ordered) - bets} out={out}")
    return 0


def cmd_report(args) -> int:
    _require_inputs(args.inputs)
    decisions = _decisions(args.inputs, args.model, args.team_stats)
    metrics = evaluate(decisions)
    print(format_table(metrics))
    if args.out:
        write_json(args.out, metrics)
        print(f"wrote {args.out}")
    return 0


def cmd_backtest(args) -> int:
    _require_inputs(args.inputs)
    stats = _stats(args.team_stats)
    rows = read_rows(args.inputs)
    train, test, train_ids, test_ids = split_games(rows, args.train_frac)
    model = fit_model(train, stats)
    decisions = [decide(row, model, stats) for row in test]
    metrics = evaluate(decisions)
    report = {
        "train_games": len(train_ids),
        "test_games": len(test_ids),
        "train_rows": len(train),
        "test_rows": len(test),
        "train_frac": args.train_frac,
        "game_disjoint": len(set(train_ids) & set(test_ids)) == 0,
        "train_game_ids": train_ids,
        "test_game_ids": test_ids,
        "model": model,
        "test": metrics,
        "v2_n_ge_30": metrics["v2"]["n_ge_30"],
    }
    print(
        f"backtest train_games={report['train_games']} test_games={report['test_games']} "
        f"game_disjoint={report['game_disjoint']} v2_n>=30={'yes' if report['v2_n_ge_30'] else 'no'}"
    )
    print(format_table(metrics))
    if args.model_out:
        write_json(args.model_out, model)
        print(f"wrote model {args.model_out}")
    if args.out:
        write_json(args.out, report)
        print(f"wrote {args.out}")
    return 0


def cmd_recalib(args) -> int:
    _require_inputs(args.inputs)
    stats = _stats(args.team_stats)
    model = _load_model(args.model, create_note=True)
    updated = recalibrate(read_rows(args.inputs), model, stats, days=args.days)
    write_json(args.model, updated)
    skipped = updated.get("recalib_skipped")
    ident = (updated.get("isotonic") or {}).get("identity")
    print(
        f"recalib rows={updated.get('recalib_rows', 0)} days={args.days} "
        f"identity={ident} skipped={skipped or 'no'} model={args.model}"
    )
    return 0


def _decisions(paths, model_path, stats_path) -> list[dict]:
    objs = [obj for obj, _origin in read_jsonl(paths)]
    if objs and "action" in objs[0] and "p_pacer" in objs[0]:
        return objs
    model = _load_model(model_path, create_note=True)
    stats = _stats(stats_path)
    return [decide(parse_row(obj), model, stats) for obj in objs]


def _require_inputs(paths: list[str]) -> None:
    missing = [p for p in paths if not Path(p).exists()]
    if missing:
        raise FileNotFoundError("missing input " + ", ".join(missing))


def _stats(path: str | None) -> dict:
    if not path:
        return {}
    return load_team_stats(path)


def _load_model(path: str | None, create_note: bool = False) -> dict:
    model = default_model()
    if not path:
        return model
    p = Path(path)
    if not p.exists():
        if create_note:
            print(f"pacer_v2: no model at {p}, using defaults", file=sys.stderr)
        return model
    model.update(load_json(p))
    return model


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pacer_v2",
        description="Log-only live player-prop pacer v2. Reads JSONL, writes decisions and reports. Never places orders.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("score", help="write one decision per candidate; deduped by key")
    s.add_argument("--in", dest="inputs", nargs="+", required=True, help="candidate JSONL files")
    s.add_argument("--out", required=True, help="decisions JSONL")
    s.add_argument("--model", default=None, help="fitted model JSON (defaults if omitted or missing)")
    s.add_argument("--team-stats", default=None, help="optional team defense/pace JSON")

    r = sub.add_parser("report", help="compare old, v1 and v2 on graded rows")
    r.add_argument("--in", dest="inputs", nargs="+", required=True,
                   help="decisions JSONL, or raw candidates (scored with --model)")
    r.add_argument("--out", default=None, help="metrics JSON")
    r.add_argument("--model", default=None)
    r.add_argument("--team-stats", default=None)

    b = sub.add_parser("backtest", help="chronological 60/40 game-disjoint split; fit on train; score test")
    b.add_argument("--in", dest="inputs", nargs="+", required=True)
    b.add_argument("--model-out", default=None, help="write the train-fitted model JSON")
    b.add_argument("--out", default=None, help="write the test report JSON")
    b.add_argument("--team-stats", default=None)
    b.add_argument("--train-frac", type=float, default=0.6, help="fraction of games in train (default 0.6)")

    c = sub.add_parser("recalib", help="isotonic refit of blended p on recent graded rows")
    c.add_argument("--in", dest="inputs", nargs="+", required=True, help="graded candidate JSONL")
    c.add_argument("--model", required=True, help="model JSON to update in place")
    c.add_argument("--days", type=int, default=28, help="window ending at the newest timestamp (default 28)")
    c.add_argument("--team-stats", default=None)
    return p
