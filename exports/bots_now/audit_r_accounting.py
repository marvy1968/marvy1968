#!/usr/bin/env python3
"""
R-Multiple & Dollar PnL Reconciliation Engine
"""

import os
import pandas as pd
import numpy as np
import run_stitched_oos_engine as eng

def audit_cohort(c_name, c_manifest):
    manifest, dfs = eng.load_data()
    btc_df = dfs["BTC/USD"]
    all_dates = btc_df.index[60:]
    oos_dates = all_dates[int(len(all_dates) * 0.50):]

    eng.FEE_RATE = 0.0010
    eng.SLIPPAGE_RATE = 0.0010
    eng.RISK_PER_TRADE = 0.0040

    df_trades, eq_curve = eng.simulate_continuous_oos(c_manifest, dfs, 250_000, 30, 2.0, oos_dates)

    total_net_pnl = df_trades["pnl"].sum()
    gross_profit = df_trades[df_trades["pnl"] > 0]["pnl"].sum()
    gross_loss = abs(df_trades[df_trades["pnl"] <= 0]["pnl"].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else np.inf

    mean_r = df_trades["r"].mean()
    median_r = df_trades["r"].median()
    win_rate = (df_trades["pnl"] > 0).mean() * 100.0

    print(f"\n==========================================================================")
    print(f" 🔍 AUDIT REPORT: {c_name.upper()}")
    print(f"==========================================================================")
    print(f"Total Completed Trades : {len(df_trades)}")
    print(f"Win Rate               : {win_rate:.1f}%")
    print(f"Gross Profit           : ${gross_profit:,.2f}")
    print(f"Gross Loss             : ${gross_loss:,.2f}")
    print(f"Net Realized PnL       : ${total_net_pnl:,.2f}")
    print(f"Profit Factor          : {pf:.2f}")
    print(f"True Mean Expectancy   : {mean_r:+.3f}R")
    print(f"Median Expectancy      : {median_r:+.3f}R")

if __name__ == "__main__":
    manifest, _ = eng.load_data()
    audit_cohort("CORE_N27", manifest[~manifest["symbol"].isin(["HYPE/USD", "PUMP/USD", "VVV/USD"])])
    audit_cohort("MATURE_N24", manifest[manifest["hit_720_ceiling"]])
