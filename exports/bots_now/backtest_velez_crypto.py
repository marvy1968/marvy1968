#!/usr/bin/env python3
"""
Oliver Velez Scalper V3 — Crypto Multi-Symbol Concurrent Simulation
- Target Universe: BTC/USD, ETH/USD, SOL/USD
- Allocation: 25% Equity per position (Max 2 Concurrent Slots = 50% Exposure)
- Timeframe: 2-Minute Resampled Candles
- Friction Model: 0.15% Taker Fee + 0.05% Slippage per order (0.40% round-trip)
- Risk/Reward: 1:1.35 R:R with 1.1x ATR Stop Buffer
- Max Hold: 10 bars (20 minutes hard time-stop)
- Active Window: 12:00 - 21:00 UTC (Peak London/NY Volatility Window)
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
MAX_HOLD_BARS = 10
TARGET_R = 1.35
FEE_SLIPPAGE_PCT = 0.0020       # 0.20% per side (0.15% fee + 0.05% slippage)
LOOKBACK_DAYS = 21              # 21 days 1-minute history

crypto_client = CryptoHistoricalDataClient(API_KEY, API_SECRET)

def load_crypto_data(symbol: str) -> pd.DataFrame:
    print(f"[*] Fetching 1-min crypto data for {symbol} ({LOOKBACK_DAYS} days)...")
    end_dt = datetime.now(timezone.utc) - timedelta(minutes=5)
    start_dt = end_dt - timedelta(days=LOOKBACK_DAYS)
    
    req = CryptoBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start_dt,
        end=end_dt
    )
    try:
        bars = crypto_client.get_crypto_bars(req)
    except Exception as e:
        print(f"[!] Error fetching {symbol}: {e}")
        return pd.DataFrame()

    df = bars.df
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol)
        
    # Resample to 2-minute bars
    resampled = df.resample("2min").agg({
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

def check_signal(curr, prev) -> bool:
    t = curr.name.time()
    # High-liquidity volume window (12:00 - 21:00 UTC)
    in_vol_window = (t >= pd.to_datetime("12:00").time()) and (t <= pd.to_datetime("21:00").time())
    if not in_vol_window:
        return False

    is_bull = (curr['close'] > curr['ema20']) and (curr['ema20'] > curr['sma200'])
    rising_slope = curr['ema_slope'] > (0.04 * curr['atr14'])
    vol_expansion = curr['volume'] >= (1.15 * curr['vol_sma'])
    if not (is_bull and rising_slope and vol_expansion):
        return False

    bar_range = curr['high'] - curr['low']
    is_elephant = (bar_range > 1.8 * curr['avg_range']) and (curr['close'] > curr['open']) and \
                  ((curr['close'] - curr['low']) / (bar_range + 1e-6) > 0.75)
    is_pullback = (prev['low'] <= prev['ema20'] * 1.002) and (curr['close'] > prev['high']) and \
                  (curr['close'] > curr['ema20'])

    return is_elephant or is_pullback

def run_crypto_portfolio():
    data = {}
    for sym in SYMBOLS:
        df = load_crypto_data(sym)
        if not df.empty:
            data[sym] = df
            print(f"  Loaded {len(df)} 2-min bars for {sym}")

    if not data:
        print("[!] No data loaded.")
        return

    all_times = sorted(list(set.union(*[set(df.index) for df in data.values()])))
    equity = START_EQUITY
    equity_curve = [equity]
    open_positions = {}
    completed_trades = []

    for current_time in all_times:
        # 1. Manage active positions
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
            elif pos['bars_held'] >= MAX_HOLD_BARS:
                exit_price = bar['close'] * (1.0 - FEE_SLIPPAGE_PCT)
                exit_reason = "TIME_STOP"

            if exit_price is not None:
                pnl = pos['units'] * (exit_price - pos['entry_price'])
                equity += pnl
                equity_curve.append(equity)
                r_mult = (exit_price - pos['entry_price']) / max(pos['risk_per_unit'], 0.001)
                completed_trades.append({
                    "symbol": sym,
                    "entry_price": pos['entry_price'],
                    "exit_price": exit_price,
                    "units": pos['units'],
                    "pnl": pnl,
                    "r_multiple": r_mult,
                    "exit_reason": exit_reason
                })
                del open_positions[sym]

        # 2. Check for entry opportunities
        if len(open_positions) < MAX_CONCURRENT:
            for sym in SYMBOLS:
                if sym in open_positions or sym not in data:
                    continue
                if len(open_positions) >= MAX_CONCURRENT:
                    break

                df = data[sym]
                if current_time not in df.index:
                    continue

                pos_in_df = df.index.get_loc(current_time)
                if pos_in_df < 25 or pos_in_df >= len(df) - 1:
                    continue

                curr = df.iloc[pos_in_df]
                prev = df.iloc[pos_in_df - 1]

                if check_signal(curr, prev):
                    next_open = df.iloc[pos_in_df + 1]['open']
                    # Apply entry fee + slippage
                    entry_price = next_open * (1.0 + FEE_SLIPPAGE_PCT)
                    stop_loss = min(curr['low'], entry_price - (1.1 * curr['atr14']))
                    risk_per_unit = entry_price - stop_loss
                    if risk_per_unit <= 0:
                        continue
                    take_profit = entry_price + (risk_per_unit * TARGET_R)

                    allocated_capital = equity * ALLOCATION_PCT
                    units = allocated_capital / entry_price
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
        print("[!] No completed trades during window.")
        return

    tdf = pd.DataFrame(completed_trades)
    wins = tdf[tdf['pnl'] > 0]
    losses = tdf[tdf['pnl'] <= 0]
    pf = (wins['pnl'].sum() / abs(losses['pnl'].sum())) if abs(losses['pnl'].sum()) > 0 else 99.9

    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    drawdown = (eq_series - peak) / peak

    print("\n" + "=" * 65)
    print("CRYPTO VELEZ SCALPER (BTC/USD, ETH/USD, SOL/USD)")
    print("2 Concurrent Slots @ 25% Equity | 0.40% Round-Trip Drag")
    print("=" * 65)
    print(f"Start Equity:       ${START_EQUITY:,.2f}")
    print(f"Final Equity:       ${equity:,.2f} ({((equity/START_EQUITY)-1)*100:+.2f}%)")
    print(f"Total Trades:       {len(tdf)}")
    print(f"Win Rate:           {(len(wins)/len(tdf))*100:.2f}%")
    print(f"Profit Factor:      {pf:.2f}")
    print(f"Expectancy:         {tdf['r_multiple'].mean():+.3f} R / trade")
    print(f"Max Drawdown:       {abs(drawdown.min())*100:.2f}%")
    print(f"Net Profit (21d):   ${equity - START_EQUITY:,.2f}")
    print("\nExit Reason Breakdown:")
    for r, c in tdf['exit_reason'].value_counts().items():
        print(f"  {r:14s}: {c} ({c/len(tdf)*100:.1f}%)")
    print("=" * 65)

if __name__ == "__main__":
    run_crypto_portfolio()
