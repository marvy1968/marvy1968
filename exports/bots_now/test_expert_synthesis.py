#!/usr/bin/env python3
import numpy as np
import pandas as pd
import glob
import os

def load_universe():
    files = glob.glob("/home/moonrider1968/bots/data/*.csv") + glob.glob("/home/moonrider1968/bots/data/*.parquet")
    dfs = {}
    for f in files:
        sym = os.path.basename(f).split('.')[0].replace('_daily', '').replace('_H1', '').upper()
        try:
            if f.endswith('.csv'):
                df = pd.read_csv(f)
            else:
                df = pd.read_parquet(f)
            df.columns = [c.lower() for c in df.columns]
            if 'date' in df.columns:
                df['time'] = pd.to_datetime(df['date'])
            elif 'time' in df.columns:
                df['time'] = pd.to_datetime(df['time'])
            df = df.sort_values('time').reset_index(drop=True)
            if len(df) >= 250:
                dfs[sym] = df
        except Exception:
            pass
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
    
    # Typical Price & VWAP Rolling Proxy (20-bar rolling VWAP)
    vol = df['volume'] if 'volume' in df.columns else pd.Series(1, index=df.index)
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    df['vwap'] = (tp * vol).rolling(20).sum() / (vol.rolling(20).sum() + 1e-9)
    
    # Swing Highs / Lows
    df['swing_high'] = df['high'].rolling(10).max()
    df['swing_low'] = df['low'].rolling(10).min()
    
    # Velez Elephant Bar Check
    candle_range = df['high'] - df['low'] + 1e-9
    body_range = (df['close'] - df['open']).abs()
    is_green = df['close'] > df['open']
    close_top = (df['close'] - df['low']) / candle_range >= 0.70
    df['elephant_bar'] = is_green & (body_range / candle_range >= 0.60) & close_top & (candle_range > df['atr'] * 0.8)
    
    return df.dropna().reset_index(drop=True)

def simulate(df, mode="EXPERT_SYNTHESIS"):
    trades = []
    pos = None
    
    for i in range(205, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        
        # 1. Manage Open Position
        if pos is not None:
            pos['bars'] += 1
            high, low, close = row['high'], row['low'], row['close']
            entry = pos['avg_entry']
            r_dist = pos['r_dist']
            mfe_r = (high - pos['entry_1']) / r_dist
            
            # --- MODEL B: OLD ZONE MODEL (Blind Add + Early Trim) ---
            if mode == "OLD_ZONE":
                # Blind Add inside 20-50 zone
                if not pos['added'] and (low <= row['sma20']) and (close >= row['sma50']):
                    pos['units'] += 0.5
                    pos['avg_entry'] = (pos['avg_entry'] * 1.0 + close * 0.5) / 1.5
                    pos['added'] = True
                
                # Trim at +2.0R
                if not pos['trimmed'] and mfe_r >= 2.0:
                    pos['realized_r'] += 0.5 * 2.0
                    pos['units'] -= 0.5
                    pos['trimmed'] = True
                    pos['stop_px'] = pos['entry_1']
                
                # Stop check
                if low <= pos['stop_px'] or pos['bars'] >= 45:
                    exit_px = pos['stop_px'] if low <= pos['stop_px'] else close
                    rem_r = ((exit_px - pos['avg_entry']) / r_dist) * pos['units']
                    trades.append(pos['realized_r'] + rem_r)
                    pos = None
                    continue

            # --- MODEL C: EXPERT SYNTHESIS (Livermore Pyramid + Velez Ignition) ---
            elif mode == "EXPERT_SYNTHESIS":
                # Step 1: Unit 1 achieves +1.0R -> Move stop to Break-Even (Risk-Free Base)
                if pos['units'] == 1.0 and mfe_r >= 1.0:
                    pos['stop_px'] = max(pos['stop_px'], pos['entry_1'])
                
                # Step 2: Add +0.5R ONLY if Unit 1 stop is at Break-Even AND Velez Ignition Bar fires off 20 SMA / VWAP
                if pos['units'] == 1.0 and pos['stop_px'] >= pos['entry_1']:
                    pulled_back = (prev['low'] <= row['ema20'] or prev['low'] <= row['vwap'])
                    ignition = row['elephant_bar'] and (row['close'] > row['ema20']) and (row['close'] > row['vwap'])
                    
                    if pulled_back and ignition:
                        add_entry = close
                        # Recalculate average fill
                        pos['avg_entry'] = (pos['entry_1'] * 1.0 + add_entry * 0.5) / 1.5
                        pos['units'] = 1.5
                        pos['added'] = True
                        # Add stop placed under recent pullback low
                        pos['stop_px'] = max(pos['entry_1'], row['low'] - (0.5 * row['atr']))
                
                # Step 3: Trailing Exit (Minervini) - Trail 20 EMA or Swing Low
                if mfe_r >= 1.5:
                    trail_line = max(row['ema20'] - (0.5 * row['atr']), prev['swing_low'])
                    pos['stop_px'] = max(pos['stop_px'], trail_line)
                
                # Exit evaluation
                if low <= pos['stop_px'] or (close < row['sma50'] and pos['bars'] >= 20):
                    exit_px = pos['stop_px'] if low <= pos['stop_px'] else close
                    pnl_r = ((exit_px - pos['avg_entry']) / r_dist) * pos['units']
                    trades.append(pnl_r)
                    pos = None
                    continue

            # --- MODEL A: BASELINE SINGLE-SHOT (Fixed 1.0R, No Adds) ---
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

        # 2. Check Weinstein Stage 2 Breakout Entry
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
        print(f"{name:<28} | Trades: 0")
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
    print(f"{name:<28} | N: {len(t):<4} | WR: {wr:4.1f}% | Net: {tot_r:+7.2f}R | Mean: {mean_r:+5.2f}R | MaxDD: {dd:5.2f}R | PF: {pf:4.2f}")

if __name__ == "__main__":
    dfs = load_universe()
    if not dfs:
        # Generate representative sample if local directory empty
        print("No local CSVs found. Generating synthetic walk-forward asset paths...")
        np.random.seed(42)
        dates = pd.date_range("2021-01-01", "2026-08-30", freq="B")
        for sym in ["MOMENTUM_RUNNER", "TREND_PULLBACK"]:
            drift = 0.0006 if sym == "MOMENTUM_RUNNER" else 0.0002
            px = 100.0 * np.cumprod(1 + np.random.normal(drift, 0.015, len(dates)))
            df = pd.DataFrame({
                "time": dates,
                "open": px * (1 + np.random.normal(0, 0.002, len(dates))),
                "high": px * (1 + np.random.uniform(0.002, 0.01, len(dates))),
                "low": px * (1 - np.random.uniform(0.002, 0.01, len(dates))),
                "close": px,
                "volume": np.random.randint(500000, 5000000, len(dates))
            })
            dfs[sym] = df

    print("=" * 88)
    print("        EXPERT COMPOSITE PYRAMID vs OLD ZONE vs BASELINE SINGLE-SHOT")
    print("=" * 88)
    
    all_trades_base = []
    all_trades_old = []
    all_trades_expert = []
    
    for sym, df in dfs.items():
        df_ind = compute_indicators(df)
        all_trades_base.extend(simulate(df_ind, "BASELINE"))
        all_trades_old.extend(simulate(df_ind, "OLD_ZONE"))
        all_trades_expert.extend(simulate(df_ind, "EXPERT_SYNTHESIS"))
        
    print_stats("1. Baseline (Single-Shot 1R)", all_trades_base)
    print_stats("2. Old Zone (+0.5R Blind/Trim)", all_trades_old)
    print_stats("3. Expert Synthesis (Pyramid)", all_trades_expert)
    print("=" * 88)
