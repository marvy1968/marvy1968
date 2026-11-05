#!/usr/bin/env python3
"""
Crypto Track A — Frozen OOS Cohort × Friction Robustness Matrix

NO parameter optimization.

Tests frozen Alpha/Beta structures across:
  3 universe cohorts
× 3 fee assumptions
× 3 slippage assumptions
= 27 OOS environments per frozen strategy.

Uses the existing chronologically certified stitched simulator.
"""

import numpy as np
import pandas as pd

import run_stitched_oos_engine as eng


INITIAL_CAPITAL = 100_000.0

FROZEN = [
    ("ALPHA", 250_000, 15, 2.0),
    ("BETA",  250_000, 30, 2.0),
]

FEES = [
    0.0006,
    0.0010,
    0.0020,
]

SLIPPAGES = [
    0.0005,
    0.0010,
    0.0020,
]

MATURE_24 = {
    "BTC/USD", "ETH/USD", "XRP/USD", "SOL/USD", "ADA/USD",
    "ZEC/USD", "XMR/USD", "TAO/USD", "DOGE/USD", "SUI/USD",
    "LINK/USD", "XLM/USD", "AVAX/USD", "LTC/USD", "UNI/USD",
    "CRV/USD", "ONDO/USD", "NEAR/USD", "AAVE/USD", "TRX/USD",
    "PEPE/USD", "ICP/USD", "ENA/USD", "FET/USD",
}

YOUNG_TRIO = {
    "HYPE/USD",
    "PUMP/USD",
    "VVV/USD",
}


def score_result(trades, equity_curve):

    eq = pd.Series(
        [x[1] for x in equity_curve],
        index=[x[0] for x in equity_curve],
        dtype=float,
    )

    eq = eq[~eq.index.duplicated(keep="last")]

    if len(eq) < 2:
        raise RuntimeError("Insufficient equity observations.")

    terminal = float(eq.iloc[-1])

    net_return_pct = (
        terminal / INITIAL_CAPITAL - 1.0
    ) * 100.0

    elapsed_days = max(
        (
            eq.index[-1] -
            eq.index[0]
        ).total_seconds() / 86400.0,
        1.0,
    )

    cagr_pct = (
        (
            terminal / INITIAL_CAPITAL
        ) ** (
            365.25 / elapsed_days
        ) - 1.0
    ) * 100.0

    peak = eq.cummax()

    dd = (
        eq / peak - 1.0
    )

    max_dd_pct = (
        abs(float(dd.min())) *
        100.0
    )

    mar = (
        cagr_pct / max_dd_pct
        if max_dd_pct > 0
        else 0.0
    )

    total_trades = len(trades)

    if total_trades == 0:
        return {
            "return_pct": net_return_pct,
            "cagr_pct": cagr_pct,
            "max_dd_pct": max_dd_pct,
            "mar": mar,
            "pf": 0.0,
            "expectancy_r": 0.0,
            "win_rate_pct": 0.0,
            "trades": 0,
        }

    pnl = trades["pnl"].astype(float)
    r = trades["r"].astype(float)

    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]

    gross_profit = float(
        wins.sum()
    )

    gross_loss = abs(
        float(losses.sum())
    )

    pf = (
        gross_profit / gross_loss
        if gross_loss > 0
        else np.inf
    )

    return {
        "return_pct": net_return_pct,
        "cagr_pct": cagr_pct,
        "max_dd_pct": max_dd_pct,
        "mar": mar,
        "pf": pf,
        "expectancy_r": float(r.mean()),
        "win_rate_pct": float(
            (pnl > 0).mean() *
            100.0
        ),
        "trades": total_trades,
    }


def main():

    print("=" * 125)
    print(
        " CRYPTO TRACK A — FROZEN OOS "
        "COHORT × FRICTION ROBUSTNESS"
    )
    print("=" * 125)

    manifest, dfs = eng.load_data()

    btc_df = dfs["BTC/USD"]

    all_dates = btc_df.index[60:]

    oos_dates = all_dates[
        int(
            len(all_dates) *
            0.50
        ):
    ]

    cohorts = {
        "FULL30":
            manifest.copy(),

        "MATURE24":
            manifest[
                manifest["symbol"].isin(
                    MATURE_24
                )
            ].copy(),

        "NO_HYPE_PUMP_VVV":
            manifest[
                ~manifest["symbol"].isin(
                    YOUNG_TRIO
                )
            ].copy(),
    }

    raw_rows = []

    for strategy, liq, lb, atr in FROZEN:

        print()
        print("=" * 125)
        print(
            f" {strategy}: "
            f"liq=${liq/1e6:.2f}M | "
            f"Donchian={lb}d | "
            f"ATR={atr:.1f}x"
        )
        print("=" * 125)

        for cohort_name, cohort_manifest in cohorts.items():

            for fee in FEES:

                for slip in SLIPPAGES:

                    eng.FEE_RATE = fee
                    eng.SLIPPAGE_RATE = slip

                    trades, equity = (
                        eng.simulate_continuous_oos(
                            cohort_manifest,
                            dfs,
                            liq,
                            lb,
                            atr,
                            oos_dates,
                        )
                    )

                    m = score_result(
                        trades,
                        equity,
                    )

                    raw_rows.append({
                        "strategy": strategy,
                        "cohort": cohort_name,
                        "assets": len(cohort_manifest),
                        "fee_pct": fee * 100.0,
                        "slippage_bp": slip * 10000.0,
                        **m,
                    })

    raw = pd.DataFrame(raw_rows)

    raw_path = (
        "/home/moonrider1968/bots/"
        "crypto_data/"
        "oos_cohort_friction_raw.csv"
    )

    raw.to_csv(
        raw_path,
        index=False,
    )

    summary = (
        raw.groupby(
            [
                "strategy",
                "cohort",
                "assets",
            ]
        )
        .agg(
            scenarios=(
                "return_pct",
                "size"
            ),

            median_return_pct=(
                "return_pct",
                "median"
            ),

            worst_return_pct=(
                "return_pct",
                "min"
            ),

            median_cagr_pct=(
                "cagr_pct",
                "median"
            ),

            worst_cagr_pct=(
                "cagr_pct",
                "min"
            ),

            median_dd_pct=(
                "max_dd_pct",
                "median"
            ),

            worst_dd_pct=(
                "max_dd_pct",
                "max"
            ),

            median_mar=(
                "mar",
                "median"
            ),

            worst_mar=(
                "mar",
                "min"
            ),

            median_pf=(
                "pf",
                "median"
            ),

            worst_pf=(
                "pf",
                "min"
            ),

            median_expectancy_r=(
                "expectancy_r",
                "median"
            ),

            worst_expectancy_r=(
                "expectancy_r",
                "min"
            ),

            min_trades=(
                "trades",
                "min"
            ),
        )
        .reset_index()
    )

    summary["gate_positive_worst_return"] = (
        summary["worst_return_pct"] > 0
    )

    summary["gate_positive_worst_expectancy"] = (
        summary["worst_expectancy_r"] > 0
    )

    summary["gate_worst_pf"] = (
        summary["worst_pf"] >= 1.10
    )

    summary["gate_worst_dd"] = (
        summary["worst_dd_pct"] <= 15.0
    )

    summary["friction_pass"] = (
        summary[
            [
                "gate_positive_worst_return",
                "gate_positive_worst_expectancy",
                "gate_worst_pf",
                "gate_worst_dd",
            ]
        ]
        .all(
            axis=1
        )
    )

    summary = summary.sort_values(
        by=[
            "friction_pass",
            "worst_return_pct",
            "worst_mar",
            "worst_pf",
        ],
        ascending=[
            False,
            False,
            False,
            False,
        ],
    )

    summary_path = (
        "/home/moonrider1968/bots/"
        "crypto_data/"
        "oos_cohort_friction_summary.csv"
    )

    summary.to_csv(
        summary_path,
        index=False,
    )

    print()
    print("=" * 125)
    print(
        " ROBUSTNESS SUMMARY — "
        "ALL 9 FRICTION ENVIRONMENTS"
    )
    print("=" * 125)

    display = [
        "strategy",
        "cohort",
        "assets",
        "worst_return_pct",
        "median_return_pct",
        "worst_dd_pct",
        "worst_mar",
        "median_mar",
        "worst_pf",
        "worst_expectancy_r",
        "min_trades",
        "friction_pass",
    ]

    print(
        summary[
            display
        ].to_string(
            index=False
        )
    )

    print()
    print("=" * 125)
    print(" OUTPUT")
    print("=" * 125)
    print(raw_path)
    print(summary_path)
    print("=" * 125)


if __name__ == "__main__":
    main()
