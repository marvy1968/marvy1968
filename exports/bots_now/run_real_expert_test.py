#!/usr/bin/env python3
import os
import glob
from pathlib import Path
import numpy as np
import pandas as pd

DATA_DIR = Path("/home/moonrider1968/bots/data/elite20_v31")

def load_data():
    dfs = {}
    csv_files = glob.glob(str(DATA_DIR / "*.csv"))
    for f in csv_files:
        sym = Path(f).stem.upper()
        try:
            df = pd.read_csv(f)
            df.columns = [c.lower() for c in df.columns]
            if 'date' in df.columns:
                df['time'] = pd.to_datetime(df['date'])
            elif 'time' in df.columns:
                df['time'] = pd.to_datetime(df['time'])
            df = df.sort_values('time').reset_index(drop=True)
            if len(df) >= 250:
                dfs[sym] = df
        except Exception as e:
            print(f"Error loading {f}: {e}")
    return dfs

def compute_indicators(df):
    df['sma20'] = df['close'].rolling(20).mean()
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['sma50'] = df['close'].rolling(50).mean()
    df['sma200'] = df['close'].rolling(200).mean()
    df['sma200_slope'] = df['sma200'] - df['sma200'].shift(20)
    
    # ATR
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift(1)).abs()
    tr3 = (df['low'] - df['close'].shift(1)).abs()
    df['atr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean()
    
    # Rolling Volume-Weighted Proxy
    vol = df['volume'] if 'volume' in df.columns else pd.Series(1, index=df.index)
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    df['vwap'] = (tp * vol).rolling(20).sum() / (vol.rolling(20).sum() + 1e-9)
    
    # Swings
    df['swing_high'] = df['high'].rolling(10).max()
    df['swing_low'] = df['low'].rolling(10).min()
    
    # Velez Elephant / Ignition Bar
    candle_range = df['high'] - df['low'] + 1e-9
    body_range = (df['close'] - df['open']).abs()
    is_green = df['close'] > df['open']
    close_top = (df['close'] - df['low']) / candle_range >= 0.70
    df['elephant_bar'] = is_green & (body_range / candle_range >= 0.60) & close_top & (candle_range > df['atr'] * 0.8)
    
    return df.dropna().reset_index(drop=True)

def simulate(df, mode="EXPERT"):
    trades = []
    pos = None
    
    for i in range(205, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        
        # 1. Manage Active Position
        if pos is not None:
            pos['bars'] += 1
            high, low, close = row['high'], row['low'], row['close']
            r_dist = pos['r_dist']
            mfe_r = (high - pos['entry_1']) / r_dist
            
            # --- MODEL 2: OLD ZONE (Blind Add + Early Trim) ---
            if mode == "OLD_ZONE":
                if not pos['added'] and (low <= row['sma20']) and (close >= row['sma50']):
                    pos['units'] += 0.5
                    pos['avg_entry'] = (pos['avg_entry'] * 1.0 + close * 0.5) / 1.5
                    pos['added'] = True
                
                if not pos['trimmed'] and mfe_r >= 2.0:
                    pos['realized_r'] += 0.5 * 2.0
                    pos['units'] -= 0.5
                    pos['trimmed'] = True
                    pos['stop_px'] = pos['entry_1']
                
                if low <= pos['stop_px'] or pos['bars'] >= 45:
                    exit_px = pos['stop_px'] if low <= pos['stop_px'] else close
                    rem_r = ((exit_px - pos['avg_entry']) / r_dist) * pos['units']
                    trades.append(pos['realized_r'] + rem_r)
                    pos = None
                    continue

            # --- MODEL 3: EXPERT SYNTHESIS (Livermore Pyramid + Velez Ignition) ---
            elif mode == "EXPERT":
                # Move Stop to Break-Even once Unit 1 reaches +1.0R
                if pos['units'] == 1.0 and mfe_r >= 1.0:
                    pos['stop_px'] = max(pos['stop_px'], pos['entry_1'])
                
                # Add +0.5R ONLY if Unit 1 is at BE and Velez Ignition bar reclaims EMA20/VWAP
                if pos['units'] == 1.0 and pos['stop_px'] >= pos['entry_1']:
                    pulled_back = (prev['low'] <= row['ema20'] or prev['low'] <= row['vwap'])
                    ignition = row['elephant_bar'] and (row['close'] > row['ema20']) and (row['close'] > row['vwap'])
                    
                    if pulled_back and ignition:
                        pos['avg_entry'] = (pos['entry_1'] * 1.0 + close * 0.5) / 1.5
                        pos['units'] = 1.5
                        pos['added'] = True
                        pos['stop_px'] = max(pos['entry_1'], row['low'] - (0.5 * row['atr']))
                
                # Progressive Ratchet (Minervini)
                if mfe_r >= 1.5:
                    trail = max(row['ema20'] - (0.5 * row['atr']), prev['swing_low'])
                    pos['stop_px'] = max(pos['stop_px'], trail)
                
                if low <= pos['stop_px'] or (close < row['sma50'] and pos['bars'] >= 20):
                    exit_px = pos['stop_px'] if low <= pos['stop_px'] else close
                    pnl_r = ((exit_px - pos['avg_entry']) / r_dist) * pos['units']
                    trades.append(pnl_r)
                    pos = None
                    continue

            # --- MODEL 1: BASELINE (Single-Shot 1.0R, No Adds/Trims) ---
            elif mode == "BASELINE":
                if mfe_r >= 1.0:
                    pos['stop_px'] = max(pos['stop_px'], pos['entry_1'])
                if mfe_r >= 1.5:
                    pos['stop_px'] = max(pos['stop_px'], row['ema20'] - (0.5 * row['atr']))
                if low <= pos['stop_px'] or pos['bars'] >= 45:
                    exit_px = pos['stop_px'] if low <= pos['stop_px'] else close
                    trades.append((exit_px - pos['entry_1']) / r_dist)
                    pos = None
                    continue

        # 2. Entry Signal (Weinstein Stage 2 Breakout)
        if pos is None:
            stage_2 = (row['close'] > row['sma200']) and (row['sma200_slope'] > 0)
            ma_aligned = row['sma20'] > row['sma50']
            breakout = (row['close'] > prev['swing_high']) and (row['close'] > row['vwap'])
            
            if stage_2 and ma_aligned and breakout:
                r_dist = max(row['atr'] * 1.2, row['close'] * 0.015)
                pos = {
                    'entry_1': row['close'],
                    'avg_entry': row['close'],
                    'stop_px': row['close'] - r_dist,
                    'r_dist': r_dist,
                    'units': 1.0,
                    'realized_r': 0.0,
                    'added': False,
                    'trimmed': False,
                    'bars': 0
                }
                
    return trades

def print_stats(name, trades):
    if not trades:
        print(f"{name:<34} | Trades: 0")
        return
    t = np.array(trades)
    wins = t[t > 0]
    wr = len(wins) / len(t) * 100.0
    tot_r = np.sum(t)
    mean_r = np.mean(t)
    cum = np.cumsum(t)
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak).min()
    pf = wins.sum() / (abs(t[t <= 0].sum()) + 1e-9)
    print(f"{name:<34} | N: {len(t):<4} | WR: {wr:4.1f}% | Net: {tot_r:+7.2f}R | Mean: {mean_r:+5.2f}R | MaxDD: {dd:5.2f}R | PF: {pf:4.2f}")

if __name__ == "__main__":
    dfs = load_data()
    print("=" * 96)
    print(f"   REAL DATASET EVALUATION: {len(dfs)} SYMBOLS IN elite20_v31")
    print("=" * 96)
    
    all_base, all_old, all_expert = [], [], []
    for sym, df in dfs.items():
        df_ind = compute_indicators(df)
        all_base.extend(simulate(df_ind, "BASELINE"))
        all_old.extend(simulate(df_ind, "OLD_ZONE"))
        all_expert.extend(simulate(df_ind, "EXPERT"))
        
    print_stats("1. Baseline Single-Shot (1.0R)", all_base)
    print_stats("2. Old Zone Model (+0.5R/-0.5R)", all_old)
    print_stats("3. Expert Synthesis (Pyramid)", all_expert)
    print("=" * 96)
