#!/usr/bin/env python3
"""
Velez / Weinstein Stage 2 Swing Trading System
- Filter: Stan Weinstein Stage 2 (150 SMA slope, Mansfield Relative Strength)
- Trigger: Oliver Velez Elephant Breakout & 20-SMA Pullback Bounces
- Position Sizing: Fixed 1.5% Risk per Trade
"""

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

SYMBOLS = ["NVDA", "TSLA", "AMD", "META", "AMZN", "MSFT", "AAPL"]
BENCHMARK = "SPY"
START_EQUITY = 100_000.0
RISK_PCT = 0.015               # 1.5% true account risk
MAX_CONCURRENT = 3
TARGET_R = 2.0                 # Trim 50% at 2.0R, trail balance
LOOKBACK_DAYS = 730            # 2 years of daily bars

def fetch_and_prepare(symbols: list, benchmark: str) -> dict:
    all_syms = list(set(symbols + [benchmark]))
    start_date = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    raw = yf.download(all_syms, start=start_date, progress=False, group_by="ticker")
    
    spy_df = raw[benchmark].dropna()
    spy_sma = spy_df['Close'].rolling(window=50).mean()
    spy_rs_base = spy_df['Close'] / spy_sma

    data = {}
    for sym in symbols:
        df = raw[sym].dropna().copy()
        if len(df) < 160:
            continue

        # Weinstein Stage 2 Moving Averages (150-day = 30-week)
        df['sma150'] = df['Close'].rolling(window=150).mean()
        df['sma150_slope'] = df['sma150'] - df['sma150'].shift(15)

        # Velez Short-Term Trendline
        df['sma20'] = df['Close'].rolling(window=20).mean()
        df['vol_sma20'] = df['Volume'].rolling(window=20).mean()

        # ATR Calculation
        tr = np.maximum(
            df['High'] - df['Low'],
            np.maximum(abs(df['High'] - df['Close'].shift(1)), abs(df['Low'] - df['Close'].shift(1)))
        )
        df['atr14'] = tr.rolling(window=14).mean()

        # Mansfield Relative Strength vs Benchmark
        stock_rs_base = df['Close'] / df['Close'].rolling(window=50).mean()
        df['mansfield_rs'] = ((stock_rs_base / spy_rs_base) - 1.0) * 100.0

        # Breakout Resistance Level (60-day base high)
        df['base_high'] = df['High'].rolling(window=60).max().shift(1)
        data[sym] = df.dropna()

    return data

def evaluate_signal(curr, prev) -> tuple:
    in_stage2 = (curr['Close'] > curr['sma150']) and (curr['sma150_slope'] > 0)
    has_relative_strength = curr['mansfield_rs'] > 0
    velez_alignment = (curr['Close'] > curr['sma20']) and (curr['sma20'] > curr['sma150'])

    if not (in_stage2 and has_relative_strength and velez_alignment):
        return "NONE", 0.0

    bar_range = curr['High'] - curr['Low']
    avg_range = curr['atr14']
    is_elephant = (bar_range > 1.4 * avg_range) and (curr['Close'] > curr['Open']) and \
                  ((curr['Close'] - curr['Low']) / (bar_range + 1e-6) > 0.70)
    volume_surge = curr['Volume'] >= (1.6 * curr['vol_sma20'])
    base_breakout = curr['Close'] > curr['base_high']

    if is_elephant and volume_surge and base_breakout:
        stop_price = min(curr['Low'], curr['sma20']) - (0.5 * curr['atr14'])
        return "STAGE2_BREAKOUT", stop_price

    tested_20sma = abs(prev['Low'] - prev['sma20']) <= (0.5 * curr['atr14'])
    bounce_confirmed = (curr['Close'] > prev['High']) and (curr['Close'] > curr['sma20']) and (curr['Close'] > curr['Open'])

    if tested_20sma and bounce_confirmed:
        stop_price = min(prev['Low'], curr['sma20']) - (0.5 * curr['atr14'])
        return "SMA20_BOUNCE", stop_price

    return "NONE", 0.0

def run_backtest():
    data = fetch_and_prepare(SYMBOLS, BENCHMARK)
    if not data:
        print("[!] Insufficient historical data.")
        return

    all_dates = sorted(list(set.union(*[set(df.index) for df in data.values()])))
    equity = START_EQUITY
    equity_curve = [equity]
    open_positions = {}
    completed_trades = []

    for dt in all_dates:
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            df = data[sym]
            if dt not in df.index:
                continue

            bar = df.loc[dt]
            pos['days_held'] += 1

            if bar['Low'] <= pos['stop_loss']:
                exit_price = pos['stop_loss']
                pnl = pos['units'] * (exit_price - pos['entry_price'])
                equity += pnl
                equity_curve.append(equity)
                completed_trades.append({
                    "symbol": sym, "type": pos['type'], "pnl": pnl,
                    "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_unit'],
                    "days": pos['days_held'], "exit": "STOP_LOSS"
                })
                del open_positions[sym]
                continue

            if not pos['scaled'] and bar['High'] >= pos['take_profit']:
                scaled_units = pos['units'] * 0.5
                pnl_half = scaled_units * (pos['take_profit'] - pos['entry_price'])
                equity += pnl_half
                pos['units'] -= scaled_units
                pos['scaled'] = True
                pos['stop_loss'] = pos['entry_price']

            if pos['scaled'] and bar['Close'] < bar['sma20']:
                exit_price = bar['Close']
                pnl_rem = pos['units'] * (exit_price - pos['entry_price'])
                equity += pnl_rem
                equity_curve.append(equity)
                completed_trades.append({
                    "symbol": sym, "type": pos['type'], "pnl": pnl_rem,
                    "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_unit'],
                    "days": pos['days_held'], "exit": "TRAILED_20SMA"
                })
                del open_positions[sym]

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
                    units = int(risk_dollars / risk_price) if 'risk_price' in locals() else int(risk_dollars / risk_per_unit)
                    if units < 1:
                        continue

                    take_profit = next_open + (risk_per_unit * TARGET_R)

                    open_positions[sym] = {
                        "entry_price": next_open,
                        "stop_loss": stop_price,
                        "take_profit": take_profit,
                        "risk_per_unit": risk_per_unit,
                        "units": units,
                        "type": setup_type,
                        "scaled": False,
                        "days_held": 0
                    }

    if not completed_trades:
        print("[!] No completed trades recorded.")
        return

    tdf = pd.DataFrame(completed_trades)
    wins = tdf[tdf['pnl'] > 0]
    losses = tdf[tdf['pnl'] <= 0]
    pf = (wins['pnl'].sum() / abs(losses['pnl'].sum())) if abs(losses['pnl'].sum()) > 0 else 99.9

    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    drawdown = (eq_series - peak) / peak

    print("\n" + "=" * 65)
    print("VELEZ / WEINSTEIN STAGE 2 SWING SCORECARD")
    print("Universe: Mega-Cap Tech | Horizon: Daily Bars")
    print("=" * 65)
    print(f"Start Equity:       ${START_EQUITY:,.2f}")
    print(f"Final Equity:       ${equity:,.2f} ({((equity/START_EQUITY)-1)*100:+.2f}%)")
    print(f"Total Trades:       {len(tdf)}")
    print(f"Win Rate:           {(len(wins)/len(tdf))*100:.2f}%")
    print(f"Profit Factor:      {pf:.2f}")
    print(f"Expectancy:         {tdf['r_mult'].mean():+.3f} R / trade")
    print(f"Max Drawdown:       {abs(drawdown.min())*100:.2f}%")
    print(f"Avg Trade Length:   {tdf['days'].mean():.1f} market days")
    print("\nSetup Breakdown:")
    for st, c in tdf['type'].value_counts().items():
        print(f"  {st:18s}: {c} ({c/len(tdf)*100:.1f}%)")
    print("=" * 65)

if __name__ == "__main__":
    run_backtest()
