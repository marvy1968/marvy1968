#!/usr/bin/env python3

from pathlib import Path
import sys
import hashlib
import numpy as np
import pandas as pd

BOTS = Path("/home/moonrider1968/bots")
STAGE = BOTS / "f0_stage3"
ACCEPTED_CSV = STAGE / "f0_stage3c_sequential_accepted_events.csv"
OUT_CSV = STAGE / "f0_g1_friction_escalation_v3.csv"

print("=" * 120)
print("F0-G1 TRUE UPSTREAM FRICTION ESCALATION STRESS TEST (V3)")
print("=" * 120)

if not ACCEPTED_CSV.exists():
    sys.exit(f"FATAL: Accepted event stream not found at {ACCEPTED_CSV}.")

sys.path.insert(0, str(BOTS))
import run_f0_hardened_portfolio_v2 as f0

events = pd.read_csv(ACCEPTED_CSV)
for c in ["signal_time", "entry_time", "exit_time"]:
    if c in events.columns:
        events[c] = pd.to_datetime(events[c], utc=True)

multipliers = [1.0, 1.5, 2.0, 3.0, 5.0]
results = []

print(f"\n[INFO] Running Upstream Price-Path Friction Scaling on {len(events)} F0-G1 Gated Trades...")

for mult in multipliers:
    mod_events = events.copy()
    
    for idx, row in mod_events.iterrows():
        side = row['side']
        pair = row['pair']
        entry_px = float(row['entry_px'])
        exit_px = float(row['exit_px'])
        stop_dist = float(row['stop_dist_quote'])
        target_dollar_risk = 500.0
        
        pip_val = f0.get_pip_value(pair)
        base_friction_cost = 2.0 * pip_val
        additional_friction = base_friction_cost * (mult - 1.0)
        
        if side == "BUY":
            new_entry = entry_px + (additional_friction / 2.0)
            new_exit = exit_px - (additional_friction / 2.0)
        else:
            new_entry = entry_px - (additional_friction / 2.0)
            new_exit = exit_px + (additional_friction / 2.0)
            
        quote_to_usd = f0.get_quote_to_usd_rate(pair, row['entry_time'])
        usd_risk_per_unit = stop_dist * quote_to_usd
        units = target_dollar_risk / usd_risk_per_unit if usd_risk_per_unit > 0 else 0
        
        quote_pnl = (new_exit - new_entry) * units if side == "BUY" else (new_entry - new_exit) * units
        net_pnl_usd = quote_pnl * quote_to_usd
        r_mult = net_pnl_usd / target_dollar_risk
        
        mod_events.loc[idx, 'entry_px'] = new_entry
        mod_events.loc[idx, 'exit_px'] = new_exit
        mod_events.loc[idx, 'r_mult'] = r_mult
        
    nav, trades = f0.simulate_h4_portfolio(mod_events, max_positions=3, use_firewall=True)
    
    start_nav = nav['mtm_nav'].iloc[0]
    final_nav = nav['mtm_nav'].iloc[-1]
    days = (nav.index[-1] - nav.index[0]).days
    years = days / 365.25 if days > 0 else 1.0
    cagr = ((final_nav / start_nav) ** (1 / years) - 1.0) * 100.0
    max_dd = abs(nav['dd_pct'].min())
    
    r_arr = trades['r_mult'].values if len(trades) > 0 else np.array([])
    mean_r = r_arr.mean() if len(r_arr) > 0 else np.nan
    
    wins = r_arr[r_arr > 0].sum() if len(r_arr) > 0 else 0
    losses = abs(r_arr[r_arr <= 0].sum()) if len(r_arr) > 0 else 0
    pf = wins / losses if losses > 0 else np.inf
    
    results.append({
        "friction_mult": f"{mult:.1f}x",
        "trades": len(trades),
        "mean_r": mean_r,
        "profit_factor": pf,
        "final_nav": final_nav,
        "cagr_pct": cagr,
        "max_dd_pct": max_dd
    })

res_df = pd.DataFrame(results)
res_df.to_csv(OUT_CSV, index=False)

print("\n" + "="*120)
print("TRUE UPSTREAM PRICE-PATH FRICTION ESCALATION RESULTS")
print("="*120)
print(res_df.to_string(index=False))
print("="*120)
print(f"Artifact saved to: {OUT_CSV}")
print("="*120)
