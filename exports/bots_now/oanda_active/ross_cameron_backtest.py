#!/usr/bin/env python3
"""
Ross Cameron Momentum Backtester - Realistic
"""

import pandas as pd
import numpy as np

def ross_cameron_backtest(df, initial_capital=100000):
    capital = initial_capital
    position = 0
    entry_price = 0
    highest_since_entry = 0
    trades = []
    equity_curve = [initial_capital]

    for i in range(30, len(df)):
        price = df['close'].iloc[i]
        volume = df['volume'].iloc[i]
        avg_vol = df['volume'].rolling(20).mean().iloc[i]
        high_5 = df['high'].rolling(5).max().iloc[i-1]

        # Entry
        if position == 0 and price > high_5 * 1.01 and volume > avg_vol * 2.2:
            position = capital * 0.97 / price
            entry_price = price
            highest_since_entry = price
            trades.append({"type": "entry", "price": price})

        # Trailing stop + target
        elif position > 0:
            highest_since_entry = max(highest_since_entry, price)
            trail_stop = highest_since_entry * 0.96   # 4% trailing

            pnl_pct = (price - entry_price) / entry_price
            if price >= entry_price * 1.05 or price <= trail_stop:
                capital = position * price
                position = 0
                trades.append({"type": "exit", "price": price, "pnl": pnl_pct})

        equity_curve.append(capital if position == 0 else position * price)

    final_capital = capital if position == 0 else position * df['close'].iloc[-1]
    win_trades = len([t for t in trades if t.get("pnl", 0) > 0])
    total_closed = len([t for t in trades if "pnl" in t])
    win_rate = (win_trades / total_closed * 100) if total_closed > 0 else 0.0

    print("\n" + "="*75)
    print("ROSS CAMERON MOMENTUM BACKTEST - REALISTIC")
    print("="*75)
    print(f"Initial Capital : ${initial_capital:,.0f}")
    print(f"Final Capital   : ${final_capital:,.0f}")
    print(f"Total Return    : {(final_capital/initial_capital - 1)*100:.1f}%")
    print(f"Total Trades    : {total_closed}")
    print(f"Win Rate        : {win_rate:.1f}%")
    print(f"Max Equity      : ${max(equity_curve):,.0f}")
    print("="*75)

if __name__ == "__main__":
    np.random.seed(42)
    n = 1200
    dates = pd.date_range('2024-01-01', periods=n)
    returns = np.random.normal(0.0005, 0.016, n)
    # Strong momentum bursts
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
    ross_cameron_backtest(df)
