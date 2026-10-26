#!/usr/bin/env python3
"""
Ross Cameron Momentum Parameter Optimizer
"""

import pandas as pd
import numpy as np
from itertools import product

def backtest_with_params(df, breakout_mult=1.01, vol_mult=2.2, profit_target=0.05, trail_pct=0.04, initial_capital=100000):
    capital = initial_capital
    position = 0
    entry_price = 0
    highest_since_entry = 0
    trades = 0
    wins = 0

    for i in range(30, len(df)):
        price = df['close'].iloc[i]
        volume = df['volume'].iloc[i]
        avg_vol = df['volume'].rolling(20).mean().iloc[i]
        high_5 = df['high'].rolling(5).max().iloc[i-1]

        if position == 0 and price > high_5 * breakout_mult and volume > avg_vol * vol_mult:
            position = capital * 0.97 / price
            entry_price = price
            highest_since_entry = price

        elif position > 0:
            highest_since_entry = max(highest_since_entry, price)
            trail_stop = highest_since_entry * (1 - trail_pct)
            pnl_pct = (price - entry_price) / entry_price

            if price >= entry_price * (1 + profit_target) or price <= trail_stop:
                capital = position * price
                position = 0
                trades += 1
                if pnl_pct > 0:
                    wins += 1

    final_capital = capital
    win_rate = (wins / trades * 100) if trades > 0 else 0
    return final_capital, trades, win_rate

if __name__ == "__main__":
    # Generate realistic data
    np.random.seed(42)
    n = 1200
    dates = pd.date_range('2024-01-01', periods=n)
    returns = np.random.normal(0.0005, 0.016, n)
    returns[90:180] *= 4.0
    returns[420:510] *= 3.5
    returns[780:860] *= 3.8
    returns[1050:1130] *= 3.2
    close = 100 * np.exp(np.cumsum(returns))
    high = close * (1 + np.random.uniform(0.01, 0.035, n))
    volume = np.random.randint(100000, 1200000, n).astype(float)
    volume[90:180] *= 4.5
    volume[420:510] *= 4.0
    volume[780:860] *= 4.2
    volume[1050:1130] *= 3.8

    df = pd.DataFrame({'close': close, 'high': high, 'volume': volume}, index=dates)

    print("Starting Parameter Optimization...\n")

    best_return = 0
    best_params = None

    # Grid search
    for breakout_mult, vol_mult, profit_target, trail_pct in product(
        [1.005, 1.008, 1.01, 1.015],
        [1.8, 2.0, 2.2, 2.5],
        [0.04, 0.05, 0.06],
        [0.035, 0.04, 0.045]
    ):
        final, trades, win_rate = backtest_with_params(df, breakout_mult, vol_mult, profit_target, trail_pct)
        ret = (final / 100000 - 1) * 100

        if ret > best_return:
            best_return = ret
            best_params = (breakout_mult, vol_mult, profit_target, trail_pct)

        if trades > 5:
            print(f"Breakout: {breakout_mult:.3f} | Vol: {vol_mult:.1f} | Target: {profit_target:.2f} | Trail: {trail_pct:.3f} → Return: {ret:.1f}% | Trades: {trades} | Win: {win_rate:.1f}%")

    print("\n" + "="*70)
    print("BEST PARAMETERS FOUND")
    print("="*70)
    print(f"Breakout Mult : {best_params[0]}")
    print(f"Volume Mult   : {best_params[1]}")
    print(f"Profit Target : {best_params[2]}")
    print(f"Trail %       : {best_params[3]}")
    print(f"Best Return   : {best_return:.1f}%")
    print("="*70)
