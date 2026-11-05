#!/usr/bin/env python3
"""
Block-Bootstrap Monte Carlo Stress Engine (5,000 Paths)
Resamples contiguous weekly blocks (7 days) from the Stitched OOS equity stream
to stress-test regime duration, drawdowns, and ruin probability.
"""

import os
import pandas as pd
import numpy as np

DATA_DIR = "/home/moonrider1968/bots/crypto_data"
MANIFEST_PATH = os.path.join(DATA_DIR, "universe_manifest.csv")
BLOCK_SIZE_DAYS = 7
N_BOOTSTRAPS = 5000
INITIAL_CAPITAL = 100_000.0

from run_stitched_oos_engine import load_data, simulate_continuous_oos, FROZEN_CANDIDATES

def run_block_bootstrap(label, equity_curve):
    eq = pd.Series([x[1] for x in equity_curve], index=[x[0] for x in equity_curve], dtype=float)
    daily_returns = eq.pct_change().dropna().values
    n_days = len(daily_returns)
    n_blocks = int(np.ceil(n_days / BLOCK_SIZE_DAYS))

    # Construct blocks
    blocks = [daily_returns[i:i + BLOCK_SIZE_DAYS] for i in range(0, n_days - BLOCK_SIZE_DAYS + 1)]

    sim_terminal_returns = []
    sim_max_dds = []

    for _ in range(N_BOOTSTRAPS):
        chosen_blocks = [blocks[idx] for idx in np.random.choice(len(blocks), size=n_blocks, replace=True)]
        synth_returns = np.concatenate(chosen_blocks)[:n_days]
        
        # Compound synthetic equity curve
        synth_nav = INITIAL_CAPITAL * np.cumprod(1.0 + synth_returns)
        peak = np.maximum.accumulate(synth_nav)
        dd = (synth_nav - peak) / peak
        
        sim_terminal_returns.append(((synth_nav[-1] / INITIAL_CAPITAL) - 1.0) * 100.0)
        sim_max_dds.append(abs(np.min(dd)) * 100.0)

    sim_terminal_returns = np.array(sim_terminal_returns)
    sim_max_dds = np.array(sim_max_dds)

    print(f"\n==========================================================================")
    print(f" 🎲 5,000-PATH BLOCK-BOOTSTRAP STRESS: {label.upper()}")
    print(f"==========================================================================")
    print(f"Block Window Resampled  : {BLOCK_SIZE_DAYS} Days (Preserves Volatility Clustering)")
    print(f"Median Synthetic Return : {np.median(sim_terminal_returns):+.2f}%")
    print(f"Mean Max Drawdown       : {np.mean(sim_max_dds):.2f}%")
    print(f"95th Percentile Max DD  : {np.percentile(sim_max_dds, 95):.2f}%")
    print(f"99th Percentile Max DD  : {np.percentile(sim_max_dds, 99):.2f}%")
    print(f"Worst Extreme DD Path   : {np.max(sim_max_dds):.2f}%")
    print(f"Ruin Probability (<50k) : {(sim_terminal_returns < -50.0).sum() / N_BOOTSTRAPS * 100.0:.2f}%")

def main():
    manifest, dfs = load_data()
    btc_df = dfs["BTC/USD"]
    all_dates = btc_df.index[60:]
    oos_dates = all_dates[int(len(all_dates) * 0.50):]

    for label, liq, lb, atr in FROZEN_CANDIDATES:
        _, eq_curve = simulate_continuous_oos(manifest, dfs, liq, lb, atr, oos_dates)
        run_block_bootstrap(label, eq_curve)

if __name__ == "__main__":
    main()
