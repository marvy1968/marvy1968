#!/usr/bin/env python3
import os
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
ALLOCATION_PCT = 0.25
MAX_HOLD_BARS = 10              # 20 minutes max hold
TARGET_R = 1.35                 # Scalper target
SLIPPAGE = 0.01
LOOKBACK_DAYS = 30

data_client = StockHistoricalDataClient(API_KEY, API_SECRET)

def load_data(symbol: str) -> pd.DataFrame:
    end_dt = datetime.now(timezone.utc) - timedelta(minutes=20)
    start_dt = end_dt - timedelta(days=LOOKBACK_DAYS)
    
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start_dt,
        end=end_dt,
        feed=DataFeed.IEX
    )
    bars = data_client.get_stock_bars(req)
    df = bars.df
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol)
    
    resampled = df.resample("2min").agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna()

    resampled['ema20'] = resampled['close'].ewm(span=20, adjust=False).mean()
    resampled['sma200'] = resampled['close'].rolling(window=200, min_periods=20).mean()
    resampled['tr'] = np.maximum(
        resampled['high'] - resampled['low'],
        np.maximum(abs(resampled['high'] - resampled['close'].shift(1)), abs(resampled['low'] - resampled['close'].shift(1)))
    )
    resampled['atr14'] = resampled['tr'].rolling(window=14).mean()
    resampled['avg_range'] = (resampled['high'] - resampled['low']).rolling(window=20).mean()
    resampled['vol_sma'] = resampled['volume'].rolling(window=20).mean()
    resampled['ema_slope'] = resampled['ema20'] - resampled['ema20'].shift(3)
    return resampled.dropna()

def simulate_symbol(symbol: str, df: pd.DataFrame) -> list:
    trades = []
    in_position = False
    entry_idx, entry_price, stop_loss, initial_risk, take_profit = 0, 0.0, 0.0, 0.0, 0.0

    n = len(df)
    for i in range(25, n - 1):
        curr, prev = df.iloc[i], df.iloc[i - 1]
        t = df.index[i].time()
        
        # Session Filter: 09:45-11:30 & 14:00-15:30 EST
        session_valid = (
            (t >= pd.to_datetime("13:45").time() and t <= pd.to_datetime("15:30").time()) or
            (t >= pd.to_datetime("18:00").time() and t <= pd.to_datetime("19:30").time())
        )
        
        if not in_position:
            if not session_valid:
                continue
            
            is_bull = (curr['close'] > curr['ema20']) and (curr['ema20'] > curr['sma200'])
            rising_slope = curr['ema_slope'] > (0.05 * curr['atr14'])
            vol_expansion = curr['volume'] >= (1.2 * curr['vol_sma'])
            
            if not (is_bull and rising_slope and vol_expansion):
                continue

            bar_range = curr['high'] - curr['low']
            is_elephant = (bar_range > 1.8 * curr['avg_range']) and (curr['close'] > curr['open']) and \
                          ((curr['close'] - curr['low']) / (bar_range + 1e-6) > 0.75)
            is_pullback = (prev['low'] <= prev['ema20'] * 1.002) and (curr['close'] > prev['high']) and \
                          (curr['close'] > curr['ema20'])

            if is_elephant or is_pullback:
                in_position = True
                entry_idx = i + 1
                entry_price = df.iloc[i + 1]['open'] + SLIPPAGE
                stop_loss = min(curr['low'], entry_price - (1.1 * curr['atr14'])) - SLIPPAGE
                initial_risk = entry_price - stop_loss
                take_profit = entry_price + (initial_risk * TARGET_R)
                continue

        if in_position:
            bars_held = i - entry_idx + 1
            exit_price, exit_reason = None, None
            
            if curr['low'] <= stop_loss:
                exit_price = stop_loss - SLIPPAGE
                exit_reason = "STOP_LOSS"
            elif curr['high'] >= take_profit:
                exit_price = take_profit - SLIPPAGE
                exit_reason = "TAKE_PROFIT"
            elif bars_held >= MAX_HOLD_BARS:
                exit_price = curr['close'] - SLIPPAGE
                exit_reason = "TIME_STOP"

            if exit_price is not None:
                pnl_per_share = exit_price - entry_price
                r_mult = pnl_per_share / max(initial_risk, 0.01)
                trades.append({
                    "symbol": symbol, "bars_held": bars_held, "entry_price": entry_price,
                    "exit_price": exit_price, "pnl_pct": (exit_price / entry_price) - 1.0,
                    "r_multiple": r_mult, "exit_reason": exit_reason
                })
                in_position = False

    return trades

if __name__ == "__main__":
    all_trades = []
    for sym in SYMBOLS:
        df = load_data(sym)
        if not df.empty:
            trades = simulate_symbol(sym, df)
            all_trades.extend(trades)
            print(f"  {sym}: {len(trades)} trades simulated.")

    if all_trades:
        tdf = pd.DataFrame(all_trades)
        wins, losses = tdf[tdf['pnl_pct'] > 0], tdf[tdf['pnl_pct'] <= 0]
        gross_gain = (wins['pnl_pct'] * ALLOCATION_PCT).sum()
        gross_loss = abs((losses['pnl_pct'] * ALLOCATION_PCT).sum())
        pf = (gross_gain / gross_loss) if gross_loss > 0 else 99.9
        
        print("\n" + "=" * 65)
        print(f"V3 SCORECARD (Trades: {len(tdf)})")
        print(f"Win Rate:      {(len(wins)/len(tdf))*100:.2f}%")
        print(f"Profit Factor: {pf:.2f}")
        print(f"Expectancy:    {tdf['r_multiple'].mean():+.3f} R / trade")
        print("Exit Distribution:")
        for r, c in tdf['exit_reason'].value_counts().items():
            print(f"  {r:14s}: {c} ({c/len(tdf)*100:.1f}%)")
        print("=" * 65)
