#!/usr/bin/env python3
"""
Oliver Velez Crypto Multi-Timeframe Benchmarking: 15-Min vs 1-Hour
- Symbols: BTC/USD, ETH/USD, SOL/USD
- Concurrency: Max 2 slots @ 25% equity each
- Drag Model: 0.20% per side (0.15% taker fee + 0.05% slippage = 0.40% round-trip)
- Lookback: 60 Days
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame

API_KEY = os.getenv("ALPACA_API_KEY", "")
API_SECRET = os.getenv("ALPACA_SECRET_KEY", "")
SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD"]

START_EQUITY = 100_000.0
ALLOCATION_PCT = 0.25
MAX_CONCURRENT = 2
FEE_SLIPPAGE_PCT = 0.0020       # 0.20% per side
LOOKBACK_DAYS = 60

crypto_client = CryptoHistoricalDataClient(API_KEY, API_SECRET)

def load_resampled_data(symbol: str, resample_freq: str) -> pd.DataFrame:
    end_dt = datetime.now(timezone.utc) - timedelta(minutes=5)
    start_dt = end_dt - timedelta(days=LOOKBACK_DAYS)
    
    req = CryptoBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start_dt,
        end=end_dt
    )
    bars = crypto_client.get_crypto_bars(req)
    df = bars.df
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol)
        
    resampled = df.resample(resample_freq).agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna()

    resampled['symbol'] = symbol
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

def check_velez_signal(curr, prev) -> bool:
    is_bull = (curr['close'] > curr['ema20']) and (curr['ema20'] > curr['sma200'])
    rising_slope = curr['ema_slope'] > (0.01 * curr['atr14'])
    vol_expansion = curr['volume'] >= (1.05 * curr['vol_sma'])
    if not (is_bull and rising_slope and vol_expansion):
        return False

    bar_range = curr['high'] - curr['low']
    # Igniting/Elephant Bar (Range expansion with strong close)
    is_elephant = (bar_range > 1.5 * curr['avg_range']) and (curr['close'] > curr['open']) and \
                  ((curr['close'] - curr['low']) / (bar_range + 1e-6) > 0.70)
    # 20 EMA Pullback bounce
    is_pullback = (prev['low'] <= prev['ema20'] * 1.003) and (curr['close'] > prev['high']) and \
                  (curr['close'] > curr['ema20'])

    return is_elephant or is_pullback

def simulate_engine(resample_freq: str, max_hold_bars: int, target_r: float, atr_mult: float) -> dict:
    data = {}
    for sym in SYMBOLS:
        df = load_resampled_data(sym, resample_freq)
        if not df.empty:
            data[sym] = df

    all_times = sorted(list(set.union(*[set(df.index) for df in data.values()])))
    equity = START_EQUITY
    equity_curve = [equity]
    open_positions = {}
    completed_trades = []

    for current_time in all_times:
        # Manage open slots
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            df = data[sym]
            if current_time not in df.index:
                continue

            bar = df.loc[current_time]
            pos['bars_held'] += 1
            exit_price, exit_reason = None, None

            if bar['low'] <= pos['stop_loss']:
                exit_price = pos['stop_loss'] * (1.0 - FEE_SLIPPAGE_PCT)
                exit_reason = "STOP_LOSS"
            elif bar['high'] >= pos['take_profit']:
                exit_price = pos['take_profit'] * (1.0 - FEE_SLIPPAGE_PCT)
                exit_reason = "TAKE_PROFIT"
            elif pos['bars_held'] >= max_hold_bars:
                exit_price = bar['close'] * (1.0 - FEE_SLIPPAGE_PCT)
                exit_reason = "TIME_STOP"

            if exit_price is not None:
                pnl = pos['units'] * (exit_price - pos['entry_price'])
                equity += pnl
                equity_curve.append(equity)
                r_mult = (exit_price - pos['entry_price']) / max(pos['risk_per_unit'], 0.001)
                completed_trades.append({
                    "symbol": sym,
                    "pnl": pnl,
                    "r_mult": r_mult,
                    "exit_reason": exit_reason
                })
                del open_positions[sym]

        # Scan new entries
        if len(open_positions) < MAX_CONCURRENT:
            for sym in SYMBOLS:
                if sym in open_positions or sym not in data:
                    continue
                if len(open_positions) >= MAX_CONCURRENT:
                    break

                df = data[sym]
                if current_time not in df.index:
                    continue

                pos_idx = df.index.get_loc(current_time)
                if pos_idx < 25 or pos_idx >= len(df) - 1:
                    continue

                curr, prev = df.iloc[pos_idx], df.iloc[pos_idx - 1]
                if check_velez_signal(curr, prev):
                    next_open = df.iloc[pos_idx + 1]['open']
                    entry_price = next_open * (1.0 + FEE_SLIPPAGE_PCT)
                    stop_loss = min(curr['low'], entry_price - (atr_mult * curr['atr14']))
                    risk_per_unit = entry_price - stop_loss
                    if risk_per_unit <= 0:
                        continue
                    take_profit = entry_price + (risk_per_unit * target_r)

                    allocated = equity * ALLOCATION_PCT
                    units = allocated / entry_price
                    if units <= 0:
                        continue

                    open_positions[sym] = {
                        "entry_time": current_time,
                        "entry_price": entry_price,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "risk_per_unit": risk_per_unit,
                        "units": units,
                        "bars_held": 0
                    }

    if not completed_trades:
        return {"trades": 0}

    tdf = pd.DataFrame(completed_trades)
    wins = tdf[tdf['pnl'] > 0]
    losses = tdf[tdf['pnl'] <= 0]
    pf = (wins['pnl'].sum() / abs(losses['pnl'].sum())) if abs(losses['pnl'].sum()) > 0 else 0.0

    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    drawdown = (eq_series - peak) / peak

    return {
        "trades": len(tdf),
        "win_rate": (len(wins) / len(tdf)) * 100.0,
        "profit_factor": pf,
        "expectancy_r": tdf['r_mult'].mean(),
        "final_equity": equity,
        "return_pct": ((equity / START_EQUITY) - 1) * 100.0,
        "max_drawdown": abs(drawdown.min()) * 100.0,
        "exit_counts": tdf['exit_reason'].value_counts().to_dict()
    }

if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING CRYPTO MULTI-TIMEFRAME BENCHMARK (BTC, ETH, SOL | 60 DAYS)")
    print("Drag: 0.40% Round-Trip | Sizing: 2 slots @ 25% Equity")
    print("=" * 70)

    print("\n[1/2] Simulating 15-Minute Engine (16 bars / 4h hold, 2.0R target, 1.2x ATR stop)...")
    res_15m = simulate_engine(resample_freq="15min", max_hold_bars=16, target_r=2.0, atr_mult=1.2)

    print("\n[2/2] Simulating 1-Hour Engine (16 bars / 16h hold, 2.5R target, 1.4x ATR stop)...")
    res_1h = simulate_engine(resample_freq="1h", max_hold_bars=16, target_r=2.5, atr_mult=1.4)

    print("\n" + "=" * 70)
    print("SCORECARD COMPARISON")
    print("=" * 70)
    print(f"{'Metric':<24} | {'15-Minute Bars':<20} | {'1-Hour Bars':<20}")
    print("-" * 70)
    print(f"{'Total Trades':<24} | {res_15m.get('trades', 0):<20} | {res_1h.get('trades', 0):<20}")
    print(f"{'Win Rate':<24} | {res_15m.get('win_rate', 0):.2f}%{'':<14} | {res_1h.get('win_rate', 0):.2f}%{'':<14}")
    print(f"{'Profit Factor':<24} | {res_15m.get('profit_factor', 0):.2f}{'':<16} | {res_1h.get('profit_factor', 0):.2f}{'':<16}")
    print(f"{'Expectancy (R)':<24} | {res_15m.get('expectancy_r', 0):+.3f} R{'':<13} | {res_1h.get('expectancy_r', 0):+.3f} R{'':<13}")
    print(f"{'Net Return':<24} | {res_15m.get('return_pct', 0):+.2f}%{'':<14} | {res_1h.get('return_pct', 0):+.2f}%{'':<14}")
    print(f"{'Max Drawdown':<24} | {res_15m.get('max_drawdown', 0):.2f}%{'':<15} | {res_1h.get('max_drawdown', 0):.2f}%{'':<15}")
    print("-" * 70)
    print("Exit Distribution (15-Min):", res_15m.get('exit_counts', {}))
    print("Exit Distribution (1-Hour):", res_1h.get('exit_counts', {}))
    print("=" * 70)
