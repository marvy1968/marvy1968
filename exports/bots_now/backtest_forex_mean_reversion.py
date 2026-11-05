#!/usr/bin/env python3
"""
OANDA Forex Statistical Mean-Reversion Scalper (M5)
- Strategy: Bollinger Band (20, 2.2 std) + RSI(7) Exhaustion Fade
- Universe: EUR_USD, GBP_USD, USD_JPY
- Timeframe: M5 (5-Minute Candles)
- Target: 20-SMA Midline Touch
- Stop: 1.2x ATR(14)
- Cost Model: 1.2 Pips Round-Trip Spread & Slippage
- Sizing: Fixed 1% Account Risk per Trade (Max 2 Concurrent Slots)
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

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY"]
START_EQUITY = 100_000.0
RISK_PCT = 0.01          # 1% true account risk
MAX_CONCURRENT = 2
MAX_HOLD_BARS = 24       # 24 x 5m = 2 hours max hold
CANDLE_COUNT = 3500      # ~12 days of 5-minute bars

if not OANDA_TOKEN:
    print("[!] OANDA API Key missing from .env")
    sys.exit(1)

client = oandapyV20.API(access_token=OANDA_TOKEN, environment=OANDA_ENV)

def get_pip(pair: str) -> float:
    return 0.01 if "JPY" in pair else 0.0001

def calculate_rsi(series: pd.Series, period: int = 7) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    return 100 - (100 / (1 + rs))

def fetch_candles(pair: str) -> pd.DataFrame:
    print(f"[*] Ingesting {CANDLE_COUNT} M5 candles for {pair}...")
    params = {"granularity": "M5", "count": CANDLE_COUNT, "price": "M"}
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
        mid = c["mid"]
        records.append({
            "time": pd.to_datetime(c["time"].split(".")[0] + "Z"),
            "open": float(mid["o"]),
            "high": float(mid["h"]),
            "low": float(mid["l"]),
            "close": float(mid["c"]),
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
    df['bb_upper'] = df['sma20'] + (2.2 * df['std20'])
    df['bb_lower'] = df['sma20'] - (2.2 * df['std20'])
    df['rsi7'] = calculate_rsi(df['close'], period=7)

    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
    )
    df['atr14'] = df['tr'].rolling(14).mean()
    return df.dropna()

def run_simulation():
    data = {}
    for pair in PAIRS:
        df = fetch_candles(pair)
        if not df.empty:
            data[pair] = df

    if not data:
        print("[!] No data available.")
        return

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
                    exit_price = pos['stop_loss'] - (0.6 * pip)
                    exit_reason = "STOP_LOSS"
                elif bar['high'] >= pos['take_profit']:
                    exit_price = pos['take_profit'] - (0.6 * pip)
                    exit_reason = "TAKE_PROFIT"
                elif pos['bars_held'] >= MAX_HOLD_BARS:
                    exit_price = bar['close'] - (0.6 * pip)
                    exit_reason = "TIME_STOP"

                if exit_price is not None:
                    delta = exit_price - pos['entry_price']
                    pnl = pos['units'] * (delta / exit_price) if "JPY" in pair else pos['units'] * delta
                    equity += pnl
                    equity_curve.append(equity)
                    completed_trades.append({
                        "symbol": pair, "dir": "LONG", "pnl": pnl, "exit_reason": exit_reason
                    })
                    del open_positions[pair]

            elif pos['direction'] == 'SHORT':
                if bar['high'] >= pos['stop_loss']:
                    exit_price = pos['stop_loss'] + (0.6 * pip)
                    exit_reason = "STOP_LOSS"
                elif bar['low'] <= pos['take_profit']:
                    exit_price = pos['take_profit'] + (0.6 * pip)
                    exit_reason = "TAKE_PROFIT"
                elif pos['bars_held'] >= MAX_HOLD_BARS:
                    exit_price = bar['close'] + (0.6 * pip)
                    exit_reason = "TIME_STOP"

                if exit_price is not None:
                    delta = pos['entry_price'] - exit_price
                    pnl = pos['units'] * (delta / exit_price) if "JPY" in pair else pos['units'] * delta
                    equity += pnl
                    equity_curve.append(equity)
                    completed_trades.append({
                        "symbol": pair, "dir": "SHORT", "pnl": pnl, "exit_reason": exit_reason
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

                curr = df.iloc[pos_idx]
                pip = curr['pip']
                next_open = df.iloc[pos_idx + 1]['open']
                risk_dollars = equity * RISK_PCT

                # Long Signal: Pierced lower BB + Oversold RSI
                if (curr['close'] < curr['bb_lower']) and (curr['rsi7'] <= 25.0):
                    entry = next_open + (0.6 * pip)
                    sl = entry - (1.2 * curr['atr14'])
                    tp = curr['sma20']  # Target the mean
                    risk_per_unit = entry - sl
                    if risk_per_unit <= 0 or tp <= entry:
                        continue

                    units = int((risk_dollars * entry) / risk_per_unit) if "JPY" in pair else int(risk_dollars / risk_per_unit)
                    if units >= 100:
                        open_positions[pair] = {
                            "direction": "LONG", "entry_price": entry, "stop_loss": sl,
                            "take_profit": tp, "units": units, "pip": pip, "bars_held": 0
                        }

                # Short Signal: Pierced upper BB + Overbought RSI
                elif (curr['close'] > curr['bb_upper']) and (curr['rsi7'] >= 75.0):
                    entry = next_open - (0.6 * pip)
                    sl = entry + (1.2 * curr['atr14'])
                    tp = curr['sma20']  # Target the mean
                    risk_per_unit = sl - entry
                    if risk_per_unit <= 0 or tp >= entry:
                        continue

                    units = int((risk_dollars * entry) / risk_per_unit) if "JPY" in pair else int(risk_dollars / risk_per_unit)
                    if units >= 100:
                        open_positions[pair] = {
                            "direction": "SHORT", "entry_price": entry, "stop_loss": sl,
                            "take_profit": tp, "units": units, "pip": pip, "bars_held": 0
                        }

    if not completed_trades:
        print("[!] No completed trades.")
        return

    tdf = pd.DataFrame(completed_trades)
    wins = tdf[tdf['pnl'] > 0]
    losses = tdf[tdf['pnl'] <= 0]
    pf = (wins['pnl'].sum() / abs(losses['pnl'].sum())) if abs(losses['pnl'].sum()) > 0 else 0.0

    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    drawdown = (eq_series - peak) / peak

    print("\n" + "=" * 68)
    print("OANDA FOREX STATISTICAL MEAN-REVERSION SCALPER (M5)")
    print("Bollinger (20, 2.2) + RSI(7) | Midline Exit | 1.2-Pip Spread Included")
    print("=" * 68)
    print(f"Start Equity:       ${START_EQUITY:,.2f}")
    print(f"Final Equity:       ${equity:,.2f} ({((equity/START_EQUITY)-1)*100:+.2f}%)")
    print(f"Total Trades:       {len(tdf)}")
    print(f"Win Rate:           {(len(wins)/len(tdf))*100:.2f}%")
    print(f"Profit Factor:      {pf:.2f}")
    print(f"Max Drawdown:       {abs(drawdown.min())*100:.2f}%")
    print(f"Net Profit:         ${equity - START_EQUITY:,.2f}")
    print("\nExit Breakdown:")
    for r, c in tdf['exit_reason'].value_counts().items():
        print(f"  {r:14s}: {c} ({c/len(tdf)*100:.1f}%)")
    print("=" * 68)

if __name__ == "__main__":
    run_simulation()
