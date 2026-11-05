#!/usr/bin/env python3
"""
OANDA London Judas Sweep Scalper (Liquidity Reversal)
- Strategy: Fade the Asian Range Breakout at London Open
- Pairs: EUR_USD, GBP_USD
- Timeframe: M15 (15-Minute Candles)
- Asian Box: 00:00 - 06:45 UTC
- Sweep Window: 07:00 - 09:30 UTC (London Open Killzone)
- Trigger: Sweep >= 3 pips, then candle CLOSES back INSIDE Asian Box
- Target: Asian Range Midpoint or 1.5R
- Stop: Beyond the sweep wick extreme (+ 1.5 pips)
- Friction: 1.2 Pips Round-Trip Spread
- Sizing: Fixed 1.0% Risk per Trade
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
OANDA_URL = os.getenv("OANDA_URL", "https://api-fxpractice.oanda.com")
OANDA_ENV = "practice" if "fxpractice" in OANDA_URL else "live"

PAIRS = ["EUR_USD", "GBP_USD"]
START_EQUITY = 100_000.0
RISK_PCT = 0.01
CANDLE_COUNT = 4500

client = oandapyV20.API(access_token=OANDA_TOKEN, environment=OANDA_ENV)

def get_pip(pair: str) -> float:
    return 0.01 if "JPY" in pair else 0.0001

def fetch_data(pair: str) -> pd.DataFrame:
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
        records.append({
            "time": pd.to_datetime(c["time"].split(".")[0] + "Z"),
            "open": float(c["mid"]["o"]),
            "high": float(c["mid"]["h"]),
            "low": float(c["mid"]["l"]),
            "close": float(c["mid"]["c"]),
            "volume": int(c["volume"])
        })

    df = pd.DataFrame(records).set_index("time")
    df['symbol'] = pair
    df['pip'] = get_pip(pair)
    return df

def simulate_judas(pair: str, df: pd.DataFrame) -> list:
    pip = get_pip(pair)
    trades = []
    
    df['date'] = df.index.date
    days = df['date'].unique()

    for day in days:
        day_bars = df[df['date'] == day]
        if len(day_bars) < 28:
            continue

        asian_bars = day_bars.between_time("00:00", "06:45")
        if len(asian_bars) < 15:
            continue

        asian_high = asian_bars['high'].max()
        asian_low = asian_bars['low'].min()
        asian_range = (asian_high - asian_low) / pip

        # Range filter: 15 to 45 pips
        if asian_range < 15 or asian_range > 45:
            continue

        asian_mid = (asian_high + asian_low) / 2.0
        london_bars = day_bars.between_time("07:00", "12:00")
        if london_bars.empty:
            continue

        trade_taken = False
        in_pos = False
        pos_dir, entry, sl, tp, risk_unit = None, 0.0, 0.0, 0.0, 0.0

        for idx, (t, bar) in enumerate(london_bars.iterrows()):
            if trade_taken and not in_pos:
                break

            # Only search for sweeps during the 07:00 - 09:30 killzone
            in_killzone = t.time() <= pd.to_datetime("09:30").time()

            if not in_pos and not trade_taken and in_killzone:
                # 1. Bullish Sweep Fade (Swept Asian High >= 3 pips, but Closed Back Inside)
                if (bar['high'] >= asian_high + 3 * pip) and (bar['close'] < asian_high):
                    in_pos = True
                    trade_taken = True
                    pos_dir = "SHORT"
                    entry = bar['close'] - (0.6 * pip)
                    sl = bar['high'] + (1.5 * pip)
                    risk_unit = sl - entry
                    if risk_unit <= 0:
                        in_pos = False
                        continue
                    # Target the Asian 50% Midline or minimum 1.35R
                    target_mid = entry - (entry - asian_mid)
                    tp = min(asian_mid, entry - (risk_unit * 1.35))
                    continue

                # 2. Bearish Sweep Fade (Swept Asian Low >= 3 pips, but Closed Back Inside)
                elif (bar['low'] <= asian_low - 3 * pip) and (bar['close'] > asian_low):
                    in_pos = True
                    trade_taken = True
                    pos_dir = "LONG"
                    entry = bar['close'] + (0.6 * pip)
                    sl = bar['low'] - (1.5 * pip)
                    risk_unit = entry - sl
                    if risk_unit <= 0:
                        in_pos = False
                        continue
                    target_mid = entry + (asian_mid - entry)
                    tp = max(asian_mid, entry + (risk_unit * 1.35))
                    continue

            if in_pos:
                exit_price, exit_reason = None, None

                if pos_dir == "SHORT":
                    if bar['high'] >= sl:
                        exit_price = sl + (0.6 * pip)
                        exit_reason = "STOP_LOSS"
                    elif bar['low'] <= tp:
                        exit_price = tp + (0.6 * pip)
                        exit_reason = "TAKE_PROFIT"
                    elif idx >= len(london_bars) - 1:
                        exit_price = bar['close'] + (0.6 * pip)
                        exit_reason = "TIME_STOP"

                    if exit_price is not None:
                        delta = entry - exit_price
                        r_mult = delta / max(risk_unit, 0.0001)
                        trades.append({
                            "pair": pair, "date": day, "dir": "SHORT",
                            "delta_pips": delta / pip, "r_mult": r_mult,
                            "exit_reason": exit_reason
                        })
                        in_pos = False

                elif pos_dir == "LONG":
                    if bar['low'] <= sl:
                        exit_price = sl - (0.6 * pip)
                        exit_reason = "STOP_LOSS"
                    elif bar['high'] >= tp:
                        exit_price = tp - (0.6 * pip)
                        exit_reason = "TAKE_PROFIT"
                    elif idx >= len(london_bars) - 1:
                        exit_price = bar['close'] - (0.6 * pip)
                        exit_reason = "TIME_STOP"

                    if exit_price is not None:
                        delta = exit_price - entry
                        r_mult = delta / max(risk_unit, 0.0001)
                        trades.append({
                            "pair": pair, "date": day, "dir": "LONG",
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
            trades = simulate_judas(pair, df)
            all_trades.extend(trades)

    if not all_trades:
        print("[!] No trades generated.")
        sys.exit(0)

    tdf = pd.DataFrame(all_trades).sort_values("date").reset_index(drop=True)
    equity = START_EQUITY
    equity_curve = [equity]

    for _, t in tdf.iterrows():
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
    print("OANDA LONDON JUDAS SWEEP SCORECARD (FADING FALSE BREAKOUTS)")
    print("EUR_USD & GBP_USD | M15 Reversal Model | 1.2-Pip Spread Deducted")
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
