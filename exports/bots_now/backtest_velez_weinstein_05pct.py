#!/usr/bin/env python3
"""
Velez / Weinstein Stage 2 Swing Engine (10 Slots @ 0.5% Risk)
- Universe: 20 High-Beta Liquid Growth Leaders
- Sizing: 0.5% Account Risk per trade (Max Open Portfolio Risk: 5.0%)
- Circuit Breaker: Liquidates entire portfolio if aggregate drawdown hits 1.0%
- Partial Scale: 50% at +2.0R, trail balance to 20-SMA or 2.5x ATR extension
"""

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

SYMBOLS = [
    "NVDA", "TSLA", "AMD", "META", "AMZN", "MSFT", "AAPL", "PLTR", 
    "ARM", "PANW", "CRWD", "NFLX", "AVGO", "QCOM", "NOW", "UBER", 
    "COIN", "SNOW", "SHOP", "NET"
]
BENCHMARK = "SPY"
START_EQUITY = 100_000.0
RISK_PCT = 0.005               # 0.50% account risk per setup ($500 on $100k)
MAX_CONCURRENT = 10            # Up to 10 active positions
CIRCUIT_BREAKER_PCT = 0.010    # 1.0% portfolio drawdown breaker ($1,000)
LOOKBACK_DAYS = 730

def fetch_and_prepare():
    all_syms = list(set(SYMBOLS + [BENCHMARK]))
    start_date = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    raw = yf.download(all_syms, start=start_date, progress=False, group_by="ticker")
    
    spy_df = raw[BENCHMARK].dropna()
    spy_sma = spy_df['Close'].rolling(window=50).mean()
    spy_rs_base = spy_df['Close'] / spy_sma

    data = {}
    for sym in SYMBOLS:
        try:
            df = raw[sym].dropna().copy()
        except Exception:
            continue
        if len(df) < 160:
            continue

        df['sma150'] = df['Close'].rolling(window=150).mean()
        df['sma150_slope'] = df['sma150'] - df['sma150'].shift(15)
        df['sma20'] = df['Close'].rolling(window=20).mean()
        df['vol_sma20'] = df['Volume'].rolling(window=20).mean()

        tr = np.maximum(
            df['High'] - df['Low'],
            np.maximum(abs(df['High'] - df['Close'].shift(1)), abs(df['Low'] - df['Close'].shift(1)))
        )
        df['atr14'] = tr.rolling(window=14).mean()

        stock_rs_base = df['Close'] / df['Close'].rolling(window=50).mean()
        df['mansfield_rs'] = ((stock_rs_base / spy_rs_base) - 1.0) * 100.0
        df['base_high'] = df['High'].rolling(window=40).max().shift(1)
        data[sym] = df.dropna()

    return data

def evaluate_signal(curr, prev) -> tuple:
    in_stage2 = (curr['Close'] > curr['sma150']) and (curr['sma150_slope'] > 0)
    has_rs = curr['mansfield_rs'] > 0
    velez_trend = (curr['Close'] > curr['sma20']) and (curr['sma20'] > curr['sma150'])

    if not (in_stage2 and has_rs and velez_trend):
        return "NONE", 0.0

    # Weinstein Base Breakout
    if (curr['Close'] > curr['base_high']) and (curr['Volume'] >= 1.4 * curr['vol_sma20']):
        sl = min(curr['Low'], curr['sma20']) - (0.4 * curr['atr14'])
        return "STAGE2_BREAKOUT", sl

    # Velez 20-SMA Dynamic Bounce
    tested_sma = abs(prev['Low'] - prev['sma20']) <= (0.4 * curr['atr14'])
    bounce = (curr['Close'] > prev['High']) and (curr['Close'] > curr['sma20']) and (curr['Close'] > curr['Open'])
    if tested_sma and bounce:
        sl = min(prev['Low'], curr['sma20']) - (0.4 * curr['atr14'])
        return "SMA20_BOUNCE", sl

    return "NONE", 0.0

def run_simulation():
    data = fetch_and_prepare()
    all_dates = sorted(list(set.union(*[set(df.index) for df in data.values()])))
    equity = START_EQUITY
    equity_curve = [equity]
    open_positions = {}
    completed_legs = []
    circuit_breaker_trips = 0

    for dt in all_dates:
        # 1.0% Portfolio-Level Circuit Breaker Evaluation
        unrealized_pnl = 0.0
        for sym, pos in open_positions.items():
            if sym in data and dt in data[sym].index:
                curr_px = data[sym].loc[dt, 'Close']
                unrealized_pnl += pos['rem_units'] * (curr_px - pos['entry_price'])

        if (unrealized_pnl / equity) <= -CIRCUIT_BREAKER_PCT and len(open_positions) > 0:
            circuit_breaker_trips += 1
            for sym in list(open_positions.keys()):
                pos = open_positions[sym]
                exit_price = data[sym].loc[dt, 'Close'] if dt in data[sym].index else pos['entry_price']
                pnl = pos['rem_units'] * (exit_price - pos['entry_price'])
                equity += pnl
                equity_curve.append(equity)
                completed_legs.append({
                    "symbol": sym, "type": pos['type'], "pnl": pnl,
                    "r_mult": (exit_price - pos['entry_price']) / max(pos['risk_per_unit'], 0.01),
                    "days": pos['days_held'], "exit": "CIRCUIT_BREAKER_FLATTEN"
                })
                del open_positions[sym]
            continue

        # Position Lifecycle Management
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            df = data[sym]
            if dt not in df.index:
                continue

            bar = df.loc[dt]
            pos['days_held'] += 1

            # Stop Loss
            if bar['Low'] <= pos['stop_loss']:
                exit_price = pos['stop_loss']
                pnl = pos['rem_units'] * (exit_price - pos['entry_price'])
                equity += pnl
                equity_curve.append(equity)
                completed_legs.append({
                    "symbol": sym, "type": pos['type'], "pnl": pnl,
                    "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_unit'],
                    "days": pos['days_held'], "exit": "STOP_LOSS"
                })
                del open_positions[sym]
                continue

            # Scale Out 50% at +2.0R
            if not pos['scaled'] and bar['High'] >= pos['target_2r']:
                scale_units = pos['initial_units'] * 0.5
                pnl = scale_units * (pos['target_2r'] - pos['entry_price'])
                equity += pnl
                equity_curve.append(equity)
                completed_legs.append({
                    "symbol": sym, "type": pos['type'], "pnl": pnl,
                    "r_mult": 2.0, "days": pos['days_held'], "exit": "SCALE_2R"
                })
                pos['rem_units'] -= scale_units
                pos['scaled'] = True
                pos['stop_loss'] = pos['entry_price']

            # Extension or SMA Exit on Balance
            stretched = (bar['High'] - bar['sma20']) >= (2.5 * bar['atr14'])
            closed_below = bar['Close'] < bar['sma20']
            if pos['scaled'] and (stretched or closed_below):
                exit_price = bar['Close']
                pnl = pos['rem_units'] * (exit_price - pos['entry_price'])
                equity += pnl
                equity_curve.append(equity)
                exit_tag = "EXTENSION_EXIT" if stretched else "TRAILED_SMA20"
                completed_legs.append({
                    "symbol": sym, "type": pos['type'], "pnl": pnl,
                    "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_unit'],
                    "days": pos['days_held'], "exit": exit_tag
                })
                del open_positions[sym]

        # Scan for Entries
        if len(open_positions) < MAX_CONCURRENT:
            for sym in SYMBOLS:
                if sym in open_positions or sym not in data:
                    continue
                if len(open_positions) >= MAX_CONCURRENT:
                    break

                df = data[sym]
                if dt not in df.index:
                    continue

                pos_idx = df.index.get_loc(dt)
                if pos_idx < 1 or pos_idx >= len(df) - 1:
                    continue

                curr, prev = df.iloc[pos_idx], df.iloc[pos_idx - 1]
                setup_type, stop_price = evaluate_signal(curr, prev)

                if setup_type != "NONE":
                    next_open = df.iloc[pos_idx + 1]['Open']
                    risk_per_unit = next_open - stop_price
                    if risk_per_unit <= 0:
                        continue

                    risk_dollars = equity * RISK_PCT
                    units = int(risk_dollars / risk_per_unit)
                    if units < 2:
                        continue

                    open_positions[sym] = {
                        "entry_price": next_open,
                        "stop_loss": stop_price,
                        "target_2r": next_open + (risk_per_unit * 2.0),
                        "risk_per_unit": risk_per_unit,
                        "initial_units": units,
                        "rem_units": units,
                        "type": setup_type,
                        "scaled": False,
                        "days_held": 0
                    }

    tdf = pd.DataFrame(completed_legs)
    wins = tdf[tdf['pnl'] > 0]
    losses = tdf[tdf['pnl'] <= 0]
    pf = (wins['pnl'].sum() / abs(losses['pnl'].sum())) if abs(losses['pnl'].sum()) > 0 else 99.9

    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    drawdown = (eq_series - peak) / peak

    print("\n" + "=" * 68)
    print("VELEZ / WEINSTEIN 10-SLOT SWING ENGINE (0.5% RISK / TRADE)")
    print("Universe: 20 Tickers | Max Concurrent: 10 | 0.5% Risk | 1.0% Breaker")
    print("=" * 68)
    print(f"Start Equity:          ${START_EQUITY:,.2f}")
    print(f"Final Equity:          ${equity:,.2f} ({((equity/START_EQUITY)-1)*100:+.2f}%)")
    print(f"Total Trade Legs:      {len(tdf)}")
    print(f"Win Rate:              {(len(wins)/len(tdf))*100:.2f}%")
    print(f"Profit Factor:         {pf:.2f}")
    print(f"Expectancy:            {tdf['r_mult'].mean():+.3f} R / leg")
    print(f"Max Portfolio DD:      {abs(drawdown.min())*100:.2f}%")
    print(f"Circuit Breaker Trips: {circuit_breaker_trips}")
    print("\nExit Type Distribution:")
    for et, c in tdf['exit'].value_counts().items():
        print(f"  {et:24s}: {c} ({c/len(tdf)*100:.1f}%)")
    print("=" * 68)

if __name__ == "__main__":
    run_simulation()
