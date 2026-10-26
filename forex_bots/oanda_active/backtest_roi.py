#!/usr/bin/env python3
"""
Velez Mean Reversion Backtester
Estimates ROI using the exact live strategy logic
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import oandapyV20
from oandapyV20.endpoints.instruments import InstrumentsCandles
import os

# ================== CONFIG ==================
ACCOUNT_ID = "REDACTED_OANDA_ACCOUNT"
ACCESS_TOKEN = os.getenv('OANDA_TOKEN') or "REDACTED_OANDA_TOKEN"

PAIRS = ["EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "USD_CAD"]
GRANULARITY = "M15"
DAYS_BACK = 180          # How much history to test (increase for better stats)

# Strategy Parameters (same as live)
RSI_LONG = 35
RSI_SHORT = 65
BB_MULT_LONG = 1.005
BB_MULT_SHORT = 0.995
ADX_MAX = 20
STOP_ATR = 1.6
TARGET_ATR = 2.2

RISK_PER_TRADE = 0.005   # 0.5%

client = oandapyV20.API(access_token=ACCESS_TOKEN)

def fetch_candles(instrument, days=DAYS_BACK):
    count = int(days * 24 * 4)  # M15 candles
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

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def bollinger_bands(series, period=20, std=2):
    sma = series.rolling(window=period).mean()
    stddev = series.rolling(window=period).std()
    return sma - stddev * std, sma + stddev * std

def adx(high, low, close, period=14):
    tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
    plus_dm = high - high.shift()
    minus_dm = low.shift() - low
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)) * 100
    return dx.rolling(period).mean()

def atr(high, low, close, period=14):
    tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def run_backtest(df, pair):
    df = df.copy()
    df['rsi'] = rsi(df['close'])
    df['bb_lower'], df['bb_upper'] = bollinger_bands(df['close'])
    df['adx'] = adx(df['high'], df['low'], df['close'])
    df['atr'] = atr(df['high'], df['low'], df['close'])

    df['long_signal'] = (df['rsi'] < RSI_LONG) & (df['close'] <= df['bb_lower'] * BB_MULT_LONG) & (df['adx'] < ADX_MAX)
    df['short_signal'] = (df['rsi'] > RSI_SHORT) & (df['close'] >= df['bb_upper'] * BB_MULT_SHORT) & (df['adx'] < ADX_MAX)

    trades = []
    position = None

    for i in range(80, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]

        if position is None:
            if row['long_signal']:
                entry = row['close']
                stop = entry - row['atr'] * STOP_ATR
                target = entry + row['atr'] * TARGET_ATR
                position = {'direction': 'LONG', 'entry': entry, 'stop': stop, 'target': target, 'entry_time': row.name}
            elif row['short_signal']:
                entry = row['close']
                stop = entry + row['atr'] * STOP_ATR
                target = entry - row['atr'] * TARGET_ATR
                position = {'direction': 'SHORT', 'entry': entry, 'stop': stop, 'target': target, 'entry_time': row.name}

        elif position is not None:
            if position['direction'] == 'LONG':
                if row['low'] <= position['stop']:
                    pnl = (position['stop'] - position['entry']) / position['entry']
                    trades.append({**position, 'exit_time': row.name, 'exit_price': position['stop'], 'pnl': pnl, 'result': 'LOSS'})
                    position = None
                elif row['high'] >= position['target']:
                    pnl = (position['target'] - position['entry']) / position['entry']
                    trades.append({**position, 'exit_time': row.name, 'exit_price': position['target'], 'pnl': pnl, 'result': 'WIN'})
                    position = None
            else:  # SHORT
                if row['high'] >= position['stop']:
                    pnl = (position['entry'] - position['stop']) / position['entry']
                    trades.append({**position, 'exit_time': row.name, 'exit_price': position['stop'], 'pnl': pnl, 'result': 'LOSS'})
                    position = None
                elif row['low'] <= position['target']:
                    pnl = (position['entry'] - position['target']) / position['entry']
                    trades.append({**position, 'exit_time': row.name, 'exit_price': position['target'], 'pnl': pnl, 'result': 'WIN'})
                    position = None

    return pd.DataFrame(trades)

# ================== RUN BACKTEST ==================
print("Running backtest on all pairs...\n")
all_trades = []

for pair in PAIRS:
    print(f"Backtesting {pair}...")
    try:
        df = fetch_candles(pair)
        trades = run_backtest(df, pair)
        trades['pair'] = pair
        all_trades.append(trades)
        print(f"  → {len(trades)} trades found")
    except Exception as e:
        print(f"  Error on {pair}: {e}")

if not all_trades:
    print("No trades generated. Check parameters or data.")
    exit()

results = pd.concat(all_trades, ignore_index=True)

# ================== METRICS ==================
total_trades = len(results)
wins = len(results[results['result'] == 'WIN'])
win_rate = wins / total_trades * 100 if total_trades > 0 else 0
total_return = results['pnl'].sum() * 100
avg_win = results[results['result'] == 'WIN']['pnl'].mean() * 100
avg_loss = results[results['result'] == 'LOSS']['pnl'].mean() * 100
profit_factor = abs(results[results['result'] == 'WIN']['pnl'].sum() / results[results['result'] == 'LOSS']['pnl'].sum()) if len(results[results['result'] == 'LOSS']) > 0 else float('inf')

# Rough monthly ROI estimate (assuming \~20 trading days/month and avg 2-4 trades/day across pairs)
avg_trades_per_day = total_trades / DAYS_BACK
monthly_roi_estimate = (avg_trades_per_day * 20) * (results['pnl'].mean() * 100) * (RISK_PER_TRADE * 100)

print("\n" + "="*60)
print("BACKTEST RESULTS (Live Strategy Logic)")
print("="*60)
print(f"Period              : Last {DAYS_BACK} days")
print(f"Total Trades        : {total_trades}")
print(f"Win Rate            : {win_rate:.1f}%")
print(f"Total Return        : {total_return:.2f}%")
print(f"Average Win         : {avg_win:.2f}%")
print(f"Average Loss        : {avg_loss:.2f}%")
print(f"Profit Factor       : {profit_factor:.2f}")
print(f"Expected Monthly ROI: \~{monthly_roi_estimate:.1f}% (at 0.5% risk)")
print("="*60)

results.to_csv("backtest_results.csv", index=False)
print("\nDetailed results saved to backtest_results.csv")
