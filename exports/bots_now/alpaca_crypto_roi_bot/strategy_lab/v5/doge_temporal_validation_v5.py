#!/usr/bin/env python3

import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports" / "v5"
REPORTS.mkdir(parents=True, exist_ok=True)

# Reuse the already-tested v4 engine.
V4_PATH = ROOT / "v4" / "doge_breakout_holdout_v4.py"

spec = importlib.util.spec_from_file_location("v4engine", V4_PATH)
v4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v4)


def score(m):
    if m["trades"] < 12:
        return -999.0

    pf = m["profit_factor"]

    if not np.isfinite(pf):
        pf = 3.0

    return (
        m["roi_pct"]
        + 2.0 * min(pf, 3.0)
        - 0.75 * m["max_drawdown_pct"]
    )


def choose_once(x, train_start, train_end, fee, slip):
    rows = []

    for p in v4.fixed_family():
        m = v4.backtest(
            x,
            p,
            fee,
            slip,
            start=train_start,
            end=train_end,
        )

        rows.append({
            "params": p,
            "trades": m["trades"],
            "roi_pct": m["roi_pct"],
            "profit_factor": m["profit_factor"],
            "expectancy": m["expectancy"],
            "max_drawdown_pct": m["max_drawdown_pct"],
            "score": score(m),
        })

    rows.sort(
        key=lambda r: r["score"],
        reverse=True,
    )

    return rows[0]["params"], rows


def aggregate(trades):
    if not trades:
        return {
            "trades": 0,
            "roi_pct": 0.0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "max_drawdown_pct": 0.0,
        }

    pnl = np.array(
        [t["pnl"] for t in trades],
        dtype=float,
    )

    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    if len(losses) and abs(losses.sum()) > 0:
        pf = float(
            wins.sum() / abs(losses.sum())
        )
    elif len(wins):
        pf = float("inf")
    else:
        pf = 0.0

    curve = np.r_[
        v4.START_EQUITY,
        v4.START_EQUITY + np.cumsum(pnl),
    ]

    peak = np.maximum.accumulate(curve)

    dd = float(
        ((peak - curve) / peak * 100).max()
    )

    return {
        "trades": len(trades),
        "roi_pct": float(
            (curve[-1] / v4.START_EQUITY - 1) * 100
        ),
        "win_rate_pct": float(
            (pnl > 0).mean() * 100
        ),
        "profit_factor": pf,
        "expectancy": float(pnl.mean()),
        "max_drawdown_pct": dd,
    }


def monte_carlo(trades, iterations):
    pnl = np.array(
        [t["pnl"] for t in trades],
        dtype=float,
    )

    if len(pnl) < 10:
        return {
            "iterations": iterations,
            "trades": len(pnl),
            "warning": "insufficient trades",
        }

    rng = np.random.default_rng(42)

    rois = []
    dds = []

    for _ in range(iterations):
        sample = rng.choice(
            pnl,
            size=len(pnl),
            replace=True,
        )

        curve = np.r_[
            v4.START_EQUITY,
            v4.START_EQUITY + np.cumsum(sample),
        ]

        peak = np.maximum.accumulate(curve)

        rois.append(
            float(
                (curve[-1] / v4.START_EQUITY - 1)
                * 100
            )
        )

        dds.append(
            float(
                ((peak - curve) / peak * 100).max()
            )
        )

    arr = np.array(rois)

    return {
        "iterations": iterations,
        "trades": len(pnl),
        "probability_profitable_pct":
            float((arr > 0).mean() * 100),
        "median_roi_pct":
            float(np.median(arr)),
        "roi_5th_pct":
            float(np.percentile(arr, 5)),
        "roi_95th_pct":
            float(np.percentile(arr, 95)),
        "dd_95th_pct":
            float(np.percentile(dds, 95)),
    }


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--fee-bps",
        type=float,
        default=15.0,
    )

    ap.add_argument(
        "--slippage-bps",
        type=float,
        default=5.0,
    )

    ap.add_argument(
        "--train-days",
        type=int,
        default=450,
    )

    ap.add_argument(
        "--block-days",
        type=int,
        default=90,
    )

    ap.add_argument(
        "--mc",
        type=int,
        default=10000,
    )

    args = ap.parse_args()

    doge = v4.resample4h(
        v4.load("DOGE/USD")
    )

    btc = v4.resample4h(
        v4.load("BTC/USD")
    )

    x = v4.features(doge, btc)

    full_start = x.index.min()
    full_end = x.index.max()

    train_end = (
        full_start
        + pd.Timedelta(days=args.train_days)
    )

    selected, dev_grid = choose_once(
        x,
        full_start,
        train_end,
        args.fee_bps,
        args.slippage_bps,
    )

    print(
        "Frozen parameters:",
        json.dumps(selected),
        flush=True,
    )

    blocks = []
    all_trades = []

    block_start = train_end
    block_number = 1

    while block_start < full_end:

        block_end = min(
            block_start
            + pd.Timedelta(
                days=args.block_days
            ),
            full_end
            + pd.Timedelta(seconds=1),
        )

        m = v4.backtest(
            x,
            selected,
            args.fee_bps,
            args.slippage_bps,
            start=block_start,
            end=block_end,
        )

        all_trades.extend(
            m["trade_list"]
        )

        blocks.append({
            "block": block_number,
            "start": str(block_start),
            "end": str(block_end),
            "trades": m["trades"],
            "roi_pct": m["roi_pct"],
            "profit_factor":
                m["profit_factor"],
            "expectancy":
                m["expectancy"],
            "max_drawdown_pct":
                m["max_drawdown_pct"],
            "profitable":
                m["roi_pct"] > 0,
        })

        print(
            f"Block {block_number}: "
            f"trades={m['trades']} "
            f"ROI={m['roi_pct']:.2f}% "
            f"PF={m['profit_factor']} "
            f"DD={m['max_drawdown_pct']:.2f}%",
            flush=True,
        )

        block_start = block_end
        block_number += 1

    agg = aggregate(all_trades)

    profitable_blocks = sum(
        b["profitable"]
        for b in blocks
    )

    profitable_block_pct = (
        profitable_blocks
        / max(len(blocks), 1)
        * 100
    )

    mc = monte_carlo(
        all_trades,
        args.mc,
    )

    # Test the whole narrow family forward
    # using the same frozen cutoff.
    family_rows = []

    for p in v4.fixed_family():
        m = v4.backtest(
            x,
            p,
            args.fee_bps,
            args.slippage_bps,
            start=train_end,
            end=full_end
                + pd.Timedelta(seconds=1),
        )

        family_rows.append({
            **p,
            "trades": m["trades"],
            "roi_pct": m["roi_pct"],
            "profit_factor":
                m["profit_factor"],
            "expectancy":
                m["expectancy"],
            "max_drawdown_pct":
                m["max_drawdown_pct"],
            "profitable":
                m["roi_pct"] > 0,
        })

    family_pct = (
        sum(
            r["profitable"]
            for r in family_rows
        )
        / max(len(family_rows), 1)
        * 100
    )

    passed = (
        agg["trades"] >= 25
        and agg["roi_pct"] > 0
        and np.isfinite(
            agg["profit_factor"]
        )
        and agg["profit_factor"] >= 1.25
        and profitable_block_pct >= 50
        and family_pct >= 50
        and mc.get(
            "probability_profitable_pct",
            0,
        ) >= 65
    )

    result = {
        "methodology": {
            "timeframe": "4h",
            "train_start":
                str(full_start),
            "train_end":
                str(train_end),
            "forward_end":
                str(full_end),
            "train_days":
                args.train_days,
            "block_days":
                args.block_days,
            "reoptimization_after_training":
                False,
        },

        "selected_parameters":
            selected,

        "forward_blocks":
            blocks,

        "aggregate_oos":
            agg,

        "profitable_blocks":
            profitable_blocks,

        "profitable_block_pct":
            profitable_block_pct,

        "family_survival_pct":
            family_pct,

        "monte_carlo":
            mc,

        "research_gate": {
            "minimum_oos_trades": 25,
            "minimum_pf": 1.25,
            "minimum_profitable_blocks_pct": 50,
            "minimum_family_survival_pct": 50,
            "minimum_mc_probability_pct": 65,
            "passed": passed,
        },
    }

    (
        REPORTS
        / "doge_temporal_validation_v5_latest.json"
    ).write_text(
        json.dumps(
            result,
            indent=2,
        ),
        encoding="utf-8",
    )

    pd.DataFrame(
        blocks
    ).to_csv(
        REPORTS
        / "doge_temporal_validation_v5_blocks.csv",
        index=False,
    )

    pd.DataFrame(
        all_trades
    ).to_csv(
        REPORTS
        / "doge_temporal_validation_v5_oos_trades.csv",
        index=False,
    )

    pd.DataFrame(
        family_rows
    ).to_csv(
        REPORTS
        / "doge_temporal_validation_v5_family.csv",
        index=False,
    )

    print(
        "\n===== DOGE TEMPORAL VALIDATION V5 ====="
    )

    print(
        json.dumps({
            "forward_blocks":
                len(blocks),
            "profitable_blocks":
                profitable_blocks,
            "profitable_block_pct":
                profitable_block_pct,
            "oos_trades":
                agg["trades"],
            "oos_roi_pct":
                agg["roi_pct"],
            "oos_pf":
                agg["profit_factor"],
            "oos_expectancy":
                agg["expectancy"],
            "oos_max_dd_pct":
                agg["max_drawdown_pct"],
            "family_survival_pct":
                family_pct,
            "mc_probability_profitable_pct":
                mc.get(
                    "probability_profitable_pct"
                ),
            "mc_dd_95th_pct":
                mc.get(
                    "dd_95th_pct"
                ),
            "passed":
                passed,
        }, indent=2)
    )


if __name__ == "__main__":
    main()
