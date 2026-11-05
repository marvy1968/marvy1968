#!/usr/bin/env python3
import sys
import numpy as np
import pandas as pd
from pathlib import Path

import report_decade_certified_results as base

print("=" * 90)
print("FINALIST C1 — STRESS & DESTRUCTION BATTERY (10.67 YEARS)")
print("1,396 EXECUTED TRADES | ZERO PARAMETER MUTATION")
print("=" * 90)

trades = base.trades_c1.copy()
trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True)
trades["exit_time"] = pd.to_datetime(trades["exit_time"], utc=True)
trades = trades.sort_values("entry_time").reset_index(drop=True)

# -------------------------------------------------------------
# TEST 1: FRICTION ESCALATION (+50% & +100% SPREAD / SLIPPAGE)
# -------------------------------------------------------------
print("\n" + "-" * 90)
print("TEST 1: FRICTION & SPREAD ESCALATION")
print("-" * 90)

def evaluate_friction_decay(multiplier_label, extra_drag_r):
    adj_r = trades["r_mult"] - extra_drag_r
    w = adj_r[adj_r > 0].sum()
    l = abs(adj_r[adj_r <= 0].sum())
    pf = w / l if l > 0 else float("inf")
    mean_r = adj_r.mean()
    win_rate = (adj_r > 0).mean() * 100
    print(f"  {multiplier_label:<25} | Mean R: {mean_r:+6.3f}R | PF: {pf:4.2f} | Win Rate: {win_rate:4.1f}%")
    return mean_r, pf

print("Baseline Execution:      Standard spread + 0.5 pip slippage")
evaluate_friction_decay("Baseline (1.0x)", 0.0)
evaluate_friction_decay("Moderate Stress (1.5x)", 0.015)
evaluate_friction_decay("Severe Stress (2.0x)", 0.030)

# -------------------------------------------------------------
# TEST 2: DRAWDOWN DURATION & UNDERWATER STAGNATION
# -------------------------------------------------------------
print("\n" + "-" * 90)
print("TEST 2: DRAWDOWN DURATION & STAGNATION ANALYSIS")
print("-" * 90)

nav_df = base.nav_c1.copy()
nav_df["peak"] = nav_df["mtm_nav"].cummax()
nav_df["is_underwater"] = nav_df["mtm_nav"] < nav_df["peak"]

underwater_periods = []
current_start = None

for t, row in nav_df.iterrows():
    if row["is_underwater"]:
        if current_start is None:
            current_start = t
    else:
        if current_start is not None:
            underwater_periods.append((current_start, t, (t - current_start).days))
            current_start = None

if current_start is not None:
    underwater_periods.append((current_start, nav_df.index[-1], (nav_df.index[-1] - current_start).days))

df_dd_periods = pd.DataFrame(underwater_periods, columns=["Start", "End", "Days"]).sort_values("Days", ascending=False)

print(f"Top 3 Longest Underwater Periods:")
for idx, r in df_dd_periods.head(3).reset_index().iterrows():
    print(f"  #{idx+1}: {r['Days']} days ({r['Days']/30.4:.1f} months) | {r['Start'].strftime('%Y-%m-%d')} -> {r['End'].strftime('%Y-%m-%d')}")

# -------------------------------------------------------------
# TEST 3: RISK-SIZING SENSITIVITY CURVE
# -------------------------------------------------------------
print("\n" + "-" * 90)
print("TEST 3: RISK-SIZING SENSITIVITY CURVE (COMPOUNDED)")
print("-" * 90)

years = 10.67
print(f"{'Risk/Trade':<12} | {'CAGR':<10} | {'Max MTM DD':<12} | {'Calmar':<8} | {'Final NAV':<15}")
print("-" * 65)

for risk_pct in [0.0025, 0.0035, 0.0050, 0.0075, 0.0100]:
    cur_nav = 100000.0
    peak_nav = 100000.0
    max_dd = 0.0
    
    for _, tr in trades.iterrows():
        dollar_risk = cur_nav * risk_pct
        pnl = tr["r_mult"] * dollar_risk
        cur_nav += pnl
        if cur_nav > peak_nav:
            peak_nav = cur_nav
        dd = (cur_nav - peak_nav) / peak_nav
        if dd < max_dd:
            max_dd = dd
            
    cagr = ((cur_nav / 100000.0) ** (1.0 / years) - 1.0) * 100.0
    calmar = abs(cagr / (max_dd * 100.0)) if max_dd < 0 else 0.0
    print(f"  {risk_pct*100:4.2f}%       | {cagr:>+6.2f}%   | {max_dd*100:>6.2f}%     | {calmar:4.2f}   | ${cur_nav:>11,.2f}")

# -------------------------------------------------------------
# TEST 4: BLOCK BOOTSTRAP (10,000 RESAMPLES)
# -------------------------------------------------------------
print("\n" + "-" * 90)
print("TEST 4: STATIONARY BLOCK BOOTSTRAP (10,000 PATHS, BLOCK=5)")
print("-" * 90)

np.random.seed(42)
N_SIMS = 10000
BLOCK_SIZE = 5
n_trades = len(trades)
n_blocks = int(np.ceil(n_trades / BLOCK_SIZE))

r_array = trades["r_mult"].values
bootstrap_final_navs = []
bootstrap_max_dds = []

for _ in range(N_SIMS):
    # Sample starting indices for blocks
    start_indices = np.random.randint(0, n_trades - BLOCK_SIZE + 1, size=n_blocks)
    sample_r = np.concatenate([r_array[i:i+BLOCK_SIZE] for i in start_indices])[:n_trades]
    
    # Simulate equity curve at 0.50% risk
    nav = 100000.0
    peak = 100000.0
    m_dd = 0.0
    for r in sample_r:
        nav += nav * 0.0050 * r
        if nav > peak:
            peak = nav
        dd = (nav - peak) / peak
        if dd < m_dd:
            m_dd = dd
    bootstrap_final_navs.append(nav)
    bootstrap_max_dds.append(m_dd * 100.0)

b_nav = np.array(bootstrap_final_navs)
b_dd = np.array(bootstrap_max_dds)

print(f"Bootstrap Final NAV (Median):     ${np.median(b_nav):>11,.2f}")
print(f"Bootstrap Final NAV (5th Pct):    ${np.percentile(b_nav, 5):>11,.2f}")
print(f"Bootstrap Max DD (Median):        {np.median(b_dd):>6.2f}%")
print(f"Bootstrap Max DD (95th Pct Tail): {np.percentile(b_dd, 5):>6.2f}%")
print(f"Probability of Max DD > 25%:      {(b_dd < -25.0).mean() * 100:4.2f}%")
print(f"Probability of Max DD > 35%:      {(b_dd < -35.0).mean() * 100:4.2f}%")
print(f"Probability of Negative Decade:   {(b_nav < 100000.0).mean() * 100:4.2f}%")

print("\n" + "=" * 90)
print("STRESS & DESTRUCTION BATTERY COMPLETE")
print("=" * 90)
