#!/usr/bin/env python3

import numpy as np
import pandas as pd
from pathlib import Path

INITIAL_NAV = 100_000.0
N_SIMS = 5000
SEED = 20260829
DD_GATE = 12.0

OUT_DIR = Path("/home/moonrider1968/bots/tournament_phase1b_output")
rng = np.random.default_rng(SEED)

def max_dd_pct(pnls):
    equity = INITIAL_NAV + np.cumsum(pnls)
    if len(equity) == 0:
        return 0.0
    equity = np.concatenate(([INITIAL_NAV], equity))
    peaks = np.maximum.accumulate(equity)
    dd = (peaks - equity) / np.maximum(peaks, 1e-12)
    return float(np.max(dd) * 100.0)

def terminal_return_pct(pnls):
    return float(np.sum(pnls) / INITIAL_NAV * 100.0)

def permutation_test(pnls):
    dds = np.empty(N_SIMS)
    for i in range(N_SIMS):
        path = rng.permutation(pnls)
        dds[i] = max_dd_pct(path)
    return {
        "p95_dd": float(np.percentile(dds, 95)),
        "p99_dd": float(np.percentile(dds, 99)),
        "worst_dd": float(np.max(dds)),
    }

def block_bootstrap(pnls, block):
    n = len(pnls)
    if n == 0:
        return None
    dds = np.empty(N_SIMS)
    rets = np.empty(N_SIMS)

    for sim in range(N_SIMS):
        sampled = []
        while len(sampled) < n:
            if n <= block:
                start = 0
            else:
                start = rng.integers(0, n - block + 1)
            sampled.extend(pnls[start:start + block].tolist())

        path = np.asarray(sampled[:n], dtype=float)
        dds[sim] = max_dd_pct(path)
        rets[sim] = terminal_return_pct(path)

    return {
        "p5_return": float(np.percentile(rets, 5)),
        "median_return": float(np.percentile(rets, 50)),
        "p95_return": float(np.percentile(rets, 95)),
        "p95_dd": float(np.percentile(dds, 95)),
        "p99_dd": float(np.percentile(dds, 99)),
        "worst_dd": float(np.max(dds)),
        "p_profit": float(np.mean(rets > 0.0) * 100.0),
    }

print("=" * 110)
print("PHASE 2 v2 — QUALIFIED EXPERT ROBUSTNESS & ORTHOGONALITY")
print("=" * 110)

# Strategy name aliases to match files on disk
QUALIFIED_EXPERTS = [
    "Sector Top-Down",
    "Weinstein Stage 2",
    "Momentum Burst",
    "Confluence"
]

all_csvs = list(OUT_DIR.glob("*.csv"))
loaded = {}

# Check for individual files or master ledger
master_file = OUT_DIR / "master_trades.csv"
if master_file.exists():
    master_df = pd.read_csv(master_file)
    for q in QUALIFIED_EXPERTS:
        matched_rows = master_df[master_df["expert"].str.contains(q, case=False, na=False)].copy()
        if not matched_rows.empty:
            matched_rows["exit_date"] = pd.to_datetime(matched_rows["exit_date"], utc=True, errors="coerce")
            matched_rows["pnl"] = pd.to_numeric(matched_rows["pnl"], errors="coerce")
            matched_rows = matched_rows.dropna(subset=["exit_date", "pnl"]).sort_values("exit_date").reset_index(drop=True)
            loaded[q] = matched_rows
else:
    for q in QUALIFIED_EXPERTS:
        for csv_path in all_csvs:
            clean_name = q.lower().replace(" ", "").replace("-", "")
            file_clean = csv_path.name.lower().replace(" ", "").replace("-", "").replace("_", "")
            if clean_name in file_clean:
                df = pd.read_csv(csv_path)
                df["exit_date"] = pd.to_datetime(df["exit_date"], utc=True, errors="coerce")
                df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce")
                df = df.dropna(subset=["exit_date", "pnl"]).sort_values("exit_date").reset_index(drop=True)
                loaded[q] = df
                break

for name, df in loaded.items():
    print(f"LOADED: {name:<24s} trades={len(df):<4d} pnl=${df['pnl'].sum():,.2f}")

if len(loaded) < 4:
    print("\n[!] Discovered CSVs in output directory:")
    for f in all_csvs:
        print(f"  • {f.name}")
    raise RuntimeError(f"Found {len(loaded)}/4 qualified strategy ledgers. Check naming mapping above.")

print()
print("=" * 110)
print("1. ACTUAL BASELINE DRAWDOWN")
print("=" * 110)
for name, df in loaded.items():
    pnls = df["pnl"].to_numpy(dtype=float)
    print(f"{name:<24s} Trades={len(pnls):4d} | Return={terminal_return_pct(pnls):8.2f}% | MaxDD={max_dd_pct(pnls):7.2f}%")

print()
print("=" * 110)
print("2. DAILY REALIZED-PNL CORRELATION MATRIX")
print("=" * 110)
daily = {}
for name, df in loaded.items():
    daily[name] = df.assign(day=df["exit_date"].dt.floor("D")).groupby("day")["pnl"].sum()

daily_df = pd.DataFrame(daily).fillna(0.0)
corr = daily_df.corr()
print(corr.round(3).to_string())

print()
print("=" * 110)
print("3. 5,000-PATH PURE SEQUENCE PERMUTATION")
print("=" * 110)
for name, df in loaded.items():
    pnls = df["pnl"].to_numpy(dtype=float)
    r = permutation_test(pnls)
    verdict = "PASS" if r["p95_dd"] <= DD_GATE else "FAIL"
    print(f"{name:<24s} | P95DD={r['p95_dd']:6.2f}% | P99DD={r['p99_dd']:6.2f}% | Worst={r['worst_dd']:6.2f}% | {verdict}")

for block in [3, 7, 14]:
    print()
    print("=" * 110)
    print(f"4. 5,000-PATH BLOCK BOOTSTRAP — BLOCK={block} TRADES")
    print("=" * 110)
    for name, df in loaded.items():
        pnls = df["pnl"].to_numpy(dtype=float)
        r = block_bootstrap(pnls, block)
        verdict = "PASS" if (r["p95_dd"] <= DD_GATE and r["p5_return"] > 0.0) else "FAIL"
        print(f"{name:<24s} | P5Ret={r['p5_return']:7.2f}% | MedRet={r['median_return']:7.2f}% | P95DD={r['p95_dd']:6.2f}% | P99DD={r['p99_dd']:6.2f}% | P(Profit)={r['p_profit']:6.1f}% | {verdict}")

print()
print("=" * 110)
print("PHASE 2 v2 COMPLETE")
print("PRIMARY ROBUSTNESS GATE: P95 DD <= 12% AND P5 TERMINAL RETURN > 0%")
print("=" * 110)
