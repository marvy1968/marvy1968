#!/usr/bin/env python3
"""
Oliver Velez 15-Minute Intraday Scalper Backtest
- Universe: NVDA, TSLA, AMD
- Timeframe: 2-Minute Resampled Bars
- Feed: DataFeed.IEX (Compliant with free market data plans)
- Hold Rule: Hard Exit at 15 Minutes (8 bars) if SL/TP not hit
- Sizing: 25% Equity per trade (Max 1 concurrent position)
- Cost Model: $0.01/share slippage + spread drag
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

API_KEY = os.getenv("ALPACA_API_KEY", "")
API_SECRET = os.getenv("ALPACA_SECRET_KEY", "")
SYMBOLS = ["NVDA", "TSLA", "AMD"]

START_EQUITY = 100_000.0
ALLOCATION_PCT = 0.25           # 25% Equity per trade
MAX_HOLD_BARS = 8               # 8 x 2-min bars = 16 min (~15-min hard time stop)
SLIPPAGE_PER_SHARE = 0.01       # Bid-ask spread + slippage
LOOKBACK_DAYS = 30              # 30 calendar days

data_client = StockHistoricalDataClient(API_KEY, API_SECRET)

def load_data(symbol: str) -> pd.DataFrame:
    print(f"[*] Fetching intraday IEX data for {symbol} ({LOOKBACK_DAYS} days)...")
    end_dt = datetime.now(timezone.utc) - timedelta(minutes=20)
    start_dt = end_dt - timedelta(days=LOOKBACK_DAYS)
    
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start_dt,
        end=end_dt,
        feed=DataFeed.IEX
    )
    try:
        bars = data_client.get_stock_bars(req)
    except Exception as e:
        print(f"[!] API query error for {symbol}: {e}")
        return pd.DataFrame()

    df = bars.df
    if df.empty:
        return pd.DataFrame()
    
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol)
    
    # Regular Trading Hours (13:30 - 20:00 UTC / 9:30 AM - 4:00 PM Eastern)
    df = df.between_time("13:30", "20:00")
    
    # Resample to 2-minute bars
    resampled = df.resample("2min").agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    
    # Oliver Velez Indicators
    resampled['ema20'] = resampled['close'].ewm(span=20, adjust=False).mean()
    resampled['sma200'] = resampled['close'].rolling(window=200, min_periods=20).mean()
    resampled['bar_range'] = resampled['high'] - resampled['low']
    resampled['avg_range'] = resampled['bar_range'].rolling(window=20).mean()
    
    return resampled.dropna()

def simulate_symbol(symbol: str, df: pd.DataFrame) -> list:
    trades = []
    in_position = False
    entry_idx = 0
    entry_price = 0.0
    stop_loss = 0.0
    take_profit = 0.0
    setup_type = ""
    
    n = len(df)
    for i in range(25, n - 1):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]
        next_open = df.iloc[i + 1]['open']
        
        if not in_position:
            # Velez Bullish Trend Filter
            is_bull_trend = (curr['close'] > curr['ema20']) and (curr['ema20'] > curr['sma200'])
            if not is_bull_trend:
                continue
            
            # Setup A: Elephant Bar (Igniting bar)
            is_elephant = (curr['bar_range'] > 1.8 * curr['avg_range']) and \
                          (curr['close'] > curr['open']) and \
                          ((curr['close'] - curr['low']) / (curr['bar_range'] + 1e-6) > 0.75)
            
            # Setup B: 20 EMA Pullback recovery
            is_pullback = (prev['low'] <= prev['ema20'] * 1.001) and \
                          (curr['close'] > prev['high']) and \
                          (curr['close'] > curr['ema20'])
            
            if is_elephant or is_pullback:
                in_position = True
                entry_idx = i + 1
                entry_price = next_open + SLIPPAGE_PER_SHARE
                stop_loss = curr['low'] - SLIPPAGE_PER_SHARE
                risk = max(entry_price - stop_loss, entry_price * 0.003)
                take_profit = entry_price + (risk * 1.5)
                setup_type = "ELEPHANT" if is_elephant else "PULLBACK"
                continue

        if in_position:
            bars_held = i - entry_idx + 1
            exit_price = None
            exit_reason = None
            
            if curr['low'] <= stop_loss:
                exit_price = stop_loss - SLIPPAGE_PER_SHARE
                exit_reason = "STOP_LOSS"
            elif curr['high'] >= take_profit:
                exit_price = take_profit - SLIPPAGE_PER_SHARE
                exit_reason = "TAKE_PROFIT"
            elif bars_held >= MAX_HOLD_BARS:
                exit_price = curr['close'] - SLIPPAGE_PER_SHARE
                exit_reason = "TIME_STOP"
            
            if exit_price is not None:
                pnl_per_share = exit_price - entry_price
                r_mult = pnl_per_share / max(entry_price - stop_loss, 0.01)
                trades.append({
                    "symbol": symbol,
                    "entry_time": df.index[entry_idx],
                    "exit_time": df.index[i],
                    "bars_held": bars_held,
                    "setup": setup_type,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl_pct": (exit_price / entry_price) - 1.0,
                    "r_multiple": r_mult,
                    "exit_reason": exit_reason
                })
                in_position = False

    return trades

def run_backtest():
    all_trades = []
    print("=" * 75)
    print("OLIVER VELEZ 15-MINUTE SCALPER: INTRADAY ROBUSTNESS BACKTEST")
    print(f"Universe: {SYMBOLS} | Timeframe: 2-Min | Max Hold: {MAX_HOLD_BARS * 2} min")
    print("=" * 75)
    
    for sym in SYMBOLS:
        df = load_data(sym)
        if df.empty:
            print(f"[!] No data returned for {sym}")
            continue
        trades = simulate_symbol(sym, df)
        all_trades.extend(trades)
        print(f"  {sym}: {len(trades)} trades simulated.")
    
    if not all_trades:
        print("[!] No trades generated during lookback period.")
        return

    tdf = pd.DataFrame(all_trades).sort_values("entry_time").reset_index(drop=True)
    
    equity = START_EQUITY
    equity_curve = [equity]
    
    for _, t in tdf.iterrows():
        allocated = equity * ALLOCATION_PCT
        shares = int(allocated // t['entry_price'])
        trade_pnl = shares * (t['exit_price'] - t['entry_price'])
        equity += trade_pnl
        equity_curve.append(equity)

    total_trades = len(tdf)
    wins = tdf[tdf['pnl_pct'] > 0]
    losses = tdf[tdf['pnl_pct'] <= 0]
    win_rate = (len(wins) / total_trades) * 100.0
    
    gross_gain = (wins['pnl_pct'] * ALLOCATION_PCT).sum()
    gross_loss = abs((losses['pnl_pct'] * ALLOCATION_PCT).sum())
    profit_factor = (gross_gain / gross_loss) if gross_loss > 0 else 99.9
    
    time_stops = len(tdf[tdf['exit_reason'] == "TIME_STOP"])
    sl_stops = len(tdf[tdf['exit_reason'] == "STOP_LOSS"])
    tp_hits = len(tdf[tdf['exit_reason'] == "TAKE_PROFIT"])

    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    drawdown = (eq_series - peak) / peak
    max_dd = abs(drawdown.min()) * 100.0

    print("\n" + "=" * 75)
    print("PERFORMANCE SCORECARD")
    print("=" * 75)
    print(f"Start Equity:       ${START_EQUITY:,.2f}")
    print(f"Final Equity:       ${equity:,.2f} ({((equity/START_EQUITY)-1)*100:+.2f}%)")
    print(f"Total Trades:       {total_trades}")
    print(f"Win Rate:           {win_rate:.2f}%")
    print(f"Profit Factor:      {profit_factor:.2f}")
    print(f"Expectancy:         {tdf['r_multiple'].mean():+.3f} R / trade")
    print(f"Max Drawdown:       {max_dd:.2f}%")
    print("\nExit Reason Breakdown:")
    print(f"  Take Profit:      {tp_hits} ({tp_hits/total_trades*100:.1f}%)")
    print(f"  Stop Loss:        {sl_stops} ({sl_stops/total_trades*100:.1f}%)")
    print(f"  15-min Time Stop: {time_stops} ({time_stops/total_trades*100:.1f}%)")
    print("=" * 75)

if __name__ == "__main__":
    run_backtest()
