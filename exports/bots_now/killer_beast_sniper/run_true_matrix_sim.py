#!/usr/bin/env python3
import os
import requests
import numpy as np
import pandas as pd

OANDA_API_KEY = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxtrade.oanda.com")
HEADERS = {"Authorization": f"Bearer {OANDA_API_KEY}", "Content-Type": "application/json"}

PAIRS = ["USD_JPY", "USD_CAD"]
SPREADS = {"USD_JPY": 1.5, "USD_CAD": 1.8}
HARD_CAPS = {"USD_JPY": 20000, "USD_CAD": 35000}
NAV = 62800.0
RISK_PCT = 0.005  # $314.00 base risk

def fetch_candles(pair, count=1500):
    url = f"{OANDA_URL}/v3/instruments/{pair}/candles?granularity=H1&count={count}&price=M"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            data = []
            for c in r.json().get('candles', []):
                if c.get('complete'):
                    data.append({
                        "time": c['time'],
                        "open": float(c['mid']['o']),
                        "high": float(c['mid']['h']),
                        "low": float(c['mid']['l']),
                        "close": float(c['mid']['c'])
                    })
            df = pd.DataFrame(data)
            return df
    except Exception as e:
        print(f"Error fetching {pair}: {e}")
    return None

def compute_indicators(df, pair):
    pip_val = 0.01 if "JPY" in pair else 0.0001
    
    # BB
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_mid'] + (2.0 * df['bb_std'])
    df['bb_lower'] = df['bb_mid'] - (2.0 * df['bb_std'])
    
    # ATR
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift(1)).abs()
    tr3 = (df['low'] - df['close'].shift(1)).abs()
    df['atr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean()
    
    # RSI
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    df['rsi'] = 100.0 - (100.0 / (1.0 + rs))
    
    # EMA21
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    
    # ADX
    up = df['high'] - df['high'].shift(1)
    down = df['low'].shift(1) - df['low']
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus_di = 100.0 * (pd.Series(plus_dm).rolling(14).mean() / (df['atr'] + 1e-9))
    minus_di = 100.0 * (pd.Series(minus_dm).rolling(14).mean() / (df['atr'] + 1e-9))
    df['adx'] = (100.0 * (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9))).rolling(14).mean()
    
    return df.dropna().reset_index(drop=True)

def calculate_units(pair, price, stop_dist):
    risk_dollars = NAV * RISK_PCT
    # Corrected USD-base sizing: stop cost in quote converted to USD
    raw_units = (risk_dollars * price) / stop_dist
    notional_max = NAV * 2.0  # 2.0x gross NAV ceiling
    capped = min(raw_units, notional_max, HARD_CAPS.get(pair, 20000))
    return int(capped)

def simulate(pair, df, mode):
    pip_val = 0.01 if "JPY" in pair else 0.0001
    spread = SPREADS.get(pair, 1.5)
    trades = []
    pos = None
    
    for i in range(50, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        
        # 1. Manage Open Position
        if pos is not None:
            pos['bars'] += 1
            exit_px = None
            entry = pos['entry_px']
            risk = pos['risk']
            
            if pos['side'] == 'LONG':
                mfe = row['high'] - entry
                r = mfe / risk
                
                # Exits
                if pos['type'] == 'MEAN_REV':
                    if row['high'] >= row['bb_mid'] or r >= 1.2:
                        exit_px = max(row['bb_mid'], entry + (0.2 * risk))
                    elif row['low'] <= pos['sl'] or pos['bars'] >= 24:
                        exit_px = pos['sl'] if row['low'] <= pos['sl'] else row['close']
                else: # TREND
                    if r >= 2.0:
                        pos['sl'] = max(pos['sl'], row['ema21'] - (0.5 * row['atr']))
                    elif r >= 1.2:
                        pos['sl'] = max(pos['sl'], entry + (0.2 * risk))
                    if row['low'] <= pos['sl'] or pos['bars'] >= 48:
                        exit_px = pos['sl'] if row['low'] <= pos['sl'] else row['close']
            else: # SHORT
                mfe = entry - row['low']
                r = mfe / risk
                if pos['type'] == 'MEAN_REV':
                    if row['low'] <= row['bb_mid'] or r >= 1.2:
                        exit_px = min(row['bb_mid'], entry - (0.2 * risk))
                    elif row['high'] >= pos['sl'] or pos['bars'] >= 24:
                        exit_px = pos['sl'] if row['high'] >= pos['sl'] else row['close']
                else: # TREND
                    if r >= 2.0:
                        pos['sl'] = min(pos['sl'], row['ema21'] + (0.5 * row['atr']))
                    elif r >= 1.2:
                        pos['sl'] = min(pos['sl'], entry - (0.2 * risk))
                    if row['high'] >= pos['sl'] or pos['bars'] >= 48:
                        exit_px = pos['sl'] if row['high'] >= pos['sl'] else row['close']
                        
            if exit_px is not None:
                pips = ((exit_px - entry) / pip_val) if pos['side'] == 'LONG' else ((entry - exit_px) / pip_val)
                net_pips = pips - spread
                # Correct dollar PnL for USD_XXX base pair:
                # 1 pip per unit = pip_val / exit_px dollars
                dollar_pnl = net_pips * (pip_val / exit_px) * pos['units']
                r_realized = dollar_pnl / (NAV * RISK_PCT)
                trades.append(r_realized)
                pos = None
                
        # 2. Check Entries
        if pos is None:
            stop_dist = max(15.0 * pip_val, 1.5 * row['atr'])
            units = calculate_units(pair, row['close'], stop_dist)
            
            # Determine active strategy type based on mode
            active_type = None
            if mode == 'MEAN_REV':
                active_type = 'MEAN_REV'
            elif mode == 'TREND':
                active_type = 'TREND'
            elif mode == 'ADX_ROUTER':
                active_type = 'TREND' if row['adx'] >= 22.0 else 'MEAN_REV'
                
            # Signal checks
            if active_type == 'MEAN_REV':
                if (prev['low'] <= prev['bb_lower']) and (row['close'] > row['bb_lower']) and (row['rsi'] < 38):
                    pos = {'side': 'LONG', 'type': 'MEAN_REV', 'entry_px': row['close'], 'risk': stop_dist, 'sl': row['close'] - stop_dist, 'units': units, 'bars': 0}
                elif (prev['high'] >= prev['bb_upper']) and (row['close'] < row['bb_upper']) and (row['rsi'] > 62):
                    pos = {'side': 'SHORT', 'type': 'MEAN_REV', 'entry_px': row['close'], 'risk': stop_dist, 'sl': row['close'] + stop_dist, 'units': units, 'bars': 0}
            elif active_type == 'TREND':
                if (row['close'] > row['ema21']) and (prev['low'] <= row['ema21']) and (row['close'] > prev['high']):
                    pos = {'side': 'LONG', 'type': 'TREND', 'entry_px': row['close'], 'risk': stop_dist, 'sl': row['close'] - stop_dist, 'units': units, 'bars': 0}
                elif (row['close'] < row['ema21']) and (prev['high'] >= row['ema21']) and (row['close'] < prev['low']):
                    pos = {'side': 'SHORT', 'type': 'TREND', 'entry_px': row['close'], 'risk': stop_dist, 'sl': row['close'] + stop_dist, 'units': units, 'bars': 0}
                    
    return trades

def print_metrics(name, pair, trades):
    if not trades:
        print(f"{name:<22} | {pair:<8} | Trades: 0")
        return
    t = np.array(trades)
    wins = t[t > 0]
    losses = t[t <= 0]
    wr = len(wins) / len(t) * 100.0
    exp_r = np.mean(t)
    cum = np.cumsum(t)
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak).min()
    print(f"{name:<22} | {pair:<8} | Trades: {len(t):<3} | WR: {wr:4.1f}% | Expectancy: {exp_r:+5.2f}R | MaxDD: {dd:5.2f}R")

if __name__ == "__main__":
    print("=" * 78)
    print("      REAL OANDA TICK/CANDLE SIMULATION & REGIME AUDIT (1500 H1 BARS)")
    print("=" * 78)
    for pair in PAIRS:
        df = fetch_candles(pair, count=1500)
        if df is None or df.empty:
            print(f"Could not load data for {pair}")
            continue
        df = compute_indicators(df, pair)
        
        t_mr = simulate(pair, df, 'MEAN_REV')
        print_metrics("1. Pure Mean-Rev", pair, t_mr)
        
        t_tr = simulate(pair, df, 'TREND')
        print_metrics("2. Pure Trend", pair, t_tr)
        
        t_adx = simulate(pair, df, 'ADX_ROUTER')
        print_metrics("3. Dynamic ADX", pair, t_adx)
        print("-" * 78)
