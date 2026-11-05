#!/usr/bin/env python3
"""
OANDA London Open Range Breakout (LORB) Scalper
- Pairs: EUR_USD, GBP_USD (Primary London majors)
- Timeframe: M15 (15-Minute Candles)
- Asian Session Range Box: 00:00 - 07:00 UTC
- Trade Execution Window: 07:00 - 11:00 UTC (London Morning Drive)
- Entry: 3-pip buffer outside Asian High/Low
- Target: 1.5R - 2.0R (Dynamic based on Asian Range size)
- Stop Loss: 50% midpoint of the Asian Range
- Spread / Friction: 1.2 Pips deducted per execution
- Position Sizing: 1.0% Account Risk per trade
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

PAIRS = ["EUR_USD", "GBP_USD"]
START_EQUITY = 100_000.0
RISK_PCT = 0.01          # 1% risk per setup
CANDLE_COUNT = 4500      # ~45 trading days of M15 candles

if not OANDA_TOKEN:
    print("[!] Error: OANDA token missing from .env")
    sys.exit(1)

client = oandapyV20.API(access_token=OANDA_TOKEN, environment=OANDA_ENV)

def get_pip(pair: str) -> float:
    return 0.01 if "JPY" in pair else 0.0001

def fetch_data(pair: str) -> pd.DataFrame:
    print(f"[*] Ingesting {CANDLE_COUNT} M15 candles for {pair} ({OANDA_ENV})...")
    params = {"granularity": "M15", "count": CANDLE_COUNT, "price": "M"}
    r = instruments.InstrumentsCandles(instrument=pair, params=params)
    try:
        client.request(r)
    except Exception as e:
        print(f"[!] Error: {e}")
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
    df['symbol'] = pair
    df['pip'] = get_pip(pair)
    return df

def simulate_lorb(pair: str, df: pd.DataFrame) -> list:
    pip = get_pip(pair)
    trades = []
    
    # Group by trading day
    df['date'] = df.index.date
    days = df['date'].unique()

    for day in days:
        day_bars = df[df['date'] == day]
        if len(day_bars) < 28:
            continue

        # Asian Session Range: 00:00 to 07:00 UTC
        asian_bars = day_bars.between_time("00:00", "06:45")
        if len(asian_bars) < 15:
            continue

        asian_high = asian_bars['high'].max()
        asian_low = asian_bars['low'].min()
        asian_range_pips = (asian_high - asian_low) / pip

        # Filter: Asian range must be consolidated (between 15 and 45 pips)
        if asian_range_pips < 15 or asian_range_pips > 45:
            continue

        asian_mid = (asian_high + asian_low) / 2.0

        # London Trading Window: 07:00 to 11:30 UTC
        london_bars = day_bars.between_time("07:00", "11:30")
        if london_bars.empty:
            continue

        trade_taken_today = False
        in_pos = False
        pos_dir, entry_price, stop_loss, take_profit, risk_per_unit = None, 0.0, 0.0, 0.0, 0.0
        entry_idx = 0

        for idx, (t, bar) in enumerate(london_bars.iterrows()):
            if trade_taken_today and not in_pos:
                break

            if not in_pos and not trade_taken_today:
                # Bullish Breakout: 3 pips above Asian High
                if bar['high'] >= (asian_high + 3 * pip):
                    in_pos = True
                    trade_taken_today = True
                    pos_dir = "LONG"
                    entry_price = asian_high + (3.6 * pip) # 3 pips + 0.6 pip entry spread
                    stop_loss = asian_mid - (0.6 * pip)    # Stop at Asian 50% midpoint
                    risk_per_unit = entry_price - stop_loss
                    take_profit = entry_price + (risk_per_unit * 1.50)
                    entry_idx = idx
                    continue

                # Bearish Breakout: 3 pips below Asian Low
                elif bar['low'] <= (asian_low - 3 * pip):
                    in_pos = True
                    trade_taken_today = True
                    pos_dir = "SHORT"
                    entry_price = asian_low - (3.6 * pip)
                    stop_loss = asian_mid + (0.6 * pip)
                    risk_per_unit = stop_loss - entry_price
                    take_profit = entry_price - (risk_per_unit * 1.50)
                    entry_idx = idx
                    continue

            if in_pos:
                exit_price, exit_reason = None, None

                if pos_dir == "LONG":
                    if bar['low'] <= stop_loss:
                        exit_price = stop_loss - (0.6 * pip)
                        exit_reason = "STOP_LOSS"
                    elif bar['high'] >= take_profit:
                        exit_price = take_profit - (0.6 * pip)
                        exit_reason = "TAKE_PROFIT"
                    elif idx >= len(london_bars) - 1: # Session close exit
                        exit_price = bar['close'] - (0.6 * pip)
                        exit_reason = "TIME_STOP"

                    if exit_price is not None:
                        delta = exit_price - entry_price
                        r_mult = delta / max(risk_per_unit, 0.0001)
                        trades.append({
                            "pair": pair, "date": day, "dir": "LONG",
                            "entry": entry_price, "exit": exit_price,
                            "delta_pips": delta / pip, "r_mult": r_mult,
                            "exit_reason": exit_reason
                        })
                        in_pos = False

                elif pos_dir == "SHORT":
                    if bar['high'] >= stop_loss:
                        exit_price = stop_loss + (0.6 * pip)
                        exit_reason = "STOP_LOSS"
                    elif bar['low'] <= take_profit:
                        exit_price = take_profit + (0.6 * pip)
                        exit_reason = "TAKE_PROFIT"
                    elif idx >= len(london_bars) - 1:
                        exit_price = bar['close'] + (0.6 * pip)
                        exit_reason = "TIME_STOP"

                    if exit_price is not None:
                        delta = entry_price - exit_price
                        r_mult = delta / max(risk_per_unit, 0.0001)
                        trades.append({
                            "pair": pair, "date": day, "dir": "SHORT",
                            "entry": entry_price, "exit": exit_price,
                            "delta_pips": delta / pip, "r_mult": r_mult,
                            "exit_reason": exit_reason
                        })
                        in_pos = False

    return trades

if __name__ == "__main__":
    all_trades = []
    for pair in PAIRS:
        df = fetch_data(pair)
        if not df.empty:
            trades = simulate_lorb(pair, df)
            all_trades.extend(trades)

    if not all_trades:
        print("[!] No trades generated.")
        sys.exit(0)

    tdf = pd.DataFrame(all_trades).sort_values("date").reset_index(drop=True)
    equity = START_EQUITY
    equity_curve = [equity]

    for _, t in tdf.iterrows():
        # Fixed 1% Risk Sizing: Risking $1,000 per trade
        risk_dollars = equity * RISK_PCT
        pnl = risk_dollars * t['r_mult']
        equity += pnl
        equity_curve.append(equity)

    wins = tdf[tdf['r_mult'] > 0]
    losses = tdf[tdf['r_mult'] <= 0]
    pf = (wins['r_mult'].sum() / abs(losses['r_mult'].sum())) if abs(losses['r_mult'].sum()) > 0 else 99.9

    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    drawdown = (eq_series - peak) / peak

    print("\n" + "=" * 68)
    print("OANDA LONDON OPEN RANGE BREAKOUT (LORB) SCALPER")
    print("EUR_USD & GBP_USD | M15 Asian Box Breakout | 1.2-Pip Spread Cost")
    print("=" * 68)
    print(f"Start Equity:       ${START_EQUITY:,.2f}")
    print(f"Final Equity:       ${equity:,.2f} ({((equity/START_EQUITY)-1)*100:+.2f}%)")
    print(f"Total Trades:       {len(tdf)}")
    print(f"Win Rate:           {(len(wins)/len(tdf))*100:.2f}%")
    print(f"Profit Factor:      {pf:.2f}")
    print(f"Expectancy:         {tdf['r_mult'].mean():+.3f} R / trade")
    print(f"Max Drawdown:       {abs(drawdown.min())*100:.2f}%")
    print(f"Net Profit:         ${equity - START_EQUITY:,.2f}")
    print("\nExit Breakdown:")
    for r, c in tdf['exit_reason'].value_counts().items():
        print(f"  {r:14s}: {c} ({c/len(tdf)*100:.1f}%)")
    print("=" * 68)
