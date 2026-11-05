#!/usr/bin/env python3

from pathlib import Path
import sys
import pandas as pd
import numpy as np

BOTS = Path("/home/moonrider1968/bots")
STAGE = BOTS / "f0_stage3"
ACCEPTED_CSV = STAGE / "f0_stage3c_sequential_accepted_events.csv"

sys.path.insert(0, str(BOTS))
import run_f0_hardened_portfolio_v2 as f0

print("=" * 110)
print("F0 STAGE 3C — GOVERNED PORTFOLIO REPLAY (GATED VS BASELINE)")
print("=" * 110)

if not ACCEPTED_CSV.exists():
    sys.exit(f"FATAL: Accepted event stream not found at {ACCEPTED_CSV}. Run preflight first.")

# 1. Load Sequential Gated Events
gated_events = pd.read_csv(ACCEPTED_CSV)
gated_events['entry_time'] = pd.to_datetime(gated_events['entry_time'], utc=True)
gated_events['signal_time'] = pd.to_datetime(gated_events['signal_time'], utc=True)
gated_events['exit_time'] = pd.to_datetime(gated_events['exit_time'], utc=True)

print(f"[INFO] Loaded Sequential Gated Events: {len(gated_events)} trades (Retained {len(gated_events)/628*100:.1f}% of canonical universe).")

# 2. Run Governed Portfolio Replay on Gated Stream
print("[INFO] Executing simulate_h4_portfolio for F0-G1 (Max 3 Positions + Factor Firewall)...")
gated_nav, gated_trades = f0.simulate_h4_portfolio(gated_events, max_positions=3, use_firewall=True)

# 3. Helper to calculate detailed metrics
def compute_portfolio_metrics(nav_df, trades_df, name):
    start_nav = nav_df['mtm_nav'].iloc[0]
    final_nav = nav_df['mtm_nav'].iloc[-1]
    
    # Years span
    days = (nav_df.index[-1] - nav_df.index[0]).days
    years = days / 365.25 if days > 0 else 1.0
    cagr = ((final_nav / start_nav) ** (1 / years) - 1.0) * 100.0
    
    max_dd = nav_df['dd_pct'].min()
    
    r = trades_df['r_mult'].values if len(trades_df) > 0 else np.array([])
    if len(r) > 0:
        wins = r[r > 0].sum()
        losses = abs(r[r <= 0].sum())
        pf = (wins / losses) if losses > 0 else 999.0
        mean_r = r.mean()
        wr = (len(r[r > 0]) / len(r)) * 100.0
    else:
        pf, mean_r, wr = 0.0, 0.0, 0.0
        
    calmar = (cagr / abs(max_dd)) if max_dd != 0 else 0.0
    
    print(f"\n--- {name} ---")
    print(f"Trades Executed  : {len(trades_df)}")
    print(f"Win Rate         : {wr:.1f}%")
    print(f"Net Mean R       : {mean_r:+.4f}R")
    print(f"Profit Factor    : {pf:.2f}")
    print(f"Final NAV        : ${final_nav:,.2f}")
    print(f"Compounded CAGR  : {cagr:+.2f}%")
    print(f"Max True MTM DD  : {max_dd:.2f}%")
    print(f"Calmar Ratio     : {calmar:.2f}")
    
    # Year-by-year breakdown
    if len(trades_df) > 0:
        trades_df['year'] = trades_df['entry_time'].dt.year
        print("Year-by-Year Performance:")
        for yr in sorted(trades_df['year'].unique()):
            yd = trades_df[trades_df['year'] == yr]
            yr_r = yd['r_mult'].values
            y_wins = yr_r[yr_r > 0].sum()
            y_losses = abs(yr_r[yr_r <= 0].sum())
            y_pf = (y_wins / y_losses) if y_losses > 0 else 999.0
            print(f"  {yr} | N: {len(yd):<3} | WR: {len(yr_r[yr_r > 0])/len(yr_r)*100:5.1f}% | Mean R: {yr_r.mean():+7.4f}R | PF: {y_pf:.2f}")

# Compare Baseline Governed Control (f0.t3) vs Gated Replay
print("\n" + "="*50 + " BASELINE GOVERNED CONTROL (F0) " + "="*50)
compute_portfolio_metrics(f0.nav3, f0.t3, "F0 Governed Baseline Control")

print("\n" + "="*50 + " STAGE 3C CHALLENGER (F0-G1 GATED) " + "="*50)
compute_portfolio_metrics(gated_nav, gated_trades, "F0-G1 Sequentially Gated Governed")

print("\n" + "="*110)
print("STAGE 3C REPLAY COMPLETE")
print("=" * 110)
