#!/usr/bin/env python3
"""
OANDA 4-Hour Macro Mean-Reversion Swing Engine (V2 Optimized)
- Changes:
  1. ADX(14) < 28 Regime Filter (Eliminates trending traps)
  2. Strict Target >= 1.20R (Fixes payoff asymmetry)
  3. Focus on EUR_GBP, EUR_USD, USD_CAD
- Friction: 1.5 Pips Round-Trip
- Sizing: 1.5% Risk per Trade
"""

import os
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
import oandapyV20
import oandapyV20.endpoints.instruments as instruments

env_path = Path("/home/moonrider1968/bots/.env")
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

OANDA_TOKEN = os.getenv("OANDA_API_KEY") or os.getenv("OANDA_ACCESS_TOKEN")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "REDACTED_OANDA_ACCOUNT")
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxpractice.oanda.com")
OANDA_ENV = "practice" if "fxpractice" in OANDA_URL else "live"

PAIRS = ["EUR_GBP", "EUR_USD", "USD_CAD"]
START_EQUITY = 100_000.0
RISK_PCT = 0.015
MAX_CONCURRENT = 3
MAX_HOLD_BARS = 60
CANDLE_COUNT = 4000

client = oandapyV20.API(access_token=OANDA_TOKEN, environment=OANDA_ENV)

def get_pip(pair: str) -> float:
    return 0.01 if "JPY" in pair else 0.0001

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    return 100 - (100 / (1 + rs))

def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    plus_dm = df['high'].diff()
    minus_dm = -df['low'].diff()
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)

    tr = np.maximum(
        df['high'] - df['low'],
        np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
    )
    atr = pd.Series(tr, index=df.index).ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-9))
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-9))
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9))
    return dx.ewm(alpha=1/period, adjust=False).mean()

def fetch_data(pair: str) -> pd.DataFrame:
    params = {"granularity": "H4", "count": CANDLE_COUNT, "price": "M"}
    r = instruments.InstrumentsCandles(instrument=pair, params=params)
    try:
        client.request(r)
    except Exception as e:
        print(f"[!] Error fetching {pair}: {e}")
        return pd.DataFrame()

    records = []
    for c in r.response.get("candles", []):
        if not c.get("complete", False):
            continue
        records.append({
            "time": pd.to_datetime(c["time"].split(".")[0] + "Z"),
            "open": float(c["mid"]["o"]),
            "high": float(c["mid"]["h"]),
            "low": float(c["mid"]["l"]),
            "close": float(c["mid"]["c"]),
            "volume": int(c["volume"])
        })

    df = pd.DataFrame(records).set_index("time")
    if df.empty:
        return df

    pip = get_pip(pair)
    df['symbol'] = pair
    df['pip'] = pip
    df['sma20'] = df['close'].rolling(20).mean()
    df['std20'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['sma20'] + (2.4 * df['std20'])
    df['bb_lower'] = df['sma20'] - (2.4 * df['std20'])
    df['rsi14'] = calculate_rsi(df['close'], period=14)
    df['adx'] = calculate_adx(df, period=14)

    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
    )
    df['atr14'] = df['tr'].rolling(14).mean()
    return df.dropna()

def run_simulation():
    data = {pair: fetch_data(pair) for pair in PAIRS}
    data = {k: v for k, v in data.items() if not v.empty}

    all_times = sorted(list(set.union(*[set(df.index) for df in data.values()])))
    equity = START_EQUITY
    equity_curve = [equity]
    open_positions = {}
    completed_trades = []

    for current_time in all_times:
        # 1. Manage Active Positions
        for pair in list(open_positions.keys()):
            pos = open_positions[pair]
            df = data[pair]
            if current_time not in df.index:
                continue

            bar = df.loc[current_time]
            pip = pos['pip']
            pos['bars_held'] += 1
            exit_price, exit_reason = None, None

            if pos['direction'] == 'LONG':
                if bar['low'] <= pos['stop_loss']:
                    exit_price = pos['stop_loss'] - (0.75 * pip)
                    exit_reason = "STOP_LOSS"
                elif bar['high'] >= pos['take_profit']:
                    exit_price = pos['take_profit'] - (0.75 * pip)
                    exit_reason = "TAKE_PROFIT"
                elif pos['bars_held'] >= MAX_HOLD_BARS:
                    exit_price = bar['close'] - (0.75 * pip)
                    exit_reason = "TIME_STOP"

                if exit_price is not None:
                    delta = exit_price - pos['entry_price']
                    pnl = pos['units'] * delta
                    equity += pnl
                    equity_curve.append(equity)
                    r_mult = delta / max(pos['risk_price'], 0.0001)
                    completed_trades.append({
                        "symbol": pair, "pnl": pnl, "r_mult": r_mult,
                        "exit_reason": exit_reason, "hold_hours": pos['bars_held'] * 4
                    })
                    del open_positions[pair]

            elif pos['direction'] == 'SHORT':
                if bar['high'] >= pos['stop_loss']:
                    exit_price = pos['stop_loss'] + (0.75 * pip)
                    exit_reason = "STOP_LOSS"
                elif bar['low'] <= pos['take_profit']:
                    exit_price = pos['take_profit'] + (0.75 * pip)
                    exit_reason = "TAKE_PROFIT"
                elif pos['bars_held'] >= MAX_HOLD_BARS:
                    exit_price = bar['close'] + (0.75 * pip)
                    exit_reason = "TIME_STOP"

                if exit_price is not None:
                    delta = pos['entry_price'] - exit_price
                    pnl = pos['units'] * delta
                    equity += pnl
                    equity_curve.append(equity)
                    r_mult = delta / max(pos['risk_price'], 0.0001)
                    completed_trades.append({
                        "symbol": pair, "pnl": pnl, "r_mult": r_mult,
                        "exit_reason": exit_reason, "hold_hours": pos['bars_held'] * 4
                    })
                    del open_positions[pair]

        # 2. Check Entries
        if len(open_positions) < MAX_CONCURRENT:
            for pair in PAIRS:
                if pair in open_positions or pair not in data:
                    continue
                if len(open_positions) >= MAX_CONCURRENT:
                    break

                df = data[pair]
                if current_time not in df.index:
                    continue

                pos_idx = df.index.get_loc(current_time)
                if pos_idx < 25 or pos_idx >= len(df) - 1:
                    continue

                curr, prev = df.iloc[pos_idx], df.iloc[pos_idx - 1]
                pip = curr['pip']
                risk_dollars = equity * RISK_PCT

                # ADX Range Filter: Skip strong trending markets
                if curr['adx'] >= 28.0:
                    continue

                # Long: Pierced lower BB + Oversold RSI + Close back inside
                if (prev['low'] < prev['bb_lower']) and (prev['rsi14'] <= 32.0) and (curr['close'] > curr['bb_lower']):
                    entry = df.iloc[pos_idx + 1]['open'] + (0.75 * pip)
                    sl = prev['low'] - (1.2 * curr['atr14'])
                    risk_price = entry - sl
                    if risk_price <= 0:
                        continue
                    # Target: Higher of 20-SMA or 1.25R
                    tp = max(curr['sma20'], entry + (1.25 * risk_price))

                    units = int(risk_dollars / risk_price)
                    if units >= 500:
                        open_positions[pair] = {
                            "direction": "LONG", "entry_price": entry, "stop_loss": sl,
                            "take_profit": tp, "risk_price": risk_price, "units": units,
                            "pip": pip, "bars_held": 0
                        }

                # Short: Pierced upper BB + Overbought RSI + Close back inside
                elif (prev['high'] > prev['bb_upper']) and (prev['rsi14'] >= 68.0) and (curr['close'] < curr['bb_upper']):
                    entry = df.iloc[pos_idx + 1]['open'] - (0.75 * pip)
                    sl = prev['high'] + (1.2 * curr['atr14'])
                    risk_price = sl - entry
                    if risk_price <= 0:
                        continue
                    tp = min(curr['sma20'], entry - (1.25 * risk_price))

                    units = int(risk_dollars / risk_price)
                    if units >= 500:
                        open_positions[pair] = {
                            "direction": "SHORT", "entry_price": entry, "stop_loss": sl,
                            "take_profit": tp, "risk_price": risk_price, "units": units,
                            "pip": pip, "bars_held": 0
                        }

    if not completed_trades:
        print("[!] No completed trades.")
        return

    tdf = pd.DataFrame(completed_trades)
    wins = tdf[tdf['pnl'] > 0]
    losses = tdf[tdf['pnl'] <= 0]
    pf = (wins['pnl'].sum() / abs(losses['pnl'].sum())) if abs(losses['pnl'].sum()) > 0 else 99.9

    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    drawdown = (eq_series - peak) / peak

    print("\n" + "=" * 68)
    print("OANDA 4-HOUR MEAN-REVERSION SWING SCORECARD (V2 OPTIMIZED)")
    print("EUR_GBP, EUR_USD, USD_CAD | ADX < 28 Filter | 1.25R Min Target")
    print("=" * 68)
    print(f"Start Equity:       ${START_EQUITY:,.2f}")
    print(f"Final Equity:       ${equity:,.2f} ({((equity/START_EQUITY)-1)*100:+.2f}%)")
    print(f"Total Trades:       {len(tdf)}")
    print(f"Win Rate:           {(len(wins)/len(tdf))*100:.2f}%")
    print(f"Profit Factor:      {pf:.2f}")
    print(f"Expectancy:         {tdf['r_mult'].mean():+.3f} R / trade")
    print(f"Max Drawdown:       {abs(drawdown.min())*100:.2f}%")
    print(f"Net Profit:         ${equity - START_EQUITY:,.2f}")
    print(f"Avg Hold Time:      {tdf['hold_hours'].mean():.1f} hours ({tdf['hold_hours'].mean()/24:.1f} days)")
    print("\nExit Breakdown:")
    for r, c in tdf['exit_reason'].value_counts().items():
        print(f"  {r:14s}: {c} ({c/len(tdf)*100:.1f}%)")
    print("=" * 68)

if __name__ == "__main__":
    run_simulation()
