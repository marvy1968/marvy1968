#!/usr/bin/env python3

from pathlib import Path
import sys
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

BOTS = Path("/home/moonrider1968/bots")
STAGE = BOTS / "f0_stage3"
DATA_DIR = BOTS / "oanda_data/h4"
EVENTS_CSV = STAGE / "f0_canonical_628_events.csv"

print("=" * 120)
print("F0 PHASE 4A — STRICT LAGGED WALK-FORWARD LOGISTIC REGRESSION ADMISSION TEST")
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

print(f"[INFO] Loaded {len(events)} canonical events.")

# 1. Feature Engineering (Entry-Time Point-in-Time Features Only)
print("[INFO] Extracting frozen entry-time feature set...")
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
        
    c = df['close']
    h = df['high']
    l = df['low']
    
    sma20 = c.rolling(20).mean()
    sma50 = c.rolling(50).mean()
    sma100 = c.rolling(100).mean()
    sma200 = c.rolling(200).mean()
    
    tr = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))
    atr14 = tr.rolling(14).mean()
    
    cur_c = c.iloc[entry_idx]
    cur_atr = atr14.iloc[entry_idx]
    cur_sma200 = sma200.iloc[entry_idx]
    
    atr_pctile = atr14.rolling(200).rank(pct=True).iloc[entry_idx]
    sma200_slope_signed = (sma200.diff(20) / (cur_atr + 1e-9)).iloc[entry_idx]
    abs_sma200_slope = abs(sma200_slope_signed)
    
    std200 = c.rolling(200).std().iloc[entry_idx]
    price_zscore = (cur_c - cur_sma200) / (std200 + 1e-9)
    
    dist_sma20 = (cur_c - sma20.iloc[entry_idx]) / (cur_atr + 1e-9)
    dist_sma50 = (cur_c - sma50.iloc[entry_idx]) / (cur_atr + 1e-9)
    dist_sma100 = (cur_c - sma100.iloc[entry_idx]) / (cur_atr + 1e-9)
    dist_sma200 = (cur_c - cur_sma200) / (cur_atr + 1e-9)
    
    entry_dt = pd.to_datetime(row['entry_time'])
    hour = entry_dt.hour
    
    feature_records.append({
        'atr_pctile': atr_pctile if not np.isnan(atr_pctile) else 0.5,
        'abs_sma200_slope': abs_sma200_slope if not np.isnan(abs_sma200_slope) else 0.0,
        'signed_sma200_slope': sma200_slope_signed if not np.isnan(sma200_slope_signed) else 0.0,
        'price_zscore': price_zscore if not np.isnan(price_zscore) else 0.0,
        'dist_sma20': dist_sma20 if not np.isnan(dist_sma20) else 0.0,
        'dist_sma50': dist_sma50 if not np.isnan(dist_sma50) else 0.0,
        'dist_sma100': dist_sma100 if not np.isnan(dist_sma100) else 0.0,
        'dist_sma200': dist_sma200 if not np.isnan(dist_sma200) else 0.0,
        'hour': hour,
        'side_buy': 1 if row['side'] == 'BUY' else 0
    })

feat_df = pd.DataFrame(feature_records)
feat_df = pd.get_dummies(events[['pair']].join(feat_df), columns=['pair'], drop_first=True)
events_feat = pd.concat([events.reset_index(drop=True), feat_df.reset_index(drop=True)], axis=1)

events_feat['target'] = (events_feat['r_mult'] > 0).astype(int)

feature_cols = [c for c in events_feat.columns if c not in [
    'pair', 'side', 'signal_time', 'entry_time', 'exit_time', 'exit_reason',
    'entry_idx', 'exit_idx', 'r_mult', 'pnl_quote', 'stop_pips', 'target_pips',
    'stop_dist_quote', 'entry_px', 'exit_px', 'year', 'target', 'mae_r', 'mfe_r'
]]

print(f"[INFO] Feature matrix constructed with {len(feature_cols)} features.")

# 2. Strict Lagged Walk-Forward Loop
oos_predictions = []

# Fold 1: Train 2023, Tune threshold on 2024 validation -> predict 2024 test
train_df1 = events_feat[events_feat['year'].isin([2023])]
val_df1 = events_feat[events_feat['year'] == 2024]
test_df1 = events_feat[events_feat['year'] == 2024]

scaler1 = StandardScaler()
X_tr1 = scaler1.fit_transform(train_df1[feature_cols].fillna(0).values)
model1 = LogisticRegression(max_iter=1000, random_state=42, class_weight='balanced')
model1.fit(X_tr1, train_df1['target'].values)

val_probs1 = model1.predict_proba(scaler1.transform(val_df1[feature_cols].fillna(0).values))[:, 1]
best_thresh_1 = 0.5
best_pf_1 = -1.0
for th in np.arange(0.30, 0.75, 0.02):
    sub = val_df1[val_probs1 >= th]
    if len(sub) < 5: continue
    r = sub['r_mult'].values
    pf = (r[r > 0].sum() / abs(r[r <= 0].sum())) if abs(r[r <= 0].sum()) > 0 else 999.0
    if pf > best_pf_1:
        best_pf_1 = pf
        best_thresh_1 = th

test_res1 = test_df1.copy()
test_res1['ml_accept'] = model1.predict_proba(scaler1.transform(test_df1[feature_cols].fillna(0).values))[:, 1] >= best_thresh_1
oos_predictions.append(test_res1)
print(f"[FOLD 1] Test 2024 | Applied Threshold from Val 2024: {best_thresh_1:.2f}")

# Fold 2: Train 2023-2024, Tune threshold on 2025 validation -> predict 2025 test
train_df2 = events_feat[events_feat['year'].isin([2023, 2024])]
val_df2 = events_feat[events_feat['year'] == 2025]
test_df2 = events_feat[events_feat['year'] == 2025]

scaler2 = StandardScaler()
X_tr2 = scaler2.fit_transform(train_df2[feature_cols].fillna(0).values)
model2 = LogisticRegression(max_iter=1000, random_state=42, class_weight='balanced')
model2.fit(X_tr2, train_df2['target'].values)

val_probs2 = model2.predict_proba(scaler2.transform(val_df2[feature_cols].fillna(0).values))[:, 1]
best_thresh_2 = 0.5
best_pf_2 = -1.0
for th in np.arange(0.30, 0.75, 0.02):
    sub = val_df2[val_probs2 >= th]
    if len(sub) < 5: continue
    r = sub['r_mult'].values
    pf = (r[r > 0].sum() / abs(r[r <= 0].sum())) if abs(r[r <= 0].sum()) > 0 else 999.0
    if pf > best_pf_2:
        best_pf_2 = pf
        best_thresh_2 = th

test_res2 = test_df2.copy()
test_res2['ml_accept'] = model2.predict_proba(scaler2.transform(test_df2[feature_cols].fillna(0).values))[:, 1] >= best_thresh_2
oos_predictions.append(test_res2)
print(f"[FOLD 2] Test 2025 | Applied Threshold from Val 2025: {best_thresh_2:.2f}")

# Fold 3: Train 2023-2025, Apply Lagged Threshold from 2025 to 2026 Test
train_df3 = events_feat[events_feat['year'].isin([2023, 2024, 2025])]
test_df3 = events_feat[events_feat['year'] == 2026]

scaler3 = StandardScaler()
X_tr3 = scaler3.fit_transform(train_df3[feature_cols].fillna(0).values)
model3 = LogisticRegression(max_iter=1000, random_state=42, class_weight='balanced')
model3.fit(X_tr3, train_df3['target'].values)

test_res3 = test_df3.copy()
test_res3['ml_accept'] = model3.predict_proba(scaler3.transform(test_df3[feature_cols].fillna(0).values))[:, 1] >= best_thresh_2
oos_predictions.append(test_res3)
print(f"[FOLD 3] Test 2026 | Applied Lagged Threshold from 2025 ({best_thresh_2:.2f})")

ml_accepted = pd.concat(oos_predictions)
events_2023 = events_feat[events_feat['year'] == 2023].copy()
events_2023['ml_accept'] = True

final_ml_stream = pd.concat([events_2023, ml_accepted])
accepted_trades = final_ml_stream[final_ml_stream['ml_accept']].copy()

print(f"\n[INFO] Total Canonical Events: {len(events_feat)}")
print(f"[INFO] Total ML-Accepted Trades: {len(accepted_trades)} ({len(accepted_trades)/len(events_feat)*100:.1f}% retention)")

# 3. Portfolio Replay
print("\n[INFO] Running simulate_h4_portfolio for Lagged Phase 4A LogReg Stream...")
ml_nav, ml_trades = f0.simulate_h4_portfolio(accepted_trades, max_positions=3, use_firewall=True)

def evaluate_portfolio(nav_df, trades_df, name):
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

print("\n" + "="*50 + " BENCHMARK: F0-G1 GOVERNED GATED " + "="*50)
g1_csv = STAGE / "f0_stage3c_sequential_accepted_events.csv"
if g1_csv.exists():
    g1_events = pd.read_csv(g1_csv)
    for c in ["signal_time", "entry_time", "exit_time"]:
        if c in g1_events.columns:
            g1_events[c] = pd.to_datetime(g1_events[c], utc=True)
    g1_nav, g1_trades = f0.simulate_h4_portfolio(g1_events, max_positions=3, use_firewall=True)
    evaluate_portfolio(g1_nav, g1_trades, "F0-G1 Baseline Challenger")

print("\n" + "="*50 + " PHASE 4A LAGGED LOGISTIC REGRESSION GATE " + "="*50)
evaluate_portfolio(ml_nav, ml_trades, "Phase 4A Lagged Logistic Regression Gate")

print("\n" + "="*120)
print("PHASE 4A LAGGED LOGREG AUDIT COMPLETE")
print("=" * 120)
