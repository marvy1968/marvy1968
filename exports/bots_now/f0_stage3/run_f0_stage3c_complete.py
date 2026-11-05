#!/usr/bin/env python3

from pathlib import Path
import hashlib
import sys
import pandas as pd
import numpy as np

BOTS = Path("/home/moonrider1968/bots")
STAGE = BOTS / "f0_stage3"
STAGE.mkdir(exist_ok=True)

ENGINE = BOTS / "run_f0_hardened_portfolio_v2.py"
EVENTS_CSV = STAGE / "f0_canonical_628_events.csv"
DATA_DIR = BOTS / "oanda_data/h4"

EXPECTED_ENGINE_SHA = "fff76188cc77d0a742cfdb5f1694e6ea8f10bcc4c18234383508b96eeafd3ab6"
EXPECTED_EVENTS_SHA = "ba6d4f896443a0d16d1d9d0e59daa409155d319af654ddaabcb32f19e876933a"

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

print("=" * 110)
print("F0 STAGE 3C — COMPLETE GOVERNED PORTFOLIO REPLAY & AUDIT (FIXED)")
print("=" * 110)

assert sha(ENGINE) == EXPECTED_ENGINE_SHA, "FATAL: engine hash changed"
assert sha(EVENTS_CSV) == EXPECTED_EVENTS_SHA, "FATAL: canonical events hash changed"
print("PASS: Immutable hashes verified.")

sys.path.insert(0, str(BOTS))
import run_f0_hardened_portfolio_v2 as f0

events = pd.read_csv(EVENTS_CSV)
events['entry_time'] = pd.to_datetime(events['entry_time'], utc=True)
events['exit_time'] = pd.to_datetime(events['exit_time'], utc=True)
events['signal_time'] = pd.to_datetime(events['signal_time'], utc=True)
events['year'] = events['entry_time'].dt.year

feature_records = []
for _, row in events.iterrows():
    pair = row['pair']
    entry_idx = int(row['entry_idx'])
    fpath = DATA_DIR / f"{pair}_H4.csv"
    if not fpath.exists():
        feature_records.append({'atr_pctile': 0.5, 'abs_sma200_slope': 0.0})
        continue
    df = pd.read_csv(fpath)
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={'mid_c': 'close', 'mid_h': 'high', 'mid_l': 'low', 'mid_o': 'open'})
    if entry_idx >= len(df) or entry_idx < 200:
        feature_records.append({'atr_pctile': 0.5, 'abs_sma200_slope': 0.0})
        continue
    c, h, l = df['close'], df['high'], df['low']
    sma200 = c.rolling(200).mean()
    tr = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))
    atr14 = tr.rolling(14).mean()
    atr_pctile = atr14.rolling(200).rank(pct=True).iloc[entry_idx]
    sma200_slope = (sma200.diff(20) / (atr14 + 1e-9)).iloc[entry_idx]
    feature_records.append({
        'atr_pctile': atr_pctile if not np.isnan(atr_pctile) else 0.5,
        'abs_sma200_slope': abs(sma200_slope) if not np.isnan(sma200_slope) else 0.0
    })

feat_df = pd.DataFrame(feature_records)
events = pd.concat([events.reset_index(drop=True), feat_df], axis=1)

train_wf1 = events[events['year'].isin([2023, 2024])]
atr75_wf1 = train_wf1['atr_pctile'].quantile(0.75)
slope33_wf1 = train_wf1['abs_sma200_slope'].quantile(0.25)

train_wf2 = events[events['year'].isin([2023, 2024, 2025])]
atr75_wf2 = train_wf2['atr_pctile'].quantile(0.75)
slope33_wf2 = train_wf2['abs_sma200_slope'].quantile(0.25)

events['stage3c_accept'] = True
mask_2025 = events['year'] == 2025
mask_2026 = events['year'] == 2026

events.loc[mask_2025, 'stage3c_accept'] = (
    (events.loc[mask_2025, 'atr_pctile'] <= atr75_wf1) &
    (events.loc[mask_2025, 'abs_sma200_slope'] >= slope33_wf1)
)

events.loc[mask_2026, 'stage3c_accept'] = (
    (events.loc[mask_2026, 'atr_pctile'] <= atr75_wf2) &
    (events.loc[mask_2026, 'abs_sma200_slope'] >= slope33_wf2)
)

accepted_events = events[events['stage3c_accept']].copy()

# Enforce strict pandas Timestamp dtype for simulator time checks
accepted_events['entry_time'] = pd.to_datetime(accepted_events['entry_time'], utc=True)
accepted_events['exit_time'] = pd.to_datetime(accepted_events['exit_time'], utc=True)
accepted_events['signal_time'] = pd.to_datetime(accepted_events['signal_time'], utc=True)

print(f"Total Canonical Events   : {len(events)}")
print(f"Total Sequentially Gated : {len(accepted_events)}")

accepted_events.to_csv(STAGE / "f0_stage3c_sequential_accepted_events.csv", index=False)

print("\n[INFO] Running simulate_h4_portfolio for F0-G1 Gated Stream...")
gated_nav, gated_trades = f0.simulate_h4_portfolio(accepted_events, max_positions=3, use_firewall=True)

def compute_portfolio_metrics(nav_df, trades_df, name):
    start_nav = nav_df['mtm_nav'].iloc[0]
    final_nav = nav_df['mtm_nav'].iloc[-1]
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
    
    if len(trades_df) > 0:
        trades_df['year'] = pd.to_datetime(trades_df['entry_time']).dt.year
        print("Year-by-Year Performance:")
        for yr in sorted(trades_df['year'].unique()):
            yd = trades_df[trades_df['year'] == yr]
            yr_r = yd['r_mult'].values
            y_wins = yr_r[yr_r > 0].sum()
            y_losses = abs(yr_r[yr_r <= 0].sum())
            y_pf = (y_wins / y_losses) if y_losses > 0 else 999.0
            print(f"  {yr} | N: {len(yd):<3} | WR: {len(yr_r[yr_r > 0])/len(yr_r)*100:5.1f}% | Mean R: {yr_r.mean():+7.4f}R | PF: {y_pf:.2f}")

print("\n" + "="*50 + " BASELINE GOVERNED CONTROL (F0) " + "="*50)
compute_portfolio_metrics(f0.nav3, f0.t3, "F0 Governed Baseline Control")

print("\n" + "="*50 + " STAGE 3C CHALLENGER (F0-G1 GATED) " + "="*50)
compute_portfolio_metrics(gated_nav, gated_trades, "F0-G1 Sequentially Gated Governed")

print("\n" + "="*110)
print("STAGE 3C COMPLETE AUDIT PASSED")
print("=" * 110)
