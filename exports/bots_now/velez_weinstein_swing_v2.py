#!/usr/bin/env python3
"""
Velez / Weinstein Stage 2 Swing System (V2 Refined)
- Universe: 12 High-Beta Growth Leaders
- True Multi-Leg Trade Accounting (Accurate Win Rate & PF)
- Flexible Stage 2 Breakout + Velez 20-SMA Value Zone Entries
- Velez Extension Exits (Locking gains when stretched > 2.5x ATR from 20-SMA)
"""

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

SYMBOLS = ["NVDA", "TSLA", "AMD", "META", "AMZN", "MSFT", "AAPL", "PLTR", "ARM", "PANW", "CRWD", "NFLX"]
BENCHMARK = "SPY"
START_EQUITY = 100_000.0
RISK_PCT = 0.015               # 1.5% true account risk
MAX_CONCURRENT = 4
LOOKBACK_DAYS = 730            # 2 full years

def fetch_and_prepare(symbols: list, benchmark: str) -> dict:
    all_syms = list(set(symbols + [benchmark]))
    start_date = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    raw = yf.download(all_syms, start=start_date, progress=False, group_by="ticker")
    
    spy_df = raw[benchmark].dropna()
    spy_sma = spy_df['Close'].rolling(window=50).mean()
    spy_rs_base = spy_df['Close'] / spy_sma

    data = {}
    for sym in symbols:
        try:
            df = raw[sym].dropna().copy()
        except Exception:
            continue
            
        if len(df) < 160:
            continue

        # Weinstein 30-Week (150-Day) Trend
        df['sma150'] = df['Close'].rolling(window=150).mean()
        df['sma150_slope'] = df['sma150'] - df['sma150'].shift(15)

        # Velez 20-Day Trendline
        df['sma20'] = df['Close'].rolling(window=20).mean()
        df['vol_sma20'] = df['Volume'].rolling(window=20).mean()

        # ATR
        tr = np.maximum(
            df['High'] - df['Low'],
            np.maximum(abs(df['High'] - df['Close'].shift(1)), abs(df['Low'] - df['Close'].shift(1)))
        )
        df['atr14'] = tr.rolling(window=14).mean()

        # Mansfield Relative Strength
        stock_rs_base = df['Close'] / df['Close'].rolling(window=50).mean()
        df['mansfield_rs'] = ((stock_rs_base / spy_rs_base) - 1.0) * 100.0

        # Consolidation Base High (40-day breakout box)
        df['base_high'] = df['High'].rolling(window=40).max().shift(1)
        data[sym] = df.dropna()

    return data

def evaluate_signal(curr, prev) -> tuple:
    in_stage2 = (curr['Close'] > curr['sma150']) and (curr['sma150_slope'] > 0)
    has_relative_strength = curr['mansfield_rs'] > 0
    velez_alignment = (curr['Close'] > curr['sma20']) and (curr['sma20'] > curr['sma150'])

    if not (in_stage2 and has_relative_strength and velez_alignment):
        return "NONE", 0.0

    # Trigger A: Weinstein Stage 2 Base Breakout (Close > Base High + Volume > 1.4x VolSMA)
    breakout = (curr['Close'] > curr['base_high']) and (curr['Volume'] >= 1.4 * curr['vol_sma20'])
    if breakout:
        stop_price = min(curr['Low'], curr['sma20']) - (0.4 * curr['atr14'])
        return "STAGE2_BREAKOUT", stop_price

    # Trigger B: Velez 20-SMA Dynamic Bounce
    tested_sma = abs(prev['Low'] - prev['sma20']) <= (0.4 * curr['atr14'])
    bounce_bar = (curr['Close'] > prev['High']) and (curr['Close'] > curr['sma20']) and (curr['Close'] > curr['Open'])
    if tested_sma and bounce_bar:
        stop_price = min(prev['Low'], curr['sma20']) - (0.4 * curr['atr14'])
        return "SMA20_BOUNCE", stop_price

    return "NONE", 0.0

def run_backtest():
    data = fetch_and_prepare(SYMBOLS, BENCHMARK)
    if not data:
        print("[!] No data available.")
        return

    all_dates = sorted(list(set.union(*[set(df.index) for df in data.values()])))
    equity = START_EQUITY
    equity_curve = [equity]
    open_positions = {}
    completed_legs = []

    for dt in all_dates:
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            df = data[sym]
            if dt not in df.index:
                continue

            bar = df.loc[dt]
            pos['days_held'] += 1

            # 1. Stop Loss Hit
            if bar['Low'] <= pos['stop_loss']:
                exit_price = pos['stop_loss']
                pnl = pos['rem_units'] * (exit_price - pos['entry_price'])
                equity += pnl
                equity_curve.append(equity)
                r_mult = (exit_price - pos['entry_price']) / pos['risk_per_unit']
                completed_legs.append({
                    "symbol": sym, "type": pos['type'], "pnl": pnl,
                    "r_mult": r_mult, "days": pos['days_held'], "exit": "STOP_LOSS"
                })
                del open_positions[sym]
                continue

            # 2. Velez Scale-Out: +2.0R Target hit (scale 50%, lock breakeven)
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

            # 3. Extension Exit on Remainder: Price stretched > 2.5x ATR above 20-SMA
            stretched = (bar['High'] - bar['sma20']) >= (2.5 * bar['atr14'])
            closed_below_sma = bar['Close'] < bar['sma20']

            if pos['scaled'] and (stretched or closed_below_sma):
                exit_price = bar['Close']
                pnl = pos['rem_units'] * (exit_price - pos['entry_price'])
                equity += pnl
                equity_curve.append(equity)
                r_mult = (exit_price - pos['entry_price']) / pos['risk_per_unit']
                exit_tag = "EXTENSION_EXIT" if stretched else "TRAILED_SMA20"
                completed_legs.append({
                    "symbol": sym, "type": pos['type'], "pnl": pnl,
                    "r_mult": r_mult, "days": pos['days_held'], "exit": exit_tag
                })
                del open_positions[sym]

        # Scan for entries
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
                    if units < 2:  # Must have at least 2 units to allow 50% scaling
                        continue

                    target_2r = next_open + (risk_per_unit * 2.0)

                    open_positions[sym] = {
                        "entry_price": next_open,
                        "stop_loss": stop_price,
                        "target_2r": target_2r,
                        "risk_per_unit": risk_per_unit,
                        "initial_units": units,
                        "rem_units": units,
                        "type": setup_type,
                        "scaled": False,
                        "days_held": 0
                    }

    if not completed_legs:
        print("[!] No completed trades.")
        return

    tdf = pd.DataFrame(completed_legs)
    wins = tdf[tdf['pnl'] > 0]
    losses = tdf[tdf['pnl'] <= 0]
    pf = (wins['pnl'].sum() / abs(losses['pnl'].sum())) if abs(losses['pnl'].sum()) > 0 else 99.9

    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    drawdown = (eq_series - peak) / peak

    print("\n" + "=" * 65)
    print("VELEZ / WEINSTEIN STAGE 2 SWING SCORECARD (V2 REFINED)")
    print("Universe: 12 High-Beta Leaders | Horizon: Daily Bars")
    print("=" * 65)
    print(f"Start Equity:       ${START_EQUITY:,.2f}")
    print(f"Final Equity:       ${equity:,.2f} ({((equity/START_EQUITY)-1)*100:+.2f}%)")
    print(f"Total Trade Legs:   {len(tdf)}")
    print(f"Win Rate:           {(len(wins)/len(tdf))*100:.2f}%")
    print(f"Profit Factor:      {pf:.2f}")
    print(f"Expectancy:         {tdf['r_mult'].mean():+.3f} R / trade leg")
    print(f"Max Drawdown:       {abs(drawdown.min())*100:.2f}%")
    print(f"Avg Hold Duration:  {tdf['days'].mean():.1f} market days")
    print("\nExit Type Distribution:")
    for et, c in tdf['exit'].value_counts().items():
        print(f"  {et:18s}: {c} ({c/len(tdf)*100:.1f}%)")
    print("\nSetup Trigger Distribution:")
    for st, c in tdf['type'].value_counts().items():
        print(f"  {st:18s}: {c} ({c/len(tdf)*100:.1f}%)")
    print("=" * 65)

if __name__ == "__main__":
    run_backtest()
