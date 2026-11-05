#!/usr/bin/env python3
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"
client     = StockHistoricalDataClient(API_KEY, SECRET_KEY)

def run_realistic_backtest(symbol="NVDA", days=60):
    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    req = StockBarsRequest(symbol_or_symbols=[symbol], timeframe=TimeFrame(15, TimeFrameUnit.Minute), start=start_date, feed=DataFeed.IEX)
    res = client.get_stock_bars(req)
    df = res.df.xs(symbol, level='symbol') if isinstance(res.df.index, pd.MultiIndex) else res.df
    df = df.copy()

    # Indicators
    df['sma20'] = df['close'].rolling(20).mean()
    df['sma200'] = df['close'].rolling(200).mean()
    tr = pd.concat([df['high'] - df['low'], (df['high'] - df['close'].shift(1)).abs(), (df['low'] - df['close'].shift(1)).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    
    # 20 MA Slope
    df['sma20_slope'] = ((df['sma20'] - df['sma20'].shift(5)) / (df['sma20'].shift(5) + 1e-9)) * 100

    # Velez Stage 2 Dip Condition: Price > 200 SMA, 20 SMA sloping up, Pullback to 20 MA zone
    z1 = df['sma20'] - (0.50 * df['atr'])
    df['signal'] = (df['close'] > df['sma200']) & (df['sma20_slope'] > 0.05) & (df['close'] <= z1)

    balance = 10000.0
    equity_curve = [balance]
    trades = []
    
    in_pos = False
    entry = 0.0
    stop = 0.0
    shares = 0
    up_bars = 0

    for i in range(200, len(df)):
        curr = df.iloc[i]
        bar_time = df.index[i]
        
        # Check market hour bounds (Avoid overnight holding)
        is_eod = hasattr(bar_time, 'hour') and bar_time.hour >= 15 and bar_time.minute >= 45

        # 1. Manage Active Position
        if in_pos:
            # EOD Mandatory Exit or Hard Stop Hit
            if is_eod or curr['low'] <= stop:
                exit_price = stop if curr['low'] <= stop else curr['close']
                pnl = shares * (exit_price - entry)
                balance += pnl
                trades.append(pnl)
                in_pos = False
                equity_curve.append(balance)
                continue

            # Stair-step profit taking: First green bar locks 1/3 and moves stop to Break-Even
            is_green = curr['close'] > curr['open']
            if is_green:
                up_bars += 1
                if up_bars == 1:
                    stop = max(stop, entry) # Trailing stop to Break-Even
                elif up_bars >= 3:
                    pnl = shares * (curr['close'] - entry)
                    balance += pnl
                    trades.append(pnl)
                    in_pos = False

        # 2. Open New Position
        elif not in_pos and curr['signal'] and not is_eod:
            entry = curr['close']
            atr = curr['atr'] if not np.isnan(curr['atr']) else 1.0
            stop = entry - (1.50 * atr)
            risk_amt = balance * 0.01
            risk_per_share = max(0.10, entry - stop)
            shares = int(risk_amt / risk_per_share)
            
            if shares > 0:
                in_pos = True
                up_bars = 0

        equity_curve.append(balance)

    # Metrics
    if not trades: return print("No trades generated.")
    t_arr = np.array(trades)
    wins = t_arr[t_arr > 0]
    losses = t_arr[t_arr < 0]
    
    win_rate = (len(wins) / len(trades)) * 100
    gross_p = np.sum(wins) if len(wins) > 0 else 0
    gross_l = abs(np.sum(losses)) if len(losses) > 0 else 1e-9
    pf = gross_p / gross_l

    print("=" * 60)
    print(f"      📊 REALISTIC VELEZ INTRADAY BACKTEST ({symbol})      ")
    print("=" * 60)
    print(f"Net Realized P&L    : ${balance - 10000.0:,.2f}")
    print(f"Total Trades        : {len(trades)}")
    print(f"Win Rate            : {win_rate:.1f}%")
    print(f"Profit Factor       : {pf:.2f}")
    print(f"Average Win         : ${np.mean(wins) if len(wins) > 0 else 0:,.2f}")
    print(f"Average Loss        : ${np.mean(losses) if len(losses) > 0 else 0:,.2f}")
    print("=" * 60)

if __name__ == "__main__":
    run_realistic_backtest("NVDA", days=60)
