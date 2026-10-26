#!/usr/bin/env python3
"""
Velez Forex - SMALL Fast Parameter Sweep (Fixed)
"""

import pandas as pd
import numpy as np
from itertools import product
import oandapyV20
from oandapyV20.endpoints.instruments import InstrumentsCandles
import os

ACCESS_TOKEN = os.getenv('OANDA_TOKEN') or "REDACTED_OANDA_TOKEN"

PAIRS = ["EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY"]
GRANULARITY = "M15"
DAYS_BACK = 120

client = oandapyV20.API(access_token=ACCESS_TOKEN)

# ================== SMALL GRID ==================
param_grid = {
    'rsi_long':     [26, 28, 30],
    'rsi_short':    [70, 72, 74],
    'bb_mult_long': [0.995, 1.0, 1.005],
    'bb_mult_short':[0.995, 1.0, 1.005],
    'adx_max':      [20, 22, 24],
    'stop_atr':     [1.4, 1.5, 1.6],
    'target_atr':   [2.4, 2.6, 2.8, 3.0],
}

def fetch_candles(instrument, days=DAYS_BACK):
    count = int(days * 24 * 4)
    params = {"count": min(count, 5000), "granularity": GRANULARITY}
    r = InstrumentsCandles(instrument=instrument, params=params)
    client.request(r)
    data = r.response['candles']
    df = pd.DataFrame([{
        'time': pd.to_datetime(c['time']),
        'open': float(c['mid']['o']),
        'high': float(c['mid']['h']),
        'low': float(c['mid']['l']),
        'close': float(c['mid']['c']),
    } for c in data])
    return df.set_index('time')

def calculate_indicators(df):
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    df['rsi'] = 100 - (100 / (1 + gain / loss))

    sma = df['close'].rolling(20).mean()
    std = df['close'].rolling(20).std()
    df['bb_lower'] = sma - std * 2
    df['bb_upper'] = sma + std * 2

    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift()),
        abs(df['low'] - df['close'].shift())
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()

    plus_dm = df['high'] - df['high'].shift()
    minus_dm = df['low'].shift() - df['low']
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
    atr = tr.rolling(14).mean()
    plus_di = 100 * (plus_dm.rolling(14).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(14).mean() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)) * 100
    df['adx'] = dx.rolling(14).mean()
    return df

def run_backtest(df, params):
    df = df.copy()
    df['long_signal'] = (df['rsi'] < params['rsi_long']) & \
                        (df['close'] <= df['bb_lower'] * params['bb_mult_long']) & \
                        (df['adx'] < params['adx_max'])

    df['short_signal'] = (df['rsi'] > params['rsi_short']) & \
                         (df['close'] >= df['bb_upper'] * params['bb_mult_short']) & \
                         (df['adx'] < params['adx_max'])

    trades = []
    position = None

    for i in range(80, len(df)):
        row = df.iloc[i]
        if position is None:
            if row['long_signal']:
                entry = row['close']
                stop = entry - row['atr'] * params['stop_atr']
                target = entry + row['atr'] * params['target_atr']
                position = {'dir': 'LONG', 'entry': entry, 'stop': stop, 'target': target}
            elif row['short_signal']:
                entry = row['close']
                stop = entry + row['atr'] * params['stop_atr']
                target = entry - row['atr'] * params['target_atr']
                position = {'dir': 'SHORT', 'entry': entry, 'stop': stop, 'target': target}
        elif position is not None:
            if position['dir'] == 'LONG':
                if row['low'] <= position['stop']:
                    pnl = (position['stop'] - position['entry']) / position['entry']
                    trades.append({'pnl': pnl, 'result': 'LOSS'})
                    position = None
                elif row['high'] >= position['target']:
                    pnl = (position['target'] - position['entry']) / position['entry']
                    trades.append({'pnl': pnl, 'result': 'WIN'})
                    position = None
            else:
                if row['high'] >= position['stop']:
                    pnl = (position['entry'] - position['stop']) / position['entry']
                    trades.append({'pnl': pnl, 'result': 'LOSS'})
                    position = None
                elif row['low'] <= position['target']:
                    pnl = (position['entry'] - position['target']) / position['entry']
                    trades.append({'pnl': pnl, 'result': 'WIN'})
                    position = None
    return pd.DataFrame(trades)

# ================== RUN ==================
print("Starting SMALL Fast Parameter Sweep (Fixed)...\n")

results = []
keys = param_grid.keys()
combinations = list(product(*param_grid.values()))
print(f"Testing {len(combinations)} combinations across {len(PAIRS)} pairs...\n")

for combo in combinations:
    params = dict(zip(keys, combo))
    all_trades = []

    for pair in PAIRS:
        try:
            df = fetch_candles(pair)
            df = calculate_indicators(df)
            trades = run_backtest(df, params)
            if not trades.empty:
                trades['pair'] = pair
                all_trades.append(trades)
        except:
            continue

    if not all_trades:
        continue

    df_trades = pd.concat(all_trades, ignore_index=True)
    total = len(df_trades)
    if total < 25:
        continue

    wins = len(df_trades[df_trades['result'] == 'WIN'])
    win_rate = wins / total * 100

    pf = abs(df_trades[df_trades['result'] == 'WIN']['pnl'].sum() /
             df_trades[df_trades['result'] == 'LOSS']['pnl'].sum()) if len(df_trades[df_trades['result'] == 'LOSS']) > 0 else 0

    avg_win = df_trades[df_trades['result'] == 'WIN']['pnl'].mean() * 100
    avg_loss = df_trades[df_trades['result'] == 'LOSS']['pnl'].mean() * 100
    expectancy = (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss)

    score = (win_rate / 100) * pf * np.log(total + 1)

    results.append({
        **params,
        'total_trades': total,
        'win_rate': round(win_rate, 1),
        'profit_factor': round(pf, 2),
        'expectancy_%': round(expectancy, 3),
        'avg_win_%': round(avg_win, 3),
        'avg_loss_%': round(avg_loss, 3),
        'score': round(score, 3)
    })

if not results:
    print("No valid combinations found.")
    exit()

results_df = pd.DataFrame(results).sort_values('score', ascending=False)

print("\n" + "="*95)
print("TOP RESULTS FROM SMALL SWEEP (sorted by Score)")
print("="*95)
print(results_df.head(10).to_string(index=False))

results_df.to_csv("parameter_sweep_small_results.csv", index=False)
print(f"\n✅ Saved to parameter_sweep_small_results.csv | Total valid: {len(results_df)}")
