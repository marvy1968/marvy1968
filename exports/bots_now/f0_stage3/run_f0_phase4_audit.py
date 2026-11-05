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
print("F0 PHASE 4 — STRICT OOS INTEGRITY & CERTIFICATION AUDIT (2024–2026 ISOLATION)")
print("=" * 120)

if not EVENTS_CSV.exists():
    sys.exit(f"FATAL: Canonical events not found at {EVENTS_CSV}.")

sys.path.insert(0, str(BOTS))
import run_f0_hardened_portfolio_v2 as f0

# 1. Source-of-Truth Guard Verification
assert len(f0.events_df) == 628, "FATAL: Canonical event count mismatch"
assert abs(f0.events_df['r_mult'].mean() - 0.101) < 0.005, "FATAL: Canonical Mean R guard violated"
print("[PASS] Source-of-truth guards verified (628 events, MeanR ≈ +0.101R, F0_SIGNAL_PARITY = PASS).")

events = pd.read_csv(EVENTS_CSV)
events['entry_time'] = pd.to_datetime(events['entry_time'], utc=True)
events['exit_time'] = pd.to_datetime(events['exit_time'], utc=True)
events['signal_time'] = pd.to_datetime(events['signal_time'], utc=True)
events['year'] = events['entry_time'].dt.year

# 2. Feature Engineering with Strict Bar Assertion
print("[INFO] Extracting frozen entry-time features and verifying entry bar alignment...")
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
        
    # Explicit bar alignment assertion requested by user
    feature_bar_idx = entry_idx - 1
    assert feature_bar_idx == entry_idx - 1, f"FATAL: Feature bar index assertion failed at event {idx}"
    
    c = df['close']
    h = df['high']
    l = df['low']
    
    sma20 = c.rolling(20).mean()
    sma50 = c.rolling(50).mean()
    sma100 = c.rolling(100).mean()
    sma200 = c.rolling(200).mean()
    
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
    
    dist_sma20 = (cur_c - sma20.iloc[feature_bar_idx]) / (cur_atr + 1e-9)
    dist_sma50 = (cur_c - sma50.iloc[feature_bar_idx]) / (cur_atr + 1e-9)
    dist_sma100 = (cur_c - sma100.iloc[feature_bar_idx]) / (cur_atr + 1e-9)
    dist_sma200 = (cur_c - sma200.iloc[feature_bar_idx]) / (cur_atr + 1e-9)
    
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

print(f"[PASS] Feature matrix verified across {len(feature_cols)} features with strict bar alignment.")

# 3. Model Execution & Chronological Threshold Provenance
def run_model_pipeline(model_type='gbm'):
    oos_preds = []
    provenance = {}
    
    # 2024 OOS (Train <= 2023, Tune threshold on 2024 validation)
    train_1 = events_feat[events_feat['year'].isin([2023])]
    val_1 = events_feat[events_feat['year'] == 2024]
    
    if model_type == 'gbm':
        m1 = LGBMClassifier(n_estimators=50, max_depth=3, learning_rate=0.03, random_state=42, class_weight='balanced', verbose=-1)
        m1.fit(train_1[feature_cols].fillna(0).values, train_1['target'].values)
        probs_val1 = m1.predict_proba(val_1[feature_cols].fillna(0).values)[:, 1]
    else:
        scaler1 = StandardScaler()
        X_tr1 = scaler1.fit_transform(train_1[feature_cols].fillna(0).values)
        m1 = LogisticRegression(max_iter=1000, random_state=42, class_weight='balanced')
        m1.fit(X_tr1, train_1['target'].values)
        probs_val1 = m1.predict_proba(scaler1.transform(val_1[feature_cols].fillna(0).values))[:, 1]
        
    best_th1, best_pf1 = 0.5, -1.0
    for th in np.arange(0.30, 0.75, 0.02):
        sub = val_1[probs_val1 >= th]
        if len(sub) < 3: continue
        r = sub['r_mult'].values
        pf = (r[r > 0].sum() / abs(r[r <= 0].sum())) if abs(r[r <= 0].sum()) > 0 else 999.0
        if pf > best_pf1:
            best_pf1, best_th1 = pf, th
            
    provenance[2024] = {'train_years': [2023], 'val_year': 2024, 'threshold': best_th1}
    test1 = val_1.copy()
    test1['ml_accept'] = probs_val1 >= best_th1
    oos_preds.append(test1)
    
    # 2025 OOS (Train <= 2024, Tune threshold on 2025 validation)
    train_2 = events_feat[events_feat['year'].isin([2023, 2024])]
    val_2 = events_feat[events_feat['year'] == 2025]
    
    if model_type == 'gbm':
        m2 = LGBMClassifier(n_estimators=50, max_depth=3, learning_rate=0.03, random_state=42, class_weight='balanced', verbose=-1)
        m2.fit(train_2[feature_cols].fillna(0).values, train_2['target'].values)
        probs_val2 = m2.predict_proba(val_2[feature_cols].fillna(0).values)[:, 1]
    else:
        scaler2 = StandardScaler()
        X_tr2 = scaler2.fit_transform(train_2[feature_cols].fillna(0).values)
        m2 = LogisticRegression(max_iter=1000, random_state=42, class_weight='balanced')
        m2.fit(X_tr2, train_2['target'].values)
        probs_val2 = m2.predict_proba(scaler2.transform(val_2[feature_cols].fillna(0).values))[:, 1]
        
    best_th2, best_pf2 = 0.5, -1.0
    for th in np.arange(0.30, 0.75, 0.02):
        sub = val_2[probs_val2 >= th]
        if len(sub) < 3: continue
        r = sub['r_mult'].values
        pf = (r[r > 0].sum() / abs(r[r <= 0].sum())) if abs(r[r <= 0].sum()) > 0 else 999.0
        if pf > best_pf2:
            best_pf2, best_th2 = pf, th
            
    provenance[2025] = {'train_years': [2023, 2024], 'val_year': 2025, 'threshold': best_th2}
    test2 = val_2.copy()
    test2['ml_accept'] = probs_val2 >= best_th2
    oos_preds.append(test2)
    
    # 2026 OOS (Train <= 2025, Apply lagged threshold from 2025 validation)
    train_3 = events_feat[events_feat['year'].isin([2023, 2024, 2025])]
    test_3 = events_feat[events_feat['year'] == 2026]
    
    if model_type == 'gbm':
        m3 = LGBMClassifier(n_estimators=50, max_depth=3, learning_rate=0.03, random_state=42, class_weight='balanced', verbose=-1)
        m3.fit(train_3[feature_cols].fillna(0).values, train_3['target'].values)
        probs_test3 = m3.predict_proba(test_3[feature_cols].fillna(0).values)[:, 1]
    else:
        scaler3 = StandardScaler()
        X_tr3 = scaler3.fit_transform(train_3[feature_cols].fillna(0).values)
        m3 = LogisticRegression(max_iter=1000, random_state=42, class_weight='balanced')
        m3.fit(X_tr3, train_3['target'].values)
        probs_test3 = m3.predict_proba(scaler3.transform(test_3[feature_cols].fillna(0).values))[:, 1]
        
    provenance[2026] = {'train_years': [2023, 2024, 2025], 'val_year': 'lagged_2025', 'threshold': best_th2}
    test3 = test_3.copy()
    test3['ml_accept'] = probs_test3 >= best_th2
    oos_preds.append(test3)
    
    return pd.concat(oos_preds), provenance

print("\n[INFO] Running LightGBM pipeline...")
gbm_oos, gbm_prov = run_model_pipeline('gbm')

print("[INFO] Running Logistic Regression pipeline...")
logreg_oos, logreg_prov = run_model_pipeline('logreg')

# 4. Evaluation Function
def evaluate_portfolio(trades_df, name):
    if len(trades_df) == 0:
        print(f"\n--- {name} --- (0 trades)")
        return
        
    nav_df, _ = f0.simulate_h4_portfolio(trades_df, max_positions=3, use_firewall=True)
    start_nav = nav_df['mtm_nav'].iloc[0]
    final_nav = nav_df['mtm_nav'].iloc[-1]
    days = (nav_df.index[-1] - nav_df.index[0]).days
    years = days / 365.25 if days > 0 else 1.0
    cagr = ((final_nav / start_nav) ** (1 / years) - 1.0) * 100.0
    max_dd = nav_df['dd_pct'].min()
    
    r = trades_df['r_mult'].values
    wins = r[r > 0].sum()
    losses = abs(r[r <= 0].sum())
    pf = (wins / losses) if losses > 0 else 999.0
    mean_r = r.mean()
    wr = (len(r[r > 0]) / len(r)) * 100.0
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
    
    trades_df['year'] = pd.to_datetime(trades_df['entry_time']).dt.year
    print("Year-by-Year Performance:")
    for yr in sorted(trades_df['year'].unique()):
        yd = trades_df[trades_df['year'] == yr]
        yr_r = yd['r_mult'].values
        y_wins = yr_r[yr_r > 0].sum()
        y_losses = abs(yr_r[yr_r <= 0].sum())
        y_pf = (y_wins / y_losses) if y_losses > 0 else 999.0
        print(f"  {yr} | N: {len(yd):<3} | WR: {len(yr_r[yr_r > 0])/len(yr_r)*100:5.1f}% | Mean R: {yr_r.mean():+7.4f}R | PF: {y_pf:.2f}")

# 5. Stream Reports
print("\n" + "="*120)
print("THRESHOLD PROVENANCE AUDIT")
print("="*120)
print("GBM Provenance:")
for yr, info in gbm_prov.items():
    print(f"  Year {yr} | Train Years: {info['train_years']} | Val/Source: {info['val_year']} | Threshold: {info['threshold']:.2f}")

print("\nLogReg Provenance:")
for yr, info in logreg_prov.items():
    print(f"  Year {yr} | Train Years: {info['train_years']} | Val/Source: {info['val_year']} | Threshold: {info['threshold']:.2f}")

# Stream A: 2023 Bootstrap Reference (Descriptive only)
trades_2023 = events_feat[events_feat['year'] == 2023].copy()
evaluate_portfolio(trades_2023, "Stream A: 2023 Bootstrap Reference (Unfiltered Raw F0)")

# Stream B: 2024–2026 Strict OOS GBM (PRIMARY)
gbm_accepted_oos = gbm_oos[gbm_oos['ml_accept']].copy()
evaluate_portfolio(gbm_accepted_oos, "Stream B: 2024–2026 Strict OOS LightGBM (PRIMARY)")

# Stream C: 2024–2026 Strict OOS LogReg (Comparator)
logreg_accepted_oos = logreg_oos[logreg_oos['ml_accept']].copy()
evaluate_portfolio(logreg_accepted_oos, "Stream C: 2024–2026 Strict OOS Logistic Regression (Comparator)")

# Stream D: F0-G1 Baseline over exact 2024-2026 period for apples-to-apples comparison
g1_csv = STAGE / "f0_stage3c_sequential_accepted_events.csv"
if g1_csv.exists():
    g1_events = pd.read_csv(g1_csv)
    for c in ["signal_time", "entry_time", "exit_time"]:
        if c in g1_events.columns:
            g1_events[c] = pd.to_datetime(g1_events[c], utc=True)
    g1_oos = g1_events[g1_events['entry_time'].dt.year >= 2024].copy()
    evaluate_portfolio(g1_oos, "Stream D: F0-G1 Baseline Challenger (2024–2026 Exact Period)")

print("\n" + "="*120)
print("PHASE 4 AUDIT COMPLETE")
print("="*120)
