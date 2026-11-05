#!/usr/bin/env python3
"""
Frozen Beta Correlated-Crash Destruction Test

NO parameter optimization.

Frozen strategy:
  Liquidity floor = $250,000
  Donchian        = 30 days
  ATR             = 2.0x

Tests:
  N=27 core cohort excluding HYPE/PUMP/VVV
  Mature N=24 cohort

Injects correlated market crashes:
  -10%, -20%, -30%, -50%

at multiple chronological OOS dates across the timeline.
All derived indicators and macro regime gates are dynamically recomputed post-shock.
"""

import copy
import numpy as np
import pandas as pd

import run_stitched_oos_engine as eng

INITIAL_CAPITAL = 100_000.0
LIQ_FLOOR = 250_000
LOOKBACK = 30
ATR_MULT = 2.0

eng.FEE_RATE = 0.0010
eng.SLIPPAGE_RATE = 0.0010

YOUNG_TRIO = {
    "HYPE/USD",
    "PUMP/USD",
    "VVV/USD",
}

MATURE_24 = {
    "BTC/USD", "ETH/USD", "XRP/USD", "SOL/USD", "ADA/USD",
    "ZEC/USD", "XMR/USD", "TAO/USD", "DOGE/USD", "SUI/USD",
    "LINK/USD", "XLM/USD", "AVAX/USD", "LTC/USD", "UNI/USD",
    "CRV/USD", "ONDO/USD", "NEAR/USD", "AAVE/USD", "TRX/USD",
    "PEPE/USD", "ICP/USD", "ENA/USD", "FET/USD",
}

SHOCK_LEVELS = [
    -0.10,
    -0.20,
    -0.30,
    -0.50,
]

def recompute_features(df):
    df = df.copy()

    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ),
    )

    df["atr14"] = df["tr"].rolling(14).mean()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["sma50"] = df["close"].rolling(50).mean()
    df["donch_high_30"] = df["high"].shift(1).rolling(30).max()
    df["quote_volume"] = df["close"] * df["volume"]
    df["med_vol_30d"] = df["quote_volume"].rolling(30, min_periods=30).median()

    return df

def shock_dataset(base_dfs, shock_date, shock_pct):
    shocked = {}
    factor = 1.0 + shock_pct

    for symbol, original in base_dfs.items():
        df = original.copy()
        mask = df.index >= shock_date

        for col in ["open", "high", "low", "close"]:
            df.loc[mask, col] = df.loc[mask, col] * factor

        shocked[symbol] = recompute_features(df)

    return shocked

def score(trades, equity_curve):
    eq = pd.Series(
        [x[1] for x in equity_curve],
        index=[x[0] for x in equity_curve],
        dtype=float,
    )
    eq = eq[~eq.index.duplicated(keep="last")]

    terminal = float(eq.iloc[-1])
    net_return = (terminal / INITIAL_CAPITAL - 1.0) * 100.0
    peak = eq.cummax()
    dd = eq / peak - 1.0
    max_dd = abs(float(dd.min())) * 100.0
    min_nav = float(eq.min())

    if len(trades) > 0:
        pnl = trades["pnl"].astype(float)
        r = trades["r"].astype(float)
        wins = pnl[pnl > 0]
        losses = pnl[pnl <= 0]
        gp = float(wins.sum())
        gl = abs(float(losses.sum()))
        pf = gp / gl if gl > 0 else np.inf
        expectancy = float(r.mean())
    else:
        pf = 0.0
        expectancy = 0.0

    return {
        "return_pct": net_return,
        "max_dd_pct": max_dd,
        "min_nav": min_nav,
        "trades": len(trades),
        "pf": pf,
        "expectancy_r": expectancy,
    }

def run_one(manifest, dfs, oos_dates):
    trades, equity = eng.simulate_continuous_oos(
        manifest,
        dfs,
        LIQ_FLOOR,
        LOOKBACK,
        ATR_MULT,
        oos_dates,
    )
    return score(trades, equity)

def main():
    print("=" * 130)
    print(" 💥 FROZEN BETA CORRELATED-CRASH DESTRUCTION TEST")
    print("=" * 130)

    manifest, base_dfs = eng.load_data()
    btc = base_dfs["BTC/USD"]
    all_dates = btc.index[60:]
    oos_dates = all_dates[int(len(all_dates) * 0.50):]

    cohorts = {
        "CORE_N27": manifest[~manifest["symbol"].isin(YOUNG_TRIO)].copy(),
        "MATURE_N24": manifest[manifest["symbol"].isin(MATURE_24)].copy(),
    }

    eligible = oos_dates[30:-30]
    shock_dates = eligible[::14]
    rows = []

    for cohort_name, cohort_manifest in cohorts.items():
        print()
        print("=" * 130)
        print(f" COHORT: {cohort_name} ({len(cohort_manifest)} assets)")
        print("=" * 130)

        baseline = run_one(cohort_manifest, base_dfs, oos_dates)
        print(
            f"Baseline: "
            f"Ret={baseline['return_pct']:+.2f}% | "
            f"DD={baseline['max_dd_pct']:.2f}% | "
            f"PF={baseline['pf']:.2f} | "
            f"Exp={baseline['expectancy_r']:+.3f}R"
        )

        for shock_pct in SHOCK_LEVELS:
            for shock_date in shock_dates:
                shocked_dfs = shock_dataset(base_dfs, shock_date, shock_pct)
                result = run_one(cohort_manifest, shocked_dfs, oos_dates)
                rows.append({
                    "cohort": cohort_name,
                    "shock_pct": shock_pct * 100.0,
                    "shock_date": shock_date.strftime("%Y-%m-%d"),
                    **result,
                })

    raw = pd.DataFrame(rows)
    raw_path = "/home/moonrider1968/bots/crypto_data/beta_crash_destruction_raw.csv"
    raw.to_csv(raw_path, index=False)

    summary = (
        raw.groupby(["cohort", "shock_pct"])
        .agg(
            tests=("return_pct", "size"),
            median_return_pct=("return_pct", "median"),
            worst_return_pct=("return_pct", "min"),
            median_dd_pct=("max_dd_pct", "median"),
            worst_dd_pct=("max_dd_pct", "max"),
            worst_min_nav=("min_nav", "min"),
            median_pf=("pf", "median"),
            worst_pf=("pf", "min"),
            median_expectancy_r=("expectancy_r", "median"),
            worst_expectancy_r=("expectancy_r", "min"),
            min_trades=("trades", "min"),
        )
        .reset_index()
    )

    summary["terminal_profit_rate_pct"] = (
        raw.assign(profitable=(raw["return_pct"] > 0))
        .groupby(["cohort", "shock_pct"])["profitable"]
        .mean()
        .values * 100.0
    )

    summary["ruin_rate_pct"] = (
        raw.assign(ruined=(raw["min_nav"] < 50_000.0))
        .groupby(["cohort", "shock_pct"])["ruined"]
        .mean()
        .values * 100.0
    )

    summary_path = "/home/moonrider1968/bots/crypto_data/beta_crash_destruction_summary.csv"
    summary.to_csv(summary_path, index=False)

    print()
    print("=" * 130)
    print(" DESTRUCTION SUMMARY")
    print("=" * 130)

    display = [
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
    ]

    print(summary[display].to_string(index=False))

    print()
    print("=" * 130)
    print(" OUTPUT")
    print("=" * 130)
    print(raw_path)
    print(summary_path)
    print("=" * 130)

if __name__ == "__main__":
    main()
