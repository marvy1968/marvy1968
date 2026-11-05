#!/usr/bin/env python3
"""
STOCK EDGE LAB V2 — MOMENTUM REFINEMENT
Research only. No order execution.

Purpose:
    Refine the promising V1 24-bar momentum family.

Important:
    * Uses existing stock_history.duckdb
    * Chronological forward validation
    * SPY market-regime filter
    * Relative-strength confirmation
    * Trend quality
    * Volume confirmation
    * Volatility filtering
    * Extension filtering
    * Portfolio concurrency/risk controls
    * Trading costs
    * Monte Carlo
    * No live/paper order execution
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "stock_history.duckdb"
REPORT_DIR = ROOT / "reports" / "v2"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

INITIAL_EQUITY = 100_000.0

# Conservative round-trip friction.
FEE_BPS_SIDE = 0.0
SLIPPAGE_BPS_SIDE = 2.5
ROUND_TRIP_COST_BPS = 2 * (
    FEE_BPS_SIDE + SLIPPAGE_BPS_SIDE
)

# Research gates
MIN_TRADES = 100
MIN_PF = 1.30
MIN_EXPECTANCY_BPS = 3.0
MIN_PROFITABLE_BLOCK_PCT = 55.0
MAX_DD_PCT = 25.0
MIN_MC_PROFITABILITY = 65.0

# Portfolio controls
RISK_PER_TRADE = 0.005
MAX_POSITION_PCT = 0.10
MAX_CONCURRENT = 5

HOLDING_BARS = 24

PARAM_GRID = [
    # rs_min, volume_min, max_atr_pct,
    # max_extension, spy_regime
    (0.00, 1.00, 0.060, 0.100, "above_200"),
    (0.02, 1.00, 0.060, 0.100, "above_200"),
    (0.04, 1.00, 0.060, 0.100, "above_200"),
    (0.02, 1.20, 0.050, 0.080, "above_200"),
    (0.04, 1.20, 0.050, 0.080, "above_200"),
    (0.06, 1.20, 0.050, 0.080, "above_200"),
    (0.02, 1.40, 0.045, 0.070, "above_200"),
    (0.04, 1.40, 0.045, 0.070, "above_200"),
    (0.06, 1.40, 0.045, 0.070, "above_200"),
    (0.04, 1.20, 0.040, 0.060, "above_50_200"),
    (0.06, 1.20, 0.040, 0.060, "above_50_200"),
    (0.08, 1.20, 0.040, 0.060, "above_50_200"),
]


def safe_pf(pnl):
    pnl = np.asarray(pnl, dtype=float)
    gains = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()

    if losses <= 0:
        return 999.0 if gains > 0 else 0.0

    return float(gains / losses)


def max_drawdown_pct(equity):
    e = np.asarray(equity, dtype=float)

    if len(e) == 0:
        return 0.0

    peaks = np.maximum.accumulate(e)
    dd = (peaks - e) / np.maximum(peaks, 1e-12)

    return float(dd.max() * 100.0)


def load_data():
    if not DB.exists():
        raise SystemExit(
            f"Database not found: {DB}"
        )

    con = duckdb.connect(str(DB), read_only=True)

    tables = [
        x[0]
        for x in con.execute(
            "SHOW TABLES"
        ).fetchall()
    ]

    if not tables:
        raise SystemExit("No tables in DuckDB.")

    table = "bars" if "bars" in tables else tables[0]

    df = con.execute(
        f"SELECT * FROM {table}"
    ).df()

    con.close()

    # Normalize column names.
    df.columns = [
        str(c).lower().strip()
        for c in df.columns
    ]

    aliases = {
        "timestamp": "timestamp",
        "time": "timestamp",
        "datetime": "timestamp",
        "date": "timestamp",
        "ticker": "symbol",
    }

    for old, new in aliases.items():
        if old in df.columns and new not in df.columns:
            df = df.rename(columns={old: new})

    required = {
        "timestamp",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }

    missing = required - set(df.columns)

    if missing:
        raise SystemExit(
            f"Missing database columns: {missing}"
        )

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        utc=True,
        errors="coerce",
    )

    df = df.dropna(
        subset=["timestamp", "symbol", "close"]
    )

    df["symbol"] = (
        df["symbol"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    df = df.sort_values(
        ["symbol", "timestamp"]
    ).reset_index(drop=True)

    return df


def prepare_symbol(g):
    g = g.copy().sort_values("timestamp")

    close = g["close"].astype(float)
    high = g["high"].astype(float)
    low = g["low"].astype(float)
    volume = g["volume"].astype(float)

    g["ret_1"] = close.pct_change()
    g["mom_24"] = close.pct_change(24)
    g["mom_48"] = close.pct_change(48)

    g["ma20"] = close.rolling(20).mean()
    g["ma50"] = close.rolling(50).mean()
    g["ma200"] = close.rolling(200).mean()

    g["vol_ma20"] = volume.rolling(20).mean()

    g["vol_ratio"] = (
        volume /
        g["vol_ma20"].replace(0, np.nan)
    )

    prev = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev).abs(),
            (low - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)

    g["atr14"] = tr.rolling(14).mean()

    g["atr_pct"] = (
        g["atr14"] /
        close.replace(0, np.nan)
    )

    g["extension20"] = (
        close /
        g["ma20"].replace(0, np.nan)
        - 1.0
    )

    # Positive trend-quality value means the
    # short trend is stronger than the medium trend.
    g["trend_quality"] = (
        g["ma20"] /
        g["ma50"].replace(0, np.nan)
        - 1.0
    )

    g["future_close"] = close.shift(-HOLDING_BARS)

    g["raw_forward_return"] = (
        g["future_close"] /
        close.replace(0, np.nan)
        - 1.0
    )

    return g


def prepare_data(df):
    parts = []

    for _, g in df.groupby(
        "symbol",
        sort=False,
    ):
        parts.append(prepare_symbol(g))

    data = pd.concat(
        parts,
        ignore_index=True,
    )

    # SPY reference series.
    spy = data[
        data["symbol"] == "SPY"
    ][
        [
            "timestamp",
            "close",
            "ma50",
            "ma200",
            "mom_24",
        ]
    ].copy()

    if spy.empty:
        raise SystemExit(
            "SPY missing from database. "
            "V2 requires SPY regime data."
        )

    spy = spy.rename(
        columns={
            "close": "spy_close",
            "ma50": "spy_ma50",
            "ma200": "spy_ma200",
            "mom_24": "spy_mom24",
        }
    )

    data = data.merge(
        spy,
        on="timestamp",
        how="left",
    )

    data["relative_strength"] = (
        data["mom_24"] -
        data["spy_mom24"]
    )

    return data


def regime_mask(df, regime):
    if regime == "above_200":
        return (
            df["spy_close"] >
            df["spy_ma200"]
        )

    if regime == "above_50_200":
        return (
            (df["spy_close"] > df["spy_ma200"])
            &
            (df["spy_ma50"] > df["spy_ma200"])
        )

    return pd.Series(
        True,
        index=df.index,
    )


def generate_candidates(df, params):
    (
        rs_min,
        volume_min,
        max_atr_pct,
        max_extension,
        spy_regime,
    ) = params

    x = df.copy()

    # Long-only momentum refinement.
    mask = (
        (x["symbol"] != "SPY")
        &
        (x["close"] > x["ma20"])
        &
        (x["ma20"] > x["ma50"])
        &
        (x["ma50"] > x["ma200"])
        &
        (x["mom_24"] > 0)
        &
        (x["mom_48"] > 0)
        &
        (x["trend_quality"] > 0)
        &
        (x["relative_strength"] >= rs_min)
        &
        (x["vol_ratio"] >= volume_min)
        &
        (x["atr_pct"] > 0)
        &
        (x["atr_pct"] <= max_atr_pct)
        &
        (x["extension20"] >= 0)
        &
        (x["extension20"] <= max_extension)
        &
        x["raw_forward_return"].notna()
    )

    mask &= regime_mask(x, spy_regime)

    x = x.loc[mask].copy()

    if x.empty:
        return x

    # Rank competing signals at each timestamp.
    # Higher relative strength and momentum are better;
    # lower extension is preferred.
    x["quality_score"] = (
        x["relative_strength"] * 4.0
        + x["mom_24"] * 2.0
        + x["trend_quality"] * 2.0
        + np.log1p(
            x["vol_ratio"].clip(lower=0)
        ) * 0.25
        - x["extension20"] * 1.5
    )

    x = x.sort_values(
        ["timestamp", "quality_score"],
        ascending=[True, False],
    )

    return x


def portfolio_sim(candidates):
    if candidates.empty:
        return pd.DataFrame()

    open_positions = []
    trades = []

    equity = INITIAL_EQUITY

    for ts, batch in candidates.groupby(
        "timestamp",
        sort=True,
    ):
        # Release expired positions.
        open_positions = [
            p
            for p in open_positions
            if p["exit_time"] > ts
        ]

        available = (
            MAX_CONCURRENT -
            len(open_positions)
        )

        if available <= 0:
            continue

        batch = batch.head(available)

        for _, row in batch.iterrows():
            # Avoid duplicate simultaneous symbol.
            if any(
                p["symbol"] == row["symbol"]
                for p in open_positions
            ):
                continue

            atr_pct = float(row["atr_pct"])

            if not np.isfinite(atr_pct) or atr_pct <= 0:
                continue

            # Approximate ATR risk sizing.
            stop_distance = max(
                atr_pct * 1.5,
                0.01,
            )

            risk_dollars = (
                equity * RISK_PER_TRADE
            )

            position_dollars = min(
                risk_dollars / stop_distance,
                equity * MAX_POSITION_PCT,
            )

            if position_dollars <= 0:
                continue

            raw_ret = float(
                row["raw_forward_return"]
            )

            net_ret = (
                raw_ret
                - ROUND_TRIP_COST_BPS / 10000.0
            )

            pnl = position_dollars * net_ret

            # Future exit timestamp approximation.
            exit_time = (
                ts
                + pd.Timedelta(
                    hours=HOLDING_BARS
                )
            )

            equity += pnl

            trades.append(
                {
                    "entry_time": ts,
                    "exit_time": exit_time,
                    "symbol": row["symbol"],
                    "position_dollars":
                        position_dollars,
                    "raw_return":
                        raw_ret,
                    "net_return":
                        net_ret,
                    "pnl": pnl,
                    "equity": equity,
                    "quality_score":
                        row["quality_score"],
                    "relative_strength":
                        row["relative_strength"],
                    "vol_ratio":
                        row["vol_ratio"],
                    "atr_pct":
                        row["atr_pct"],
                    "extension20":
                        row["extension20"],
                }
            )

            open_positions.append(
                {
                    "symbol": row["symbol"],
                    "exit_time": exit_time,
                }
            )

    return pd.DataFrame(trades)


def summarize(trades):
    if trades.empty:
        return {
            "trades": 0,
            "roi_pct": 0.0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy_bps": 0.0,
            "max_drawdown_pct": 0.0,
        }

    pnl = trades["pnl"].to_numpy(float)

    roi = (
        trades["equity"].iloc[-1]
        / INITIAL_EQUITY
        - 1.0
    ) * 100.0

    win_rate = (
        (pnl > 0).mean() * 100.0
    )

    position = (
        trades["position_dollars"]
        .replace(0, np.nan)
    )

    trade_returns = (
        trades["pnl"] / position
    )

    expectancy_bps = (
        trade_returns.mean() * 10000.0
    )

    equity_curve = np.r_[
        INITIAL_EQUITY,
        trades["equity"].to_numpy(float),
    ]

    return {
        "trades": int(len(trades)),
        "roi_pct": float(roi),
        "win_rate_pct": float(win_rate),
        "profit_factor": safe_pf(pnl),
        "expectancy_bps":
            float(expectancy_bps),
        "max_drawdown_pct":
            max_drawdown_pct(equity_curve),
    }


def block_test(candidates):
    if candidates.empty:
        return [], 0.0

    start = candidates["timestamp"].min()
    end = candidates["timestamp"].max()

    boundaries = pd.date_range(
        start=start.floor("D"),
        end=end.ceil("D")
        + pd.Timedelta(days=90),
        freq="90D",
        tz="UTC",
    )

    results = []

    for i in range(
        max(0, len(boundaries) - 1)
    ):
        a = boundaries[i]
        b = boundaries[i + 1]

        block = candidates[
            (candidates["timestamp"] >= a)
            &
            (candidates["timestamp"] < b)
        ]

        trades = portfolio_sim(block)
        stats = summarize(trades)

        results.append(
            {
                "block": i + 1,
                "start": str(a),
                "end": str(b),
                **stats,
                "profitable":
                    stats["roi_pct"] > 0,
            }
        )

    if not results:
        return [], 0.0

    profitable_pct = (
        100.0
        * sum(
            x["profitable"]
            for x in results
        )
        / len(results)
    )

    return results, profitable_pct


def monte_carlo(trades, iterations, seed=42):
    if len(trades) < MIN_TRADES:
        return {
            "iterations": iterations,
            "trades": int(len(trades)),
            "probability_profitable_pct":
                None,
            "dd_95th_pct": None,
            "warning":
                "insufficient trades",
        }

    rng = np.random.default_rng(seed)

    rets = (
        trades["pnl"]
        /
        trades["position_dollars"]
        .replace(0, np.nan)
    ).dropna().to_numpy(float)

    if len(rets) == 0:
        return {
            "iterations": iterations,
            "trades": 0,
            "probability_profitable_pct":
                None,
            "dd_95th_pct": None,
        }

    final = []
    dds = []

    # MC uses constant 10% position allocation
    # to measure trade-sequence robustness.
    fraction = MAX_POSITION_PCT

    for _ in range(iterations):
        sample = rng.choice(
            rets,
            size=len(rets),
            replace=True,
        )

        equity = 1.0
        curve = [equity]

        for r in sample:
            equity *= (
                1.0 + fraction * r
            )
            curve.append(equity)

        final.append(
            (equity - 1.0) * 100.0
        )

        dds.append(
            max_drawdown_pct(curve)
        )

    final = np.asarray(final)
    dds = np.asarray(dds)

    return {
        "iterations": iterations,
        "trades": int(len(rets)),
        "probability_profitable_pct":
            float(
                (final > 0).mean() * 100.0
            ),
        "median_roi_pct":
            float(np.median(final)),
        "roi_5th_pct":
            float(np.percentile(final, 5)),
        "roi_95th_pct":
            float(np.percentile(final, 95)),
        "dd_95th_pct":
            float(np.percentile(dds, 95)),
    }


def param_dict(params):
    return {
        "relative_strength_min":
            params[0],
        "volume_ratio_min":
            params[1],
        "max_atr_pct":
            params[2],
        "max_extension20":
            params[3],
        "spy_regime":
            params[4],
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--mc",
        type=int,
        default=10000,
    )

    args = parser.parse_args()

    print(
        "===== STOCK MOMENTUM "
        "REFINEMENT LAB V2 ====="
    )
    print("Research only.")
    print("Execution enabled: NO")
    print(f"Database: {DB}")

    raw = load_data()

    print(
        f"Rows loaded: {len(raw):,}"
    )
    print(
        f"Symbols: "
        f"{raw['symbol'].nunique()}"
    )

    data = prepare_data(raw)

    # Preserve a final chronological holdout.
    dates = (
        data["timestamp"]
        .dropna()
        .sort_values()
    )

    cutoff = dates.quantile(0.80)

    development = data[
        data["timestamp"] < cutoff
    ].copy()

    holdout = data[
        data["timestamp"] >= cutoff
    ].copy()

    print(
        f"Development ends: {cutoff}"
    )
    print(
        f"Holdout rows: {len(holdout):,}"
    )

    development_results = []

    # PARAMETER SELECTION USES DEVELOPMENT ONLY.
    for n, params in enumerate(
        PARAM_GRID,
        1,
    ):
        candidates = generate_candidates(
            development,
            params,
        )

        trades = portfolio_sim(candidates)
        stats = summarize(trades)

        blocks, profitable_pct = block_test(
            candidates
        )

        score = (
            stats["expectancy_bps"]
            + 20.0 * math.log(
                max(
                    stats["profit_factor"],
                    0.01,
                )
            )
            + profitable_pct * 0.25
            - stats["max_drawdown_pct"]
            * 0.75
        )

        development_results.append(
            {
                "candidate": n,
                **param_dict(params),
                **stats,
                "profitable_block_pct":
                    profitable_pct,
                "score": score,
            }
        )

        print(
            f"Candidate {n:02d}: "
            f"trades={stats['trades']} "
            f"PF={stats['profit_factor']:.2f} "
            f"EXP={stats['expectancy_bps']:.2f}bps "
            f"DD={stats['max_drawdown_pct']:.2f}% "
            f"blocks={profitable_pct:.1f}%"
        )

    ranking = pd.DataFrame(
        development_results
    ).sort_values(
        "score",
        ascending=False,
    )

    if ranking.empty:
        raise SystemExit(
            "No development candidates."
        )

    # Pick champion strictly from development.
    champion_row = ranking.iloc[0]

    champion_params = (
        float(
            champion_row[
                "relative_strength_min"
            ]
        ),
        float(
            champion_row[
                "volume_ratio_min"
            ]
        ),
        float(
            champion_row[
                "max_atr_pct"
            ]
        ),
        float(
            champion_row[
                "max_extension20"
            ]
        ),
        str(
            champion_row[
                "spy_regime"
            ]
        ),
    )

    print()
    print(
        "Development champion:",
        json.dumps(
            param_dict(champion_params),
            indent=2,
        )
    )

    # NOW touch holdout exactly once.
    holdout_candidates = generate_candidates(
        holdout,
        champion_params,
    )

    holdout_trades = portfolio_sim(
        holdout_candidates
    )

    holdout_stats = summarize(
        holdout_trades
    )

    holdout_blocks, holdout_profitable_pct = (
        block_test(holdout_candidates)
    )

    mc = monte_carlo(
        holdout_trades,
        args.mc,
    )

    mc_prob = mc.get(
        "probability_profitable_pct"
    )

    passed = (
        holdout_stats["trades"]
        >= MIN_TRADES
        and holdout_stats["profit_factor"]
        >= MIN_PF
        and holdout_stats["expectancy_bps"]
        >= MIN_EXPECTANCY_BPS
        and holdout_profitable_pct
        >= MIN_PROFITABLE_BLOCK_PCT
        and holdout_stats[
            "max_drawdown_pct"
        ] <= MAX_DD_PCT
        and mc_prob is not None
        and mc_prob
        >= MIN_MC_PROFITABILITY
    )

    verdict = (
        "STOCK_MOMENTUM_EDGE_SURVIVED"
        if passed
        else
        "STOCK_MOMENTUM_NOT_VALIDATED"
    )

    report = {
        "methodology": {
            "version": "v2",
            "strategy":
                "momentum refinement",
            "holding_bars":
                HOLDING_BARS,
            "initial_equity":
                INITIAL_EQUITY,
            "risk_per_trade":
                RISK_PER_TRADE,
            "max_position_pct":
                MAX_POSITION_PCT,
            "max_concurrent":
                MAX_CONCURRENT,
            "round_trip_cost_bps":
                ROUND_TRIP_COST_BPS,
            "parameter_selection":
                "development only",
            "holdout_used_for_selection":
                False,
            "execution_enabled":
                False,
        },
        "development_cutoff":
            str(cutoff),
        "development_ranking":
            ranking.to_dict(
                orient="records"
            ),
        "selected_parameters":
            param_dict(champion_params),
        "holdout": {
            **holdout_stats,
            "profitable_block_pct":
                holdout_profitable_pct,
        },
        "holdout_blocks":
            holdout_blocks,
        "monte_carlo":
            mc,
        "research_gate": {
            "minimum_trades":
                MIN_TRADES,
            "minimum_profit_factor":
                MIN_PF,
            "minimum_expectancy_bps":
                MIN_EXPECTANCY_BPS,
            "minimum_profitable_block_pct":
                MIN_PROFITABLE_BLOCK_PCT,
            "maximum_drawdown_pct":
                MAX_DD_PCT,
            "minimum_mc_probability_pct":
                MIN_MC_PROFITABILITY,
            "passed":
                passed,
        },
        "verdict":
            verdict,
    }

    json_path = (
        REPORT_DIR /
        "stock_momentum_refinement_v2_latest.json"
    )

    ranking_path = (
        REPORT_DIR /
        "stock_momentum_refinement_v2_ranking.csv"
    )

    trades_path = (
        REPORT_DIR /
        "stock_momentum_refinement_v2_holdout_trades.csv"
    )

    json_path.write_text(
        json.dumps(
            report,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    ranking.to_csv(
        ranking_path,
        index=False,
    )

    holdout_trades.to_csv(
        trades_path,
        index=False,
    )

    print()
    print(
        "========== V2 FINAL RESULT =========="
    )

    print(
        json.dumps(
            {
                "holdout_trades":
                    holdout_stats[
                        "trades"
                    ],
                "holdout_roi_pct":
                    holdout_stats[
                        "roi_pct"
                    ],
                "holdout_pf":
                    holdout_stats[
                        "profit_factor"
                    ],
                "holdout_expectancy_bps":
                    holdout_stats[
                        "expectancy_bps"
                    ],
                "holdout_max_dd_pct":
                    holdout_stats[
                        "max_drawdown_pct"
                    ],
                "profitable_block_pct":
                    holdout_profitable_pct,
                "mc_probability_profitable_pct":
                    mc_prob,
                "mc_dd_95th_pct":
                    mc.get(
                        "dd_95th_pct"
                    ),
                "passed":
                    passed,
            },
            indent=2,
        )
    )

    print()
    print("Reports:")
    print(json_path)
    print(ranking_path)
    print(trades_path)

    print()
    print(
        "FINAL VERDICT:",
        verdict,
    )


if __name__ == "__main__":
    main()
