#!/usr/bin/env python3
"""
Oliver Velez Forex Momentum Engine — OANDA v20 Backtest
- Reads credentials automatically from .env
- Pairs: EUR_USD, GBP_USD, USD_JPY
- Granularity: M15 (15-Minute Candles)
- Liquidity Window: London / NY Overlap (07:30 - 16:30 UTC)
- Risk Model: 1:1.5 R:R with 1.2x ATR Stop Buffer
- Max Hold: 12 bars (3 hours hard time-stop)
- Friction Model: 1.2 Pips Spread / Slippage per round-trip
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime, timezone
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
ALLOCATION_PCT = 0.25          # 25% equity per setup
MAX_CONCURRENT = 2
MAX_HOLD_BARS = 12             # 12 x 15-min bars = 3 hours max
TARGET_R = 1.50
CANDLE_COUNT = 2500            # ~26 days of M15 trading data

if not OANDA_TOKEN:
    print("[!] Error: OANDA_API_KEY / OANDA_ACCESS_TOKEN not found in .env.")
    sys.exit(1)

client = oandapyV20.API(access_token=OANDA_TOKEN, environment=OANDA_ENV)

def get_pip_factor(pair: str) -> float:
    return 0.01 if "JPY" in pair else 0.0001

def fetch_oanda_candles(pair: str) -> pd.DataFrame:
    print(f"[*] Ingesting {CANDLE_COUNT} M15 candles for {pair} ({OANDA_ENV})...")
    params = {
        "granularity": "M15",
        "count": CANDLE_COUNT,
        "price": "M"
    }
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
        time_str = c["time"].split(".")[0] + "Z"
        mid = c["mid"]
        records.append({
            "time": pd.to_datetime(time_str),
            "open": float(mid["o"]),
            "high": float(mid["h"]),
            "low": float(mid["l"]),
            "close": float(mid["c"]),
            "volume": int(c["volume"])
        })

    df = pd.DataFrame(records).set_index("time")
    if df.empty:
        return df

    df['symbol'] = pair
    df['pip'] = get_pip_factor(pair)
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['sma200'] = df['close'].rolling(window=200, min_periods=20).mean()
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
    )
    df['atr14'] = df['tr'].rolling(window=14).mean()
    df['avg_range'] = (df['high'] - df['low']).rolling(window=20).mean()
    df['vol_sma'] = df['volume'].rolling(window=20).mean()
    df['ema_slope'] = df['ema20'] - df['ema20'].shift(3)
    return df.dropna()

def check_velez_fx_signal(curr, prev) -> bool:
    t = curr.name.time()
    # High liquidity FX session: 07:30 to 16:30 UTC (London Open to NY Midday)
    in_session = (t >= pd.to_datetime("07:30").time()) and (t <= pd.to_datetime("16:30").time())
    if not in_session:
        return False

    is_bull = (curr['close'] > curr['ema20']) and (curr['ema20'] > curr['sma200'])
    rising_slope = curr['ema_slope'] > (0.02 * curr['atr14'])
    vol_confirm = curr['volume'] >= (1.05 * curr['vol_sma'])
    if not (is_bull and rising_slope and vol_confirm):
        return False

    bar_range = curr['high'] - curr['low']
    # Elephant Bar: > 1.6x average candle range, closing in upper 25% of range
    is_elephant = (bar_range > 1.6 * curr['avg_range']) and (curr['close'] > curr['open']) and \
                  ((curr['close'] - curr['low']) / (bar_range + 1e-6) > 0.75)

    # 20 EMA Pullback Bounce
    is_pullback = (prev['low'] <= prev['ema20'] * 1.0005) and (curr['close'] > prev['high']) and \
                  (curr['close'] > curr['ema20'])

    return is_elephant or is_pullback

def run_simulation():
    data = {}
    for pair in PAIRS:
        df = fetch_oanda_candles(pair)
        if not df.empty:
            data[pair] = df

    if not data:
        print("[!] No candle data available to simulate.")
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
                price_delta = exit_price - pos['entry_price']
                if "JPY" in pair:
                    pnl = pos['units'] * (price_delta / exit_price)
                else:
                    pnl = pos['units'] * price_delta

                equity += pnl
                equity_curve.append(equity)
                r_mult = price_delta / max(pos['risk_price'], 0.0001)
                completed_trades.append({
                    "symbol": pair,
                    "entry_price": pos['entry_price'],
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "r_mult": r_mult,
                    "exit_reason": exit_reason
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
                if check_velez_fx_signal(curr, prev):
                    pip = curr['pip']
                    next_open = df.iloc[pos_idx + 1]['open']
                    entry_price = next_open + (0.6 * pip)  # 0.6 pip entry spread
                    stop_loss = min(curr['low'], entry_price - (1.2 * curr['atr14'])) - (0.4 * pip)
                    risk_price = entry_price - stop_loss
                    if risk_price <= 0:
                        continue
                    take_profit = entry_price + (risk_price * TARGET_R)

                    if "JPY" in pair:
                        units = int((equity * ALLOCATION_PCT) * entry_price)
                    else:
                        units = int((equity * ALLOCATION_PCT) / entry_price)

                    if units < 100:
                        continue

                    open_positions[pair] = {
                        "entry_time": current_time,
                        "entry_price": entry_price,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "risk_price": risk_price,
                        "units": units,
                        "pip": pip,
                        "bars_held": 0
                    }

    if not completed_trades:
        print("[!] No completed trades during sample period.")
        return

    tdf = pd.DataFrame(completed_trades)
    wins = tdf[tdf['pnl'] > 0]
    losses = tdf[tdf['pnl'] <= 0]
    pf = (wins['pnl'].sum() / abs(losses['pnl'].sum())) if abs(losses['pnl'].sum()) > 0 else 99.9

    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    drawdown = (eq_series - peak) / peak

    print("\n" + "=" * 68)
    print("OANDA FOREX VELEZ SCALPER (EUR_USD, GBP_USD, USD_JPY)")
    print("M15 Candles | London/NY Window | 1.2-Pip Spread Included")
    print("=" * 68)
    print(f"Start Equity:       ${START_EQUITY:,.2f}")
    print(f"Final Equity:       ${equity:,.2f} ({((equity/START_EQUITY)-1)*100:+.2f}%)")
    print(f"Total Trades:       {len(tdf)}")
    print(f"Win Rate:           {(len(wins)/len(tdf))*100:.2f}%")
    print(f"Profit Factor:      {pf:.2f}")
    print(f"Expectancy:         {tdf['r_mult'].mean():+.3f} R / trade")
    print(f"Max Drawdown:       {abs(drawdown.min())*100:.2f}%")
    print(f"Net Profit:         ${equity - START_EQUITY:,.2f}")
    print("\nExit Reason Distribution:")
    for r, c in tdf['exit_reason'].value_counts().items():
        print(f"  {r:14s}: {c} ({c/len(tdf)*100:.1f}%)")
    print("=" * 68)

if __name__ == "__main__":
    run_simulation()
