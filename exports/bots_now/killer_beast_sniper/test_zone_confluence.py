#!/usr/bin/env python3
import os
import requests
import numpy as np
import pandas as pd

OANDA_API_KEY = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxtrade.oanda.com")

if not OANDA_API_KEY:
    try:
        with open("oanda_stage4_daemon.py.bak_pre_m3_flat", "r") as f:
            for line in f:
                if line.startswith("OANDA_API_KEY") and "=" in line:
                    OANDA_API_KEY = line.split("=")[1].strip().strip('"').strip("'")
                elif line.startswith("OANDA_ACCOUNT_ID") and "=" in line:
                    OANDA_ACCOUNT_ID = line.split("=")[1].strip().strip('"').strip("'")
    except Exception:
        pass

HEADERS = {"Authorization": f"Bearer {OANDA_API_KEY}", "Content-Type": "application/json"}
PAIRS = ["USD_JPY", "USD_CAD"]
SPREAD_PIPS = {"USD_JPY": 1.5, "USD_CAD": 1.8}

def fetch_data(pair, count=2000):
    url = f"{OANDA_URL}/v3/instruments/{pair}/candles?granularity=H1&count={count}&price=M"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            candles = []
            for c in r.json().get('candles', []):
                if c.get('complete'):
                    candles.append({
                        "time": c['time'],
                        "open": float(c['mid']['o']),
                        "high": float(c['mid']['h']),
                        "low": float(c['mid']['l']),
                        "close": float(c['mid']['c'])
                    })
            return pd.DataFrame(candles)
    except Exception as e:
        print(f"Data fetch error {pair}: {e}")
    return None

def compute_indicators(df):
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['sma150'] = df['close'].rolling(150).mean()
    
    # BB
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_mid'] + (2.0 * df['bb_std'])
    df['bb_lower'] = df['bb_mid'] - (2.0 * df['bb_std'])
    
    # ATR & ADX
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift(1)).abs()
    tr3 = (df['low'] - df['close'].shift(1)).abs()
    df['atr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean()
    
    up = df['high'] - df['high'].shift(1)
    down = df['low'].shift(1) - df['low']
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus_di = 100.0 * (pd.Series(plus_dm).rolling(14).mean() / (df['atr'] + 1e-9))
    minus_di = 100.0 * (pd.Series(minus_dm).rolling(14).mean() / (df['atr'] + 1e-9))
    df['adx'] = (100.0 * (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9))).rolling(14).mean()
    
    # Swing High/Low for Fibonacci calculation
    df['swing_high_20'] = df['high'].rolling(20).max()
    df['swing_low_20'] = df['low'].rolling(20).min()
    return df.dropna().reset_index(drop=True)

def run_simulation(pair, df):
    pip_mult = 0.01 if "JPY" in pair else 0.0001
    spread = SPREAD_PIPS.get(pair, 1.5) * pip_mult
    trades = []
    
    pos = None  # None or dict
    
    for i in range(160, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        
        # 1. Manage Active Position
        if pos is not None:
            side = pos['side']
            r_dist = pos['r_dist']
            
            # Check Scale-in (+0.5R in Buy Zone)
            # Buy Zone: Price between EMA20 and EMA50 and near 50%-61.8% Fib
            if side == 'LONG':
                mfe = row['high'] - pos['avg_entry']
                current_r = mfe / r_dist
                
                # Check Sell Zone Harvest (-0.5R when touching Upper BB or +2.0R)
                if not pos['harvested_sell_zone'] and (row['high'] >= row['bb_upper'] or current_r >= 2.0):
                    harvest_r = 0.5 * current_r
                    pos['realized_r'] += harvest_r
                    pos['units_r'] -= 0.5
                    pos['harvested_sell_zone'] = True
                    # Lock stop to BE on remainder
                    pos['stop_px'] = max(pos['stop_px'], pos['avg_entry'] + (0.2 * r_dist))
                
                # Scale-in (+0.5R in Buy Zone) if not added yet, and initial trade reached at least +0.8R first
                if not pos['added_buy_zone'] and pos['peak_r'] >= 0.8:
                    fib_50 = row['swing_high_20'] - (0.50 * (row['swing_high_20'] - row['swing_low_20']))
                    in_buy_zone = (row['low'] <= row['ema20']) and (row['close'] >= row['ema50'])
                    if in_buy_zone or (row['low'] <= fib_50 and row['close'] >= row['ema50']):
                        # Add 0.5R
                        new_units = pos['units_r'] + 0.5
                        pos['avg_entry'] = (pos['avg_entry'] * pos['units_r'] + row['close'] * 0.5) / new_units
                        pos['units_r'] = new_units
                        pos['added_buy_zone'] = True
                        pos['stop_px'] = max(pos['stop_px'], row['ema50'] - (0.5 * row['atr']))
                
                # Invalidation Punch-Through (Below EMA50 / Zone breakdown)
                invalidated = row['close'] < row['ema50'] - (0.5 * row['atr'])
                stopped = row['low'] <= pos['stop_px']
                
                if stopped or invalidated or pos['bars'] >= 60:
                    exit_px = pos['stop_px'] if stopped else row['close']
                    rem_r = ((exit_px - pos['avg_entry'] - spread) / r_dist) * pos['units_r']
                    total_trade_r = pos['realized_r'] + rem_r
                    trades.append({"pair": pair, "side": side, "net_r": total_trade_r, "bars": pos['bars']})
                    pos = None
                    continue
                else:
                    pos['bars'] += 1
                    pos['peak_r'] = max(pos['peak_r'], current_r)
                    
            elif side == 'SHORT':
                mfe = pos['avg_entry'] - row['low']
                current_r = mfe / r_dist
                
                if not pos['harvested_sell_zone'] and (row['low'] <= row['bb_lower'] or current_r >= 2.0):
                    harvest_r = 0.5 * current_r
                    pos['realized_r'] += harvest_r
                    pos['units_r'] -= 0.5
                    pos['harvested_sell_zone'] = True
                    pos['stop_px'] = min(pos['stop_px'], pos['avg_entry'] - (0.2 * r_dist))
                    
                if not pos['added_buy_zone'] and pos['peak_r'] >= 0.8:
                    fib_50 = row['swing_low_20'] + (0.50 * (row['swing_high_20'] - row['swing_low_20']))
                    in_sell_zone = (row['high'] >= row['ema20']) and (row['close'] <= row['ema50'])
                    if in_sell_zone or (row['high'] >= fib_50 and row['close'] <= row['ema50']):
                        new_units = pos['units_r'] + 0.5
                        pos['avg_entry'] = (pos['avg_entry'] * pos['units_r'] + row['close'] * 0.5) / new_units
                        pos['units_r'] = new_units
                        pos['added_buy_zone'] = True
                        pos['stop_px'] = min(pos['stop_px'], row['ema50'] + (0.5 * row['atr']))
                        
                invalidated = row['close'] > row['ema50'] + (0.5 * row['atr'])
                stopped = row['high'] >= pos['stop_px']
                
                if stopped or invalidated or pos['bars'] >= 60:
                    exit_px = pos['stop_px'] if stopped else row['close']
                    rem_r = ((pos['avg_entry'] - exit_px - spread) / r_dist) * pos['units_r']
                    total_trade_r = pos['realized_r'] + rem_r
                    trades.append({"pair": pair, "side": side, "net_r": total_trade_r, "bars": pos['bars']})
                    pos = None
                    continue
                else:
                    pos['bars'] += 1
                    pos['peak_r'] = max(pos['peak_r'], current_r)

        # 2. Check Entries on Strong Trend
        if pos is None:
            # Strong Trend Definition
            adx_strong = row['adx'] >= 25.0
            r_dist = max(18.0 * pip_mult, 1.5 * row['atr'])
            
            # Long Setup: Strong Trend Alignment
            if adx_strong and (row['close'] > row['ema20']) and (row['ema20'] > row['ema50']) and (row['ema50'] > row['sma150']):
                # Entry on pullback touch into 20 EMA
                if prev['low'] <= row['ema20'] and row['close'] > prev['high']:
                    pos = {
                        "side": "LONG",
                        "avg_entry": row['close'] + spread,
                        "r_dist": r_dist,
                        "stop_px": row['close'] - r_dist,
                        "units_r": 1.0,
                        "realized_r": 0.0,
                        "added_buy_zone": False,
                        "harvested_sell_zone": False,
                        "peak_r": 0.0,
                        "bars": 0
                    }
            # Short Setup
            elif adx_strong and (row['close'] < row['ema20']) and (row['ema20'] < row['ema50']) and (row['ema50'] < row['sma150']):
                if prev['high'] >= row['ema20'] and row['close'] < prev['low']:
                    pos = {
                        "side": "SHORT",
                        "avg_entry": row['close'] - spread,
                        "r_dist": r_dist,
                        "stop_px": row['close'] + r_dist,
                        "units_r": 1.0,
                        "realized_r": 0.0,
                        "added_buy_zone": False,
                        "harvested_sell_zone": False,
                        "peak_r": 0.0,
                        "bars": 0
                    }
                    
    return trades

def print_report(pair, trades):
    if not trades:
        print(f"{pair:<10} | No trades triggered under current filters.")
        return
    tdf = pd.DataFrame(trades)
    wins = tdf[tdf['net_r'] > 0]
    wr = len(wins) / len(tdf) * 100.0
    tot_r = tdf['net_r'].sum()
    mean_r = tdf['net_r'].mean()
    profit_factor = wins['net_r'].sum() / (abs(tdf[tdf['net_r'] <= 0]['net_r'].sum()) + 1e-9)
    print(f"{pair:<10} | Trades: {len(tdf):<3} | WinRate: {wr:4.1f}% | Total Net R: {tot_r:+6.2f}R | Mean R: {mean_r:+5.2f}R | PF: {profit_factor:4.2f}")

if __name__ == "__main__":
    print("=" * 80)
    print("CONFLUENCE ZONE (FIB + BB + MA 20/50/150 + S/R) SCALE-IN/TRIM AUDIT")
    print("=" * 80)
    for p in PAIRS:
        raw_df = fetch_data(p, count=2000)
        if raw_df is not None and not raw_df.empty:
            df = compute_indicators(raw_df)
            trades = run_simulation(p, df)
            print_report(p, trades)
        else:
            print(f"Failed to fetch data for {p}")
