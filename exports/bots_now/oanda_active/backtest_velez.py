#!/usr/bin/env python3
"""
OANDA-Style Backtester + Parameter Optimizer (Fixed)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import oandapyV20
from oandapyV20.endpoints.instruments import InstrumentsCandles
import os

ACCOUNT_ID = "REDACTED_OANDA_ACCOUNT"
ACCESS_TOKEN = os.getenv('OANDA_TOKEN') or "REDACTED_OANDA_TOKEN"

client = oandapyV20.API(access_token=ACCESS_TOKEN)

def get_historical_data(instrument, count=3000, granularity="M15"):
    """Fetch historical data from OANDA (safe method)"""
    params = {
        "count": count,
        "granularity": granularity
    }
    
    r = InstrumentsCandles(instrument=instrument, params=params)
    client.request(r)
    
    data = []
    for candle in r.response['candles']:
        data.append({
            'time': pd.to_datetime(candle['time']),
            'open': float(candle['mid']['o']),
            'high': float(candle['mid']['h']),
            'low': float(candle['mid']['l']),
            'close': float(candle['mid']['c']),
        })
    return pd.DataFrame(data).set_index('time')

def calculate_indicators(df):
    df = df.copy()
    
    # RSI
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # Bollinger Bands
    sma = df['close'].rolling(20).mean()
    std = df['close'].rolling(20).std()
    df['bb_lower'] = sma - std * 2
    
    # ATR
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift()),
        abs(df['low'] - df['close'].shift())
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    
    # ADX
    plus_dm = df['high'] - df['high'].shift()
    minus_dm = df['low'].shift() - df['low']
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
    atr = tr.rolling(14).mean()
    plus_di = 100 * (plus_dm.rolling(14).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(14).mean() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    df['adx'] = dx.rolling(14).mean()
    
    return df

def run_backtest(df, rsi_entry=35, bb_mult=1.008, adx_max=26, 
                 stop_atr=1.6, target_atr=2.2, trail_atr=1.2, 
                 initial_balance=10000):
    
    df = calculate_indicators(df).dropna()
    
    balance = initial_balance
    equity_curve = [balance]
    trades = []
    in_position = False
    entry_price = stop_price = target_price = 0
    
    for i in range(1, len(df)):
        row = df.iloc[i]
        
        # Manage open position
        if in_position:
            if row['low'] <= stop_price:
                pnl = stop_price - entry_price
                balance += pnl
                trades.append({'type': 'loss', 'pnl': pnl})
                in_position = False
            elif row['high'] >= target_price:
                pnl = target_price - entry_price
                balance += pnl
                trades.append({'type': 'win', 'pnl': pnl})
                in_position = False
            else:
                new_trail = row['close'] - (row['atr'] * trail_atr)
                if new_trail > stop_price:
                    stop_price = new_trail
        
        # New entry signal
        if not in_position:
            if (row['rsi'] < rsi_entry and 
                row['close'] <= row['bb_lower'] * bb_mult and 
                row['adx'] < adx_max):
                
                entry_price = row['close']
                stop_price = entry_price - (row['atr'] * stop_atr)
                target_price = entry_price + (row['atr'] * target_atr)
                in_position = True
        
        equity_curve.append(balance)
    
    wins = [t for t in trades if t['type'] == 'win']
    losses = [t for t in trades if t['type'] == 'loss']
    
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    profit_factor = sum(t['pnl'] for t in wins) / abs(sum(t['pnl'] for t in losses)) if losses else float('inf')
    total_pnl = balance - initial_balance
    
    return {
        'final_balance': round(balance, 2),
        'total_pnl': round(total_pnl, 2),
        'total_trades': len(trades),
        'win_rate': round(win_rate, 1),
        'profit_factor': round(profit_factor, 2),
    }

# ====================== RUN ======================

print("📥 Fetching \~3000 candles (\~31 days) from OANDA...")
df = get_historical_data("EUR_USD", count=3000)

print("\n🔬 Running base backtest with current parameters...")
results = run_backtest(df)
for k, v in results.items():
    print(f"  {k}: {v}")

print("\n🔧 Optimizing parameters (grid search)...")

best = {'profit_factor': 0}
for rsi in [30, 35, 40]:
    for bb in [1.005, 1.008, 1.01]:
        for adx in [20, 26, 30]:
            res = run_backtest(df, rsi_entry=rsi, bb_mult=bb, adx_max=adx)
            if res['profit_factor'] > best['profit_factor'] and res['total_trades'] > 15:
                best = res
                best['params'] = {'rsi': rsi, 'bb_mult': bb, 'adx': adx}

print("\n🏆 Best Parameters Found:")
print(f"  RSI Entry     : {best['params']['rsi']}")
print(f"  BB Multiplier : {best['params']['bb_mult']}")
print(f"  ADX Max       : {best['params']['adx']}")
print(f"  Profit Factor : {best['profit_factor']}")
print(f"  Win Rate      : {best['win_rate']}%")
print(f"  Total Trades  : {best['total_trades']}")

print("\n✅ Done. Update your live bot with the best parameters above for better performance.")
