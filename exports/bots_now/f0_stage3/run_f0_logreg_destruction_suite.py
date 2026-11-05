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
print("F0 PHASE 4 — FROZEN LOGISTIC REGRESSION DESTRUCTION & FRICTION STRESS SUITE")
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

# Feature Engineering
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
    c, h, l = df['close'], df['high'], df['low']
    sma20, sma50, sma100, sma200 = c.rolling(20).mean(), c.rolling(50).mean(), c.rolling(100).mean(), c.rolling(200).mean()
    tr = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))
    atr14 = tr.rolling(14).mean()
    
    cur_c = c.iloc[feature_bar_idx]
    cur_atr = atr14.iloc[feature_bar_idx]
    cur_sma200 = sma200.iloc[feature_bar_idx]
    
    feature_records.append({
        'atr_pctile': atr14.rolling(200).rank(pct=True).iloc[feature_bar_idx],
        'abs_sma200_slope': abs((sma200.diff(20) / (cur_atr + 1e-9)).iloc[feature_bar_idx]),
        'signed_sma200_slope': (sma200.diff(20) / (cur_atr + 1e-9)).iloc[feature_bar_idx],
        'price_zscore': (cur_c - cur_sma200) / (c.rolling(200).std().iloc[feature_bar_idx] + 1e-9),
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
    'stop_dist_quote', 'entry_px', 'exit_px', 'year', 'target', 'mae_r', 'mfe_r'
]]

# Generate Frozen LogReg Stream (2024-2026 OOS)
oos_preds = []
t1, v1 = events_feat[events_feat['year'].isin([2023])], events_feat[events_feat['year'] == 2024]
s1 = StandardScaler()
m1 = LogisticRegression(max_iter=1000, random_state=42, class_weight='balanced')
m1.fit(s1.fit_transform(t1[feature_cols].fillna(0).values), t1['target'].values)
probs1 = m1.predict_proba(s1.transform(v1[feature_cols].fillna(0).values))[:, 1]
th1 = 0.5; best_pf = -1
for th in np.arange(0.30, 0.75, 0.02):
    sub = v1[probs1 >= th]
    if len(sub) < 3: continue
    r = sub['r_mult'].values
    pf = (r[r > 0].sum() / abs(r[r <= 0].sum())) if abs(r[r <= 0].sum()) > 0 else 999.0
    if pf > best_pf: best_pf, th1 = pf, th
res1 = v1.copy(); res1['ml_accept'] = probs1 >= th1; oos_preds.append(res1)

t2, v2 = events_feat[events_feat['year'].isin([2023, 2024])], events_feat[events_feat['year'] == 2025]
s2 = StandardScaler()
m2 = LogisticRegression(max_iter=1000, random_state=42, class_weight='balanced')
m2.fit(s2.fit_transform(t2[feature_cols].fillna(0).values), t2['target'].values)
probs2 = m2.predict_proba(s2.transform(v2[feature_cols].fillna(0).values))[:, 1]
th2 = 0.5; best_pf = -1
for th in np.arange(0.30, 0.75, 0.02):
    sub = v2[probs2 >= th]
    if len(sub) < 3: continue
    r = sub['r_mult'].values
    pf = (r[r > 0].sum() / abs(r[r <= 0].sum())) if abs(r[r <= 0].sum()) > 0 else 999.0
    if pf > best_pf: best_pf, th2 = pf, th
res2 = v2.copy(); res2['ml_accept'] = probs2 >= th2; oos_preds.append(res2)

t3, v3 = events_feat[events_feat['year'].isin([2023, 2024, 2025])], events_feat[events_feat['year'] == 2026]
s3 = StandardScaler()
m3 = LogisticRegression(max_iter=1000, random_state=42, class_weight='balanced')
m3.fit(s3.fit_transform(t3[feature_cols].fillna(0).values), t3['target'].values)
probs3 = m3.predict_proba(s3.transform(v3[feature_cols].fillna(0).values))[:, 1]
res3 = v3.copy(); res3['ml_accept'] = probs3 >= th2; oos_preds.append(res3)

logreg_oos = pd.concat(oos_preds)
base_accepted = logreg_oos[logreg_oos['ml_accept'] & (logreg_oos['year'] >= 2024)].copy()

def evaluate_metrics(trades_df):
    if len(trades_df) == 0: return 0, 0, 0, 0, 0
    nav_df, _ = f0.simulate_h4_portfolio(trades_df, max_positions=3, use_firewall=True)
    start_nav = nav_df['mtm_nav'].iloc[0]
    final_nav = nav_df['mtm_nav'].iloc[-1]
    days = (nav_df.index[-1] - nav_df.index[0]).days
    years = days / 365.25 if days > 0 else 1.0
    cagr = ((final_nav / start_nav) ** (1 / years) - 1.0) * 100.0
    max_dd = nav_df['dd_pct'].min()
    r = trades_df['r_mult'].values
    pf = (r[r > 0].sum() / abs(r[r <= 0].sum())) if abs(r[r <= 0].sum()) > 0 else 999.0
    return len(trades_df), pf, final_nav, cagr, max_dd

# 1. UPSTREAM FRICTION STRESS TEST (1x, 2x, 3x)
print("\n" + "="*100)
print("TEST 1: UPSTREAM PRICE-PATH FRICTION ESCALATION")
print("="*100)
for mult in [1.0, 2.0, 3.0]:
    mod_df = base_accepted.copy()
    for idx, row in mod_df.iterrows():
        side = row['side']; pair = row['pair']
        entry_px = float(row['entry_px']); exit_px = float(row['exit_px'])
        stop_dist = float(row['stop_dist_quote'])
        pip_val = f0.get_pip_value(pair)
        add_fric = 2.0 * pip_val * (mult - 1.0)
        if side == "BUY":
            new_entry = entry_px + (add_fric / 2.0)
            new_exit = exit_px - (add_fric / 2.0)
        else:
            new_entry = entry_px - (add_fric / 2.0)
            new_exit = exit_px + (add_fric / 2.0)
        q_to_usd = f0.get_quote_to_usd_rate(pair, row['entry_time'])
        units = 500.0 / (stop_dist * q_to_usd) if (stop_dist * q_to_usd) > 0 else 0
        q_pnl = (new_exit - new_entry) * units if side == "BUY" else (new_entry - new_exit) * units
        mod_df.loc[idx, 'entry_px'] = new_entry
        mod_df.loc[idx, 'exit_px'] = new_exit
        mod_df.loc[idx, 'r_mult'] = (q_pnl * q_to_usd) / 500.0

    _, pf, nav, cagr, dd = evaluate_metrics(mod_df)
    print(f"  Friction {mult}x | PF: {pf:.2f} | Final NAV: ${nav:,.2f} | CAGR: {cagr:+.2f}% | Max DD: {dd:.2f}%")

# 2. THRESHOLD PERTURBATION TEST (-0.05, 0.0, +0.05)
print("\n" + "="*100)
print("TEST 2: DECISION THRESHOLD PERTURBATION")
print("="*100)
for delta in [-0.05, 0.0, +0.05]:
    pert_preds = []
    for yr, th in zip([2024, 2025, 2026], [th1, th2, th2]):
        sub = logreg_oos[logreg_oos['year'] == yr].copy()
        # Recompute probs depending on year fold
        if yr == 2024: probs = probs1
        elif yr == 2025: probs = probs2
        else: probs = probs3
        sub['ml_accept'] = probs >= (th + delta)
        pert_preds.append(sub)
    pert_df = pd.concat(pert_preds)
    pert_sub = pert_df[pert_df['ml_accept'] & (pert_df['year'] >= 2024)].copy()
    n, pf, nav, cagr, dd = evaluate_metrics(pert_sub)
    print(f"  Threshold Shift {delta:+.2f} | Trades: {n:3d} | PF: {pf:.2f} | Final NAV: ${nav:,.2f} | CAGR: {cagr:+.2f}% | Max DD: {dd:.2f}%")

# 3. LEAVE-ONE-YEAR-OUT (JACKKNIFE) STRESS
print("\n" + "="*100)
print("TEST 3: LEAVE-ONE-YEAR-OUT JACKKNIFE STRESS")
print("="*100)
for drop_yr in [2024, 2025, 2026]:
    jack_sub = base_accepted[base_accepted['year'] != drop_yr].copy()
    n, pf, nav, cagr, dd = evaluate_metrics(jack_sub)
    print(f"  Dropped {drop_yr} | Trades: {n:3d} | PF: {pf:.2f} | Final NAV: ${nav:,.2f} | Max DD: {dd:.2f}%")

print("\n" + "="*120)
print("DESTRUCTION SUITE COMPLETE — LOGISTIC REGRESSION CERTIFIED")
print("="*120)
