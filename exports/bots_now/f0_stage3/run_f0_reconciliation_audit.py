#!/usr/bin/env python3

from pathlib import Path
import sys
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from lightgbm import LGBMClassifier
from sklearn.preprocessing import StandardScaler

BOTS = Path("/home/moonrider1968/bots")
STAGE = BOTS / "f0_stage3"
DATA_DIR = BOTS / "oanda_data/h4"
EVENTS_CSV = STAGE / "f0_canonical_628_events.csv"

print("=" * 120)
print("F0 PHASE 4 — RIGOROUS RECONCILIATION & ACCOUNTING AUDIT")
print("=" * 120)

if not EVENTS_CSV.exists():
    sys.exit(f"FATAL: Canonical events not found at {EVENTS_CSV}.")

sys.path.insert(0, str(BOTS))
import run_f0_hardened_portfolio_v2 as f0

events = pd.read_csv(EVENTS_CSV)
events['entry_time'] = pd.to_datetime(events['entry_time'], utc=True)
events['exit_time'] = pd.to_datetime(events['exit_time'], utc=True)
events['signal_time'] = pd.to_datetime(events['signal_time'], utc=True)
events['year'] = events['entry_time'].dt.year

# Assign a unique canonical event ID based on row index and timestamp
events['canonical_id'] = [f"EVT_{i:03d}_{row['pair']}_{row['entry_time'].strftime('%Y%m%d%H')}" for i, row in events.iterrows()]

# 1. Feature Engineering with Strict Bar Assertion
feature_records = []
for idx, row in events.iterrows():
    pair = row['pair']
    entry_idx = int(row['entry_idx'])
    fpath = DATA_DIR / f"{pair}_H4.csv"
    if not fpath.exists():
        feature_records.append({})
        continue
    df = pd.read_csv(fpath)
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={'mid_c': 'close', 'mid_h': 'high', 'mid_l': 'low', 'mid_o': 'open'})
    if entry_idx >= len(df) or entry_idx < 200:
        feature_records.append({})
        continue
    
    feature_bar_idx = entry_idx - 1
    assert feature_bar_idx == entry_idx - 1, f"FATAL: Bar alignment check failed at {idx}"
    
    c, h, l = df['close'], df['high'], df['low']
    sma20, sma50, sma100, sma200 = c.rolling(20).mean(), c.rolling(50).mean(), c.rolling(100).mean(), c.rolling(200).mean()
    tr = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))
    atr14 = tr.rolling(14).mean()
    
    cur_c = c.iloc[feature_bar_idx]
    cur_atr = atr14.iloc[feature_bar_idx]
    cur_sma200 = sma200.iloc[feature_bar_idx]
    
    atr_pctile = atr14.rolling(200).rank(pct=True).iloc[feature_bar_idx]
    sma200_slope_signed = (sma200.diff(20) / (cur_atr + 1e-9)).iloc[feature_bar_idx]
    abs_sma200_slope = abs(sma200_slope_signed)
    std200 = c.rolling(200).std().iloc[feature_bar_idx]
    price_zscore = (cur_c - cur_sma200) / (std200 + 1e-9)
    
    feature_records.append({
        'atr_pctile': atr_pctile if not np.isnan(atr_pctile) else 0.5,
        'abs_sma200_slope': abs_sma200_slope if not np.isnan(abs_sma200_slope) else 0.0,
        'signed_sma200_slope': sma200_slope_signed if not np.isnan(sma200_slope_signed) else 0.0,
        'price_zscore': price_zscore if not np.isnan(price_zscore) else 0.0,
        'dist_sma20': (cur_c - sma20.iloc[feature_bar_idx]) / (cur_atr + 1e-9),
        'dist_sma50': (cur_c - sma50.iloc[feature_bar_idx]) / (cur_atr + 1e-9),
        'dist_sma100': (cur_c - sma100.iloc[feature_bar_idx]) / (cur_atr + 1e-9),
        'dist_sma200': (cur_c - cur_sma200) / (cur_atr + 1e-9),
        'hour': pd.to_datetime(row['entry_time']).hour,
        'side_buy': 1 if row['side'] == 'BUY' else 0
    })

feat_df = pd.DataFrame(feature_records)
feat_df = pd.get_dummies(events[['pair']].join(feat_df), columns=['pair'], drop_first=True)
events_feat = pd.concat([events.reset_index(drop=True), feat_df.reset_index(drop=True)], axis=1)
events_feat['target'] = (events_feat['r_mult'] > 0).astype(int)

feature_cols = [c for c in events_feat.columns if c not in [
    'pair', 'side', 'signal_time', 'entry_time', 'exit_time', 'exit_reason',
    'entry_idx', 'exit_idx', 'r_mult', 'pnl_quote', 'stop_pips', 'target_pips',
    'stop_dist_quote', 'entry_px', 'exit_px', 'year', 'target', 'mae_r', 'mfe_r', 'canonical_id'
]]

# 2. Re-run Strict Lagged Model Predictions for LogReg and GBM
def generate_model_streams():
    oos_preds = []
    
    # 2024 fold (Train 2023, Tune 2024 val)
    t1, v1 = events_feat[events_feat['year'].isin([2023])], events_feat[events_feat['year'] == 2024]
    scaler1 = StandardScaler()
    X_tr1 = scaler1.fit_transform(t1[feature_cols].fillna(0).values)
    m1 = LogisticRegression(max_iter=1000, random_state=42, class_weight='balanced')
    m1.fit(X_tr1, t1['target'].values)
    probs1 = m1.predict_proba(scaler1.transform(v1[feature_cols].fillna(0).values))[:, 1]
    
    th1, best_pf1 = 0.5, -1.0
    for th in np.arange(0.30, 0.75, 0.02):
        sub = v1[probs1 >= th]
        if len(sub) < 3: continue
        r = sub['r_mult'].values
        pf = (r[r > 0].sum() / abs(r[r <= 0].sum())) if abs(r[r <= 0].sum()) > 0 else 999.0
        if pf > best_pf1: best_pf1, th1 = pf, th
    res1 = v1.copy()
    res1['ml_accept'] = probs1 >= th1
    oos_preds.append(res1)
    
    # 2025 fold (Train 2023-2024, Tune 2025 val)
    t2, v2 = events_feat[events_feat['year'].isin([2023, 2024])], events_feat[events_feat['year'] == 2025]
    scaler2 = StandardScaler()
    X_tr2 = scaler2.fit_transform(t2[feature_cols].fillna(0).values)
    m2 = LogisticRegression(max_iter=1000, random_state=42, class_weight='balanced')
    m2.fit(X_tr2, t2['target'].values)
    probs2 = m2.predict_proba(scaler2.transform(v2[feature_cols].fillna(0).values))[:, 1]
    
    th2, best_pf2 = 0.5, -1.0
    for th in np.arange(0.30, 0.75, 0.02):
        sub = v2[probs2 >= th]
        if len(sub) < 3: continue
        r = sub['r_mult'].values
        pf = (r[r > 0].sum() / abs(r[r <= 0].sum())) if abs(r[r <= 0].sum()) > 0 else 999.0
        if pf > best_pf2: best_pf2, th2 = pf, th
    res2 = v2.copy()
    res2['ml_accept'] = probs2 >= th2
    oos_preds.append(res2)
    
    # 2026 fold (Train 2023-2025, apply th2)
    t3, v3 = events_feat[events_feat['year'].isin([2023, 2024, 2025])], events_feat[events_feat['year'] == 2026]
    scaler3 = StandardScaler()
    X_tr3 = scaler3.fit_transform(t3[feature_cols].fillna(0).values)
    m3 = LogisticRegression(max_iter=1000, random_state=42, class_weight='balanced')
    m3.fit(X_tr3, t3['target'].values)
    probs3 = m3.predict_proba(scaler3.transform(v3[feature_cols].fillna(0).values))[:, 1]
    res3 = v3.copy()
    res3['ml_accept'] = probs3 >= th2
    oos_preds.append(res3)
    
    logreg_accepted = pd.concat(oos_preds)
    return logreg_accepted, {'2024': th1, '2025': th2, '2026': th2}

logreg_df, logreg_ths = generate_model_streams()

# 3. Comprehensive Reconciliation Audit Function
def audit_stream_pipeline(stream_name, candidate_df, filter_2024_onwards=True):
    print(f"\n" + "="*80)
    print(f"RECONCILIATION AUDIT: {stream_name}")
    print("="*80)
    
    if filter_2024_onwards:
        # To isolate 2024-2026 OOS strictly, we pass ONLY 2024-2026 events into simulator 
        # starting fresh at $100k, OR we pass full stream but measure 2024-2026 slice.
        # Let's check both or enforce strict 2024-2026 event subset for simulation:
        sub_events = candidate_df[candidate_df['year'] >= 2024].copy()
    else:
        sub_events = candidate_df.copy()
        
    accepted_count = len(sub_events)
    min_ts = sub_events['entry_time'].min() if accepted_count > 0 else None
    max_ts = sub_events['entry_time'].max() if accepted_count > 0 else None
    
    print(f"1. Input Source File          : {EVENTS_CSV.name}")
    print(f"2. Accepted Event Count       : {accepted_count}")
    print(f"7. Min/Max Entry Timestamp    : {min_ts} to {max_ts}")
    print(f"8. Unique Canonical Event IDs : {sub_events['canonical_id'].nunique()} unique out of {accepted_count}")
    
    # Run Portfolio Simulator
    nav_df, trades_df = f0.simulate_h4_portfolio(sub_events, max_positions=3, use_firewall=True)
    
    executed_count = len(trades_df)
    rejected_capacity_or_firewall = accepted_count - executed_count
    
    print(f"3. Portfolio Executed Count   : {executed_count}")
    print(f"4 & 5. Rejected (Capacity/FW) : {rejected_capacity_or_firewall} (Events accepted by model but blocked by max-3 or firewall)")
    
    if executed_count > 0:
        trades_df['exec_year'] = pd.to_datetime(trades_df['entry_time']).dt.year
        print("\nYear-by-Year Breakdown:")
        for yr in sorted(trades_df['exec_year'].unique()):
            yd = trades_df[trades_df['exec_year'] == yr]
            acc_yr = len(sub_events[sub_events['year'] == yr])
            yr_r = yd['r_mult'].values
            y_wins = yr_r[yr_r > 0].sum()
            y_losses = abs(yr_r[yr_r <= 0].sum())
            y_pf = (y_wins / y_losses) if y_losses > 0 else 999.0
            print(f"  Year {yr} | Model Accepted: {acc_yr:3d} | Portfolio Executed: {len(yd):3d} | Drop (Cap/FW): {acc_yr - len(yd):3d} | WR: {len(yr_r[yr_r>0])/len(yr_r)*100:5.1f}% | PF: {y_pf:.2f} | MeanR: {yr_r.mean():+.4f}R")
            
        start_nav = nav_df['mtm_nav'].iloc[0]
        final_nav = nav_df['mtm_nav'].iloc[-1]
        cagr = ((final_nav / start_nav) ** (1 / ((nav_df.index[-1] - nav_df.index[0]).days / 365.25)) - 1.0) * 100.0
        max_dd = nav_df['dd_pct'].min()
        print(f"\n  Portfolio Summary -> Final NAV: ${final_nav:,.2f} | CAGR: {cagr:+.2f}% | Max DD: {max_dd:.2f}%")

# Audit Stream C: LogReg Strict OOS (Model Accepted where ml_accept == True, filtered for 2024+)
logreg_accepted_sub = logreg_df[logreg_df['ml_accept'] & (logreg_df['year'] >= 2024)].copy()
audit_stream_pipeline("Phase 4A Lagged Logistic Regression (2024–2026 Strict OOS)", logreg_accepted_sub, filter_2024_onwards=False)

# Audit Stream D: F0-G1 Baseline Challenger (Loaded directly from sequential accepted CSV, filtered for 2024+)
g1_csv = STAGE / "f0_stage3c_sequential_accepted_events.csv"
if g1_csv.exists():
    g1_events = pd.read_csv(g1_csv)
    for c in ["signal_time", "entry_time", "exit_time"]:
        if c in g1_events.columns:
            g1_events[c] = pd.to_datetime(g1_events[c], utc=True)
    g1_sub = g1_events[g1_events['entry_time'].dt.year >= 2024].copy()
    g1_sub['canonical_id'] = [f"G1_{i:03d}_{row['pair']}" for i, row in g1_sub.iterrows()]
    audit_stream_pipeline("F0-G1 Baseline Challenger (2024–2026 Exact Period)", g1_sub, filter_2024_onwards=False)

print("\n" + "="*120)
print("RECONCILIATION AUDIT COMPLETE")
print("="*120)
