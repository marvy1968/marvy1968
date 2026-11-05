#!/usr/bin/env python3

import numpy as np
import pandas as pd
from pathlib import Path

INITIAL_NAV = 100_000.0
N_SIMS = 5000
SEED = 20260829
DD_GATE = 12.0

OUT = Path(
    "/home/moonrider1968/bots/tournament_phase1b_output"
)

STRATEGIES = {
    "Sector Top-Down": {
        "trades": OUT / "8_sector_top-down_trades.csv",
        "equity": OUT / "8_sector_top-down_equity.csv",
    },
    "Weinstein Stage 2": {
        "trades": OUT / "3_weinstein_stage_2_long_trades.csv",
        "equity": OUT / "3_weinstein_stage_2_long_equity.csv",
    },
    "Momentum Burst": {
        "trades": OUT / "5_momentum_burst_trades.csv",
        "equity": OUT / "5_momentum_burst_equity.csv",
    },
    "Multi-Layer Confluence": {
        "trades": OUT / "7_multi-layer_confluence_trades.csv",
        "equity": OUT / "7_multi-layer_confluence_equity.csv",
    },
}

rng = np.random.default_rng(SEED)


def trade_path_max_dd(pnls):
    eq = INITIAL_NAV + np.cumsum(pnls)
    eq = np.concatenate(([INITIAL_NAV], eq))
    peaks = np.maximum.accumulate(eq)
    dd = (peaks - eq) / np.maximum(peaks, 1e-12)
    return float(dd.max() * 100.0)


def mtm_max_dd(nav):
    nav = np.asarray(nav, dtype=float)

    if len(nav) == 0:
        return np.nan

    peaks = np.maximum.accumulate(nav)
    dd = (peaks - nav) / np.maximum(peaks, 1e-12)

    return float(dd.max() * 100.0)


def terminal_return(pnls):
    return float(
        np.sum(pnls) / INITIAL_NAV * 100.0
    )


def permutation_test(pnls):
    dds = np.empty(N_SIMS)

    for i in range(N_SIMS):
        path = rng.permutation(pnls)
        dds[i] = trade_path_max_dd(path)

    return {
        "p95_dd": np.percentile(dds, 95),
        "p99_dd": np.percentile(dds, 99),
        "worst_dd": np.max(dds),
    }


def block_bootstrap(pnls, block):
    n = len(pnls)

    dds = np.empty(N_SIMS)
    rets = np.empty(N_SIMS)

    for sim in range(N_SIMS):

        sampled = []

        while len(sampled) < n:

            if n <= block:
                start = 0
            else:
                start = rng.integers(
                    0,
                    n - block + 1
                )

            sampled.extend(
                pnls[start:start + block].tolist()
            )

        path = np.asarray(
            sampled[:n],
            dtype=float,
        )

        dds[sim] = trade_path_max_dd(path)
        rets[sim] = terminal_return(path)

    return {
        "p5_ret": float(np.percentile(rets, 5)),
        "median_ret": float(np.percentile(rets, 50)),
        "p95_ret": float(np.percentile(rets, 95)),
        "p95_dd": float(np.percentile(dds, 95)),
        "p99_dd": float(np.percentile(dds, 99)),
        "worst_dd": float(np.max(dds)),
        "p_profit": float(np.mean(rets > 0) * 100.0),
    }


print("=" * 118)
print("PHASE 2 v3 — QUALIFIED EXPERT ROBUSTNESS & ORTHOGONALITY")
print("=" * 118)

loaded = {}

for name, paths in STRATEGIES.items():

    tp = paths["trades"]
    ep = paths["equity"]

    if not tp.exists():
        raise FileNotFoundError(
            f"Missing trade file: {tp}"
        )

    if not ep.exists():
        raise FileNotFoundError(
            f"Missing equity file: {ep}"
        )

    trades = pd.read_csv(tp)
    equity = pd.read_csv(ep)

    required_trade_cols = {
        "exit_date",
        "pnl",
        "r_mult",
    }

    missing = (
        required_trade_cols
        - set(trades.columns)
    )

    if missing:
        raise RuntimeError(
            f"{name}: missing trade columns {missing}"
        )

    if "mtm_nav" not in equity.columns:
        raise RuntimeError(
            f"{name}: mtm_nav missing from equity file"
        )

    trades["exit_date"] = pd.to_datetime(
        trades["exit_date"],
        errors="raise",
    )

    trades["pnl"] = pd.to_numeric(
        trades["pnl"],
        errors="raise",
    )

    trades["r_mult"] = pd.to_numeric(
        trades["r_mult"],
        errors="raise",
    )

    equity["mtm_nav"] = pd.to_numeric(
        equity["mtm_nav"],
        errors="raise",
    )

    trades = trades.sort_values(
        "exit_date"
    ).reset_index(drop=True)

    loaded[name] = {
        "trades": trades,
        "equity": equity,
    }

    print(
        f"LOADED: {name:<25s} "
        f"trades={len(trades):4d} | "
        f"PnL=${trades['pnl'].sum():10,.2f}"
    )


print()
print("=" * 118)
print("1. PHASE-1B BASELINE RECONCILIATION")
print("=" * 118)

for name, obj in loaded.items():

    t = obj["trades"]
    e = obj["equity"]

    pnls = t["pnl"].to_numpy(float)

    mtm_dd = mtm_max_dd(
        e["mtm_nav"].to_numpy(float)
    )

    print(
        f"{name:<25s} | "
        f"Trades={len(t):4d} | "
        f"Return={terminal_return(pnls):7.2f}% | "
        f"MTM MaxDD={mtm_dd:6.2f}% | "
        f"MeanR={t['r_mult'].mean():+7.3f} | "
        f"PF="
        f"{t.loc[t.pnl > 0, 'pnl'].sum() / abs(t.loc[t.pnl < 0, 'pnl'].sum()):6.2f}"
    )


print()
print("=" * 118)
print("2. DAILY REALIZED-PNL CORRELATION MATRIX")
print("=" * 118)

daily = {}

for name, obj in loaded.items():

    df = obj["trades"].copy()

    df["day"] = (
        df["exit_date"]
        .dt.normalize()
    )

    daily[name] = (
        df.groupby("day")["pnl"].sum()
    )

daily_df = pd.DataFrame(
    daily
).fillna(0.0)

print(
    daily_df.corr()
    .round(3)
    .to_string()
)


print()
print("=" * 118)
print("3. 5,000-PATH PURE TRADE-SEQUENCE PERMUTATION")
print("=" * 118)

for name, obj in loaded.items():

    pnls = (
        obj["trades"]["pnl"]
        .to_numpy(float)
    )

    r = permutation_test(pnls)

    verdict = (
        "PASS"
        if r["p95_dd"] <= DD_GATE
        else "FAIL"
    )

    print(
        f"{name:<25s} | "
        f"P95DD={r['p95_dd']:6.2f}% | "
        f"P99DD={r['p99_dd']:6.2f}% | "
        f"Worst={r['worst_dd']:6.2f}% | "
        f"{verdict}"
    )


for block in [3, 7, 14]:

    print()
    print("=" * 118)
    print(
        f"4. 5,000-PATH BLOCK BOOTSTRAP "
        f"— BLOCK={block} TRADES"
    )
    print("=" * 118)

    for name, obj in loaded.items():

        pnls = (
            obj["trades"]["pnl"]
            .to_numpy(float)
        )

        r = block_bootstrap(
            pnls,
            block,
        )

        verdict = (
            "PASS"
            if (
                r["p95_dd"] <= DD_GATE
                and r["p5_ret"] > 0.0
            )
            else "FAIL"
        )

        print(
            f"{name:<25s} | "
            f"P5Ret={r['p5_ret']:7.2f}% | "
            f"MedRet={r['median_ret']:7.2f}% | "
            f"P95DD={r['p95_dd']:6.2f}% | "
            f"WorstDD={r['worst_dd']:6.2f}% | "
            f"P(Profit)={r['p_profit']:6.1f}% | "
            f"{verdict}"
        )


print()
print("=" * 118)
print("5. PAIRWISE CORRELATION — LOWEST TO HIGHEST")
print("=" * 118)

corr = daily_df.corr()

pairs = []

names = list(corr.columns)

for i in range(len(names)):

    for j in range(i + 1, len(names)):

        pairs.append(
            (
                float(
                    corr.loc[
                        names[i],
                        names[j]
                    ]
                ),
                names[i],
                names[j],
            )
        )

for value, a, b in sorted(pairs):

    print(
        f"{a:<25s} + "
        f"{b:<25s} "
        f"corr={value:+.3f}"
    )


print()
print("=" * 118)
print("PHASE 2 v3 COMPLETE")
print(
    "ROBUSTNESS GATE: "
    "P95 DD <= 12% AND "
    "P5 TERMINAL RETURN > 0%"
)
print("=" * 118)
