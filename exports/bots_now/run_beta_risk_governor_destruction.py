#!/usr/bin/env python3
"""
Frozen Beta Risk-Governor Destruction Certification

NO signal optimization.

Frozen:
    Liquidity floor = $250k
    Donchian = 30d
    ATR = 2.0x
    Fee = 0.10%
    Slippage = 10bp

Only portfolio risk changes:
    1.00%
    0.75%
    0.50%
    0.40%

Tests correlated crashes:
    -20%, -30%, -50%

Goal:
    Find whether a predeclared risk governor can keep
    worst drawdown <= 20% without altering the alpha model.
"""

import pandas as pd

import run_stitched_oos_engine as eng
import run_beta_crash_destruction as crash


RISK_LEVELS = [
    0.0100,
    0.0075,
    0.0050,
    0.0040,
]

SHOCK_LEVELS = [
    -0.20,
    -0.30,
    -0.50,
]


def main():

    print("=" * 140)
    print(" 🛡️ FROZEN BETA — RISK GOVERNOR DESTRUCTION CERTIFICATION")
    print("=" * 140)

    manifest, base_dfs = eng.load_data()

    btc = base_dfs["BTC/USD"]
    all_dates = btc.index[60:]
    oos_dates = all_dates[int(len(all_dates) * 0.50):]

    cohorts = {
        "CORE_N27":
            manifest[
                ~manifest["symbol"].isin(
                    crash.YOUNG_TRIO
                )
            ].copy(),

        "MATURE_N24":
            manifest[
                manifest["symbol"].isin(
                    crash.MATURE_24
                )
            ].copy(),
    }

    eligible = oos_dates[30:-30]
    shock_dates = eligible[::14]

    eng.FEE_RATE = 0.0010
    eng.SLIPPAGE_RATE = 0.0010

    rows = []

    for risk in RISK_LEVELS:

        eng.RISK_PER_TRADE = risk

        print()
        print("=" * 140)
        print(
            f" RISK PER TRADE = {risk * 100:.2f}%"
        )
        print("=" * 140)

        for cohort_name, cohort_manifest in cohorts.items():

            # Unshocked reference
            baseline = crash.run_one(
                cohort_manifest,
                base_dfs,
                oos_dates,
            )

            print(
                f"{cohort_name} baseline | "
                f"Ret={baseline['return_pct']:+.2f}% | "
                f"DD={baseline['max_dd_pct']:.2f}% | "
                f"PF={baseline['pf']:.2f} | "
                f"Exp={baseline['expectancy_r']:+.3f}R"
            )

            for shock_pct in SHOCK_LEVELS:

                for shock_date in shock_dates:

                    shocked_dfs = (
                        crash.shock_dataset(
                            base_dfs,
                            shock_date,
                            shock_pct,
                        )
                    )

                    result = crash.run_one(
                        cohort_manifest,
                        shocked_dfs,
                        oos_dates,
                    )

                    rows.append({
                        "risk_pct":
                            risk * 100.0,

                        "cohort":
                            cohort_name,

                        "shock_pct":
                            shock_pct * 100.0,

                        "shock_date":
                            shock_date.strftime(
                                "%Y-%m-%d"
                            ),

                        **result,
                    })

    raw = pd.DataFrame(rows)

    raw_path = (
        "/home/moonrider1968/bots/"
        "crypto_data/"
        "beta_risk_governor_destruction_raw.csv"
    )

    raw.to_csv(
        raw_path,
        index=False,
    )

    summary = (
        raw.groupby(
            [
                "risk_pct",
                "cohort",
                "shock_pct",
            ]
        )
        .agg(
            tests=(
                "return_pct",
                "size",
            ),

            median_return_pct=(
                "return_pct",
                "median",
            ),

            worst_return_pct=(
                "return_pct",
                "min",
            ),

            median_dd_pct=(
                "max_dd_pct",
                "median",
            ),

            worst_dd_pct=(
                "max_dd_pct",
                "max",
            ),

            worst_min_nav=(
                "min_nav",
                "min",
            ),

            median_pf=(
                "pf",
                "median",
            ),

            worst_pf=(
                "pf",
                "min",
            ),

            median_expectancy_r=(
                "expectancy_r",
                "median",
            ),

            worst_expectancy_r=(
                "expectancy_r",
                "min",
            ),

            min_trades=(
                "trades",
                "min",
            ),
        )
        .reset_index()
    )

    profit_rates = (
        raw.assign(
            profitable=
                raw["return_pct"] > 0
        )
        .groupby(
            [
                "risk_pct",
                "cohort",
                "shock_pct",
            ]
        )["profitable"]
        .mean()
        .reset_index(
            name="terminal_profit_rate"
        )
    )

    profit_rates[
        "terminal_profit_rate_pct"
    ] = (
        profit_rates[
            "terminal_profit_rate"
        ] * 100.0
    )

    summary = summary.merge(
        profit_rates[
            [
                "risk_pct",
                "cohort",
                "shock_pct",
                "terminal_profit_rate_pct",
            ]
        ],
        on=[
            "risk_pct",
            "cohort",
            "shock_pct",
        ],
        how="left",
    )

    ruin_rates = (
        raw.assign(
            ruined=
                raw["min_nav"] < 50_000.0
        )
        .groupby(
            [
                "risk_pct",
                "cohort",
                "shock_pct",
            ]
        )["ruined"]
        .mean()
        .reset_index(
            name="ruin_rate"
        )
    )

    ruin_rates[
        "ruin_rate_pct"
    ] = (
        ruin_rates[
            "ruin_rate"
        ] * 100.0
    )

    summary = summary.merge(
        ruin_rates[
            [
                "risk_pct",
                "cohort",
                "shock_pct",
                "ruin_rate_pct",
            ]
        ],
        on=[
            "risk_pct",
            "cohort",
            "shock_pct",
        ],
        how="left",
    )

    summary[
        "dd_ceiling_pass"
    ] = (
        summary[
            "worst_dd_pct"
        ] <= 20.0
    )

    summary_path = (
        "/home/moonrider1968/bots/"
        "crypto_data/"
        "beta_risk_governor_destruction_summary.csv"
    )

    summary.to_csv(
        summary_path,
        index=False,
    )

    print()
    print("=" * 140)
    print(" RISK GOVERNOR DESTRUCTION SUMMARY")
    print("=" * 140)

    display = [
        "risk_pct",
        "cohort",
        "shock_pct",
        "tests",
        "median_return_pct",
        "worst_return_pct",
        "median_dd_pct",
        "worst_dd_pct",
        "worst_min_nav",
        "median_pf",
        "worst_pf",
        "median_expectancy_r",
        "worst_expectancy_r",
        "terminal_profit_rate_pct",
        "ruin_rate_pct",
        "dd_ceiling_pass",
    ]

    print(
        summary[
            display
        ].to_string(
            index=False
        )
    )

    print()
    print("=" * 140)
    print(" WORST -50% SHOCK BY RISK LEVEL")
    print("=" * 140)

    severe = (
        summary[
            summary[
                "shock_pct"
            ] == -50.0
        ]
        .sort_values(
            [
                "risk_pct",
                "cohort",
            ],
            ascending=[
                False,
                True,
            ],
        )
    )

    print(
        severe[
            [
                "risk_pct",
                "cohort",
                "worst_return_pct",
                "worst_dd_pct",
                "worst_min_nav",
                "worst_expectancy_r",
                "terminal_profit_rate_pct",
                "ruin_rate_pct",
                "dd_ceiling_pass",
            ]
        ].to_string(
            index=False
        )
    )

    print()
    print("=" * 140)
    print("OUTPUT")
    print("=" * 140)
    print(raw_path)
    print(summary_path)
    print("=" * 140)


if __name__ == "__main__":
    main()
