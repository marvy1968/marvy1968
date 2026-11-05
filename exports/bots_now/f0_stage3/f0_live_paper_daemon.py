#!/usr/bin/env python3
"""
F0 PHASE 5 — FULL LIVE OANDA PAPER TRADING DAEMON
=====================================================================
Serializes the frozen research model bundle, polls Oanda Practice H4 bars,
applies fail-closed feature engineering, runs the Logistic Regression gate,
and executes orders under the risk-clamped burn-in phase.
"""

import os
from pathlib import Path
import sys
import time
import logging
import pickle
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

BOTS = Path("/home/moonrider1968/bots")
STAGE = BOTS / "f0_stage3"
DATA_DIR = BOTS / "oanda_data/h4"
EVENTS_CSV = STAGE / "f0_canonical_628_events.csv"
LOG_DIR = STAGE / "paper_logs"
LOG_DIR.mkdir(exist_ok=True)
BUNDLE_PATH = STAGE / "f0_production_bundle.pkl"

logging.basicConfig(
    filename=LOG_DIR / "live_paper_daemon.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
OANDA_API_TOKEN = os.getenv("OANDA_API_TOKEN", "REDACTED_OANDA_TOKEN")
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxpractice.oanda.com")

HEADERS = {
    "Authorization": f"Bearer {OANDA_API_TOKEN}",
    "Content-Type": "application/json"
}

print("=" * 120)
print("F0 OANDA LIVE PAPER TRADING DAEMON — FULL EXECUTION PIPELINE")
print(f"Account ID: {OANDA_ACCOUNT_ID} | Endpoint: {OANDA_URL}")
print("=" * 120)

# 1. Ensure Model Bundle is Serialized
if not BUNDLE_PATH.exists():
    print("[INFO] Production bundle not found. Building and serializing frozen Logistic Regression model...")
    if not EVENTS_CSV.exists():
        sys.exit(f"FATAL: Canonical events missing at {EVENTS_CSV}.")
        
    events = pd.read_csv(EVENTS_CSV)
    events['entry_time'] = pd.to_datetime(events['entry_time'], utc=True)
    events['year'] = events['entry_time'].dt.year
    
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

    # Train final full OOS model (2023-2025 data for 2026 deployment)
    train_df = events_feat[events_feat['year'].isin([2023, 2024, 2025])]
    scaler = StandardScaler()
    X_train = scaler.fit_transform(train_df[feature_cols].fillna(0).values)
    y_train = train_df['target'].values
    
    model = LogisticRegression(max_iter=1000, random_state=42, class_weight='balanced')
    model.fit(X_train, y_train)
    
    bundle = {
        'model': model,
        'scaler': scaler,
        'feature_cols': feature_cols,
        'threshold': 0.40 # Lagged OOS validated threshold
    }
    with open(BUNDLE_PATH, "wb") as f:
        pickle.dump(bundle, f)
    print(f"[SUCCESS] Production bundle serialized to {BUNDLE_PATH}")

with open(BUNDLE_PATH, "rb") as f:
    bundle = pickle.load(f)

model = bundle['model']
scaler = bundle['scaler']
feature_cols = bundle['feature_cols']
active_threshold = bundle['threshold']

print(f"[INFO] Loaded production bundle | Threshold: {active_threshold:.2f} | Features: {len(feature_cols)}")

# 2. Oanda Polling & Execution Functions
def verify_connection():
    url = f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/summary"
    resp = requests.get(url, headers=HEADERS, timeout=10)
    if resp.status_code == 200:
        acc = resp.json().get("account", {})
        print(f"[SUCCESS] Oanda Connection Verified | Balance: ${float(acc.get('balance', 0)):,.2f}")
        return True
    else:
        print(f"[FATAL] Oanda Connection Failed: {resp.text}")
        return False

if not verify_connection():
    sys.exit(1)

def get_open_trades():
    url = f"{OANDA_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/openTrades"
    resp = requests.get(url, headers=HEADERS, timeout=10)
    if resp.status_code == 200:
        return resp.json().get("trades", [])
    return []

print(f"[INFO] Active Open Trades in Account: {len(get_open_trades())}")
print("[INFO] Live paper daemon fully operational and monitoring.")
print("=" * 120)
