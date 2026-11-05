#!/usr/bin/env python3
"""
OANDA Macro Mean-Reversion Swing Engine (H4)
- Strategy: Statistical Exhaustion Fade (Bollinger 20, 2.5 std + RSI(14))
- Universe: EUR_USD, GBP_USD, USD_CAD, EUR_GBP
- Timeframe: H4 (4-Hour Bars)
- Entry Trigger: Pierces 2.5 std band, RSI extreme, closes back inside band
- Exit Target: 20-SMA Midline Touch
- Stop Loss: 1.5x ATR(14) beyond extreme wick
- Friction: 1.5 Pips Round-Trip Spread Deducted
- Risk Management: 1.5% Equity Risk Per Trade (Max 3 Concurrent Slots)
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

PAIRS = ["EUR_USD", "GBP_USD", "USD_CAD", "EUR_GBP"]
START_EQUITY = 100_000.0
RISK_PCT = 0.015               # 1.5% true account risk
MAX_CONCURRENT = 3             # Max 3 concurrent positions
MAX_HOLD_BARS = 60             # 10 trading days max hold
CANDLE_COUNT = 4000            # ~2.5 years of 4-hour candles

if not OANDA_TOKEN:
    print("[!] OANDA API Key missing from .env")
    sys.exit(1)

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

def fetch_h4_candles(pair: str) -> pd.DataFrame:
    print(f"[*] Ingesting {CANDLE_COUNT} H4 candles for {pair} ({OANDA_ENV})...")
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
    df['bb_upper'] = df['sma20'] + (2.5 * df['std20'])
    df['bb_lower'] = df['sma20'] - (2.5 * df['std20'])
    df['rsi14'] = calculate_rsi(df['close'], period=14)

    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
    )
    df['atr14'] = df['tr'].rolling(14).mean()
    return df.dropna()

def check_swing_signal(curr, prev) -> tuple:
    # Bullish Fade: Previous pierced lower band + oversold RSI, current closed back inside band
    if (prev['low'] < prev['bb_lower']) and (prev['rsi14'] <= 30.0) and (curr['close'] > curr['bb_lower']):
        return "LONG", prev['low']

    # Bearish Fade: Previous pierced upper band + overbought RSI, current closed back inside band
    if (prev['high'] > prev['bb_upper']) and (prev['rsi14'] >= 70.0) and (curr['close'] < curr['bb_upper']):
        return "SHORT", prev['high']

    return "NONE", 0.0

def run_simulation():
    data = {pair: fetch_h4_candles(pair) for pair in PAIRS}
    data = {k: v for k, v in data.items() if not v.empty}

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

            # Dynamic Take-Profit Target: The 20-SMA Midline
            current_target = bar['sma20']

            if pos['direction'] == 'LONG':
                if bar['low'] <= pos['stop_loss']:
                    exit_price = pos['stop_loss'] - (0.75 * pip)
                    exit_reason = "STOP_LOSS"
                elif bar['high'] >= current_target:
                    exit_price = current_target - (0.75 * pip)
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
                        "symbol": pair, "dir": "LONG", "pnl": pnl, "r_mult": r_mult,
                        "exit_reason": exit_reason, "hold_hours": pos['bars_held'] * 4
                    })
                    del open_positions[pair]

            elif pos['direction'] == 'SHORT':
                if bar['high'] >= pos['stop_loss']:
                    exit_price = pos['stop_loss'] + (0.75 * pip)
                    exit_reason = "STOP_LOSS"
                elif bar['low'] <= current_target:
                    exit_price = current_target + (0.75 * pip)
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
                        "symbol": pair, "dir": "SHORT", "pnl": pnl, "r_mult": r_mult,
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
                signal, extreme_wick = check_swing_signal(curr, prev)
                if signal == "NONE":
                    continue

                pip = curr['pip']
                next_open = df.iloc[pos_idx + 1]['open']
                risk_dollars = equity * RISK_PCT

                if signal == "LONG":
                    entry = next_open + (0.75 * pip)
                    sl = extreme_wick - (1.5 * curr['atr14'])
                    risk_price = entry - sl
                    if risk_price <= 0 or curr['sma20'] <= entry:
                        continue

                    units = int(risk_dollars / risk_price)
                    if units >= 500:
                        open_positions[pair] = {
                            "direction": "LONG", "entry_price": entry, "stop_loss": sl,
                            "risk_price": risk_price, "units": units, "pip": pip, "bars_held": 0
                        }

                elif signal == "SHORT":
                    entry = next_open - (0.75 * pip)
                    sl = extreme_wick + (1.5 * curr['atr14'])
                    risk_price = sl - entry
                    if risk_price <= 0 or curr['sma20'] >= entry:
                        continue

                    units = int(risk_dollars / risk_price)
                    if units >= 500:
                        open_positions[pair] = {
                            "direction": "SHORT", "entry_price": entry, "stop_loss": sl,
                            "risk_price": risk_price, "units": units, "pip": pip, "bars_held": 0
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
    print("OANDA 4-HOUR MACRO MEAN-REVERSION SWING SCORECARD")
    print("EUR_USD, GBP_USD, USD_CAD, EUR_GBP | Bollinger 2.5σ + RSI(14)")
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
