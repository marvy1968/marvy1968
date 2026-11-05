#!/usr/bin/env python3
"""
Intraday 15-Minute ORB + VWAP Confirmation Backtest
- Universe: NVDA, TSLA, AMD (Scalper Daemon Core)
- Granularity: 5-Minute Bars (Max 60-Day Intraday Horizon via yfinance)
- Rules:
    1. Opening Range: 09:30 - 09:45 AM EDT (High/Low of first three 5-min bars)
    2. Entry Trigger: 09:45 - 11:30 AM EDT, Bar closes > 15-Min High AND > Intraday VWAP
    3. Stop Loss: max(ORB_Midpoint, VWAP) at time of entry
    4. Target: +1.5R | Hard Exit: Close < VWAP OR 11:30 AM EDT Session Close
- Capital: $50,000 Base | Risk: 0.50% Equity per Trade
"""

import yfinance as yf
import pandas as pd
import numpy as np

SYMBOLS = ["NVDA", "TSLA", "AMD"]
INITIAL_CAPITAL = 50000.0
RISK_PCT = 0.005
TARGET_R = 1.5

print(f"[*] Downloading 60-day 5-minute intraday data for {SYMBOLS}...")
raw = yf.download(SYMBOLS, period="60d", interval="5m", progress=False, group_by="ticker")

trade_log = []

for sym in SYMBOLS:
    try:
        df = raw[sym].dropna().copy()
    except Exception:
        continue
    if len(df) == 0:
        continue

    # Convert index to Eastern Time
    if df.index.tz is not None:
        df.index = df.index.tz_convert('America/New_York')
    else:
        df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')

    # Calculate typical price and volume weight
    df['tp'] = (df['High'] + df['Low'] + df['Close']) / 3.0
    df['tp_vol'] = df['tp'] * df['Volume']
    df['date'] = df.index.date

    # Group by trading date
    days = df.groupby('date')

    for trade_date, day_df in days:
        rth = day_df.between_time('09:30', '16:00').copy()
        if len(rth) < 6:
            continue

        # Cumulative intraday VWAP
        rth['cum_tp_vol'] = rth['tp_vol'].cumsum()
        rth['cum_vol'] = rth['Volume'].cumsum()
        rth['vwap'] = rth['cum_tp_vol'] / np.where(rth['cum_vol'] == 0, 1e-5, rth['cum_vol'])

        # 1. Establish 15-minute Opening Range (09:30 - 09:45)
        orb_bars = rth.between_time('09:30', '09:45')
        if len(orb_bars) < 3:
            continue

        orb_high = orb_bars['High'].max()
        orb_low = orb_bars['Low'].min()
        orb_mid = (orb_high + orb_low) / 2.0

        # 2. Monitor Session (09:45 - 11:30 AM EDT)
        trading_window = rth.between_time('09:50', '11:30')
        in_pos = False
        pos = {}

        for ts, bar in trading_window.iterrows():
            if not in_pos:
                # Long Breakout Criteria:
                # - Closes above 15-min High
                # - Strictly above cumulative VWAP
                if bar['Close'] > orb_high and bar['Close'] > bar['vwap']:
                    stop_level = max(orb_mid, bar['vwap'])
                    rps = bar['Close'] - stop_level

                    # Reject if risk per share is unreasonably tight (< 0.20) or blown out (> 4.0% of price)
                    if 0.20 <= rps <= (0.04 * bar['Close']):
                        in_pos = True
                        target_price = bar['Close'] + (TARGET_R * rps)
                        risk_dollars = INITIAL_CAPITAL * RISK_PCT
                        shares = int(risk_dollars / rps)
                        if shares < 1:
                            shares = 1

                        pos = {
                            "symbol": sym,
                            "date": trade_date,
                            "entry_time": ts,
                            "entry_price": bar['Close'],
                            "stop_loss": stop_level,
                            "target_price": target_price,
                            "rps": rps,
                            "shares": shares,
                            "bars_held": 0
                        }
            else:
                pos['bars_held'] += 1

                # Stop hit
                if bar['Low'] <= pos['stop_loss']:
                    exit_price = min(bar['Open'], pos['stop_loss'])
                    pnl = (exit_price - pos['entry_price']) * pos['shares']
                    r_mult = (exit_price - pos['entry_price']) / pos['rps']
                    trade_log.append({
                        "symbol": sym, "pnl": pnl, "r_mult": r_mult,
                        "reason": "STOP_LOSS", "bars": pos['bars_held']
                    })
                    in_pos = False
                    break

                # Target hit (+1.5R)
                if bar['High'] >= pos['target_price']:
                    exit_price = max(bar['Open'], pos['target_price'])
                    pnl = (exit_price - pos['entry_price']) * pos['shares']
                    r_mult = (exit_price - pos['entry_price']) / pos['rps']
                    trade_log.append({
                        "symbol": sym, "pnl": pnl, "r_mult": r_mult,
                        "reason": "TARGET_1.5R", "bars": pos['bars_held']
                    })
                    in_pos = False
                    break

                # Invalidation: Close below VWAP
                if bar['Close'] < bar['vwap']:
                    exit_price = bar['Close']
                    pnl = (exit_price - pos['entry_price']) * pos['shares']
                    r_mult = (exit_price - pos['entry_price']) / pos['rps']
                    trade_log.append({
                        "symbol": sym, "pnl": pnl, "r_mult": r_mult,
                        "reason": "VWAP_BREAK", "bars": pos['bars_held']
                    })
                    in_pos = False
                    break

        # If still held at 11:30 AM, market close exit
        if in_pos:
            exit_price = trading_window.iloc[-1]['Close']
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            r_mult = (exit_price - pos['entry_price']) / pos['rps']
            trade_log.append({
                "symbol": sym, "pnl": pnl, "r_mult": r_mult,
                "reason": "SESSION_CLOSE", "bars": pos['bars_held']
            })

tdf = pd.DataFrame(trade_log)

print("\n" + "="*65)
print("  15-MIN ORB + VWAP CONFIRMATION: NVDA, TSLA, AMD (60-DAY 5M)")
print("="*65)

if len(tdf) == 0:
    print("[!] Zero qualifying setups triggered.")
else:
    wins = tdf[tdf['pnl'] > 0]
    losses = tdf[tdf['pnl'] <= 0]
    win_rate = (len(wins) / len(tdf)) * 100
    gross_profit = wins['pnl'].sum()
    gross_loss = abs(losses['pnl'].sum())
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 99.99
    net_pnl = tdf['pnl'].sum()
    avg_r = tdf['r_mult'].mean()
    avg_bars = tdf['bars'].mean()

    print(f"Total Trades Executed:   {len(tdf)} (across 60 trading days)")
    print(f"Win Rate:                {win_rate:.2f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Profit Factor:           {pf:.2f}")
    print(f"Average Expectancy:      {avg_r:+.3f} R / trade")
    print(f"Average Duration:        {avg_bars * 5:.1f} Minutes ({avg_bars:.1f} bars)")
    print(f"Gross Profit:            ${gross_profit:,.2f}")
    print(f"Gross Loss:              ${gross_loss:,.2f}")
    print(f"Net Profit:              ${net_pnl:,.2f} (Return on $50k: +{(net_pnl/INITIAL_CAPITAL)*100:.2f}%)")
    print("-"*65)
    print("Breakdown by Ticker:")
    for s in SYMBOLS:
        sub = tdf[tdf['symbol'] == s]
        if len(sub) > 0:
            s_win = (len(sub[sub['pnl'] > 0]) / len(sub)) * 100
            print(f"  {s:5s} | Trades: {len(sub):2d} | Win Rate: {s_win:5.1f}% | Net PnL: ${sub['pnl'].sum():>+8.2f}")
    print("-"*65)
    print("Exit Breakdown:")
    print(tdf['reason'].value_counts().to_string())
    print("="*65)
