#!/usr/bin/env python3
"""
Larry Connors Double 7's Strategy Backtest
- Universe: Top 5 Liquid Leaders Across All 11 S&P Sectors (55 Symbols)
- Macro Trend: Close > 200-day SMA
- Entry Trigger: Close <= 7-day Low Close
- Exit Trigger: Close >= 7-day High Close OR Disaster Stop (2.5x ATR14) OR 15-Day Time Exit
- Risk: 0.50% Equity Risk | Max Slots: 10 | Horizon: 2-Year Daily Bars
"""

import yfinance as yf
import pandas as pd
import numpy as np

SECTOR_55_SYMBOLS = [
    # Tech
    "AAPL", "MSFT", "AVGO", "ORCL", "AMD",
    # Comm Services
    "GOOGL", "META", "NFLX", "TMUS", "DIS",
    # Consumer Discretionary
    "AMZN", "HD", "MCD", "NKE", "BKNG",
    # Financials
    "BRK-B", "JPM", "V", "MA", "BAC",
    # Health Care
    "LLY", "JNJ", "ABBV", "MRK", "UNH",
    # Industrials
    "CAT", "GE", "RTX", "HON", "UNP",
    # Consumer Staples
    "WMT", "COST", "PG", "KO", "PEP",
    # Energy
    "XOM", "CVX", "COP", "EOG", "SLB",
    # Materials
    "LIN", "SHW", "FCX", "ECL", "APD",
    # Real Estate
    "PLD", "AMT", "EQIX", "SPG", "PSA",
    # Utilities
    "NEE", "SO", "DUK", "CEG", "AEP"
]

INITIAL_CAPITAL = 100000.0
RISK_PCT = 0.005
MAX_POSITIONS = 10
MAX_HOLD_DAYS = 15

print(f"[*] Downloading 2-year daily history for {len(SECTOR_55_SYMBOLS)} sector leaders...")
raw = yf.download(SECTOR_55_SYMBOLS, period="2y", interval="1d", progress=False, group_by="ticker")

ticker_data = {}
all_dates = set()

for sym in SECTOR_55_SYMBOLS:
    try:
        df = raw[sym].dropna().copy()
    except Exception:
        continue
    if len(df) < 205:
        continue

    df['sma200'] = df['Close'].rolling(200).mean()
    # 7-day lowest close and highest close (looking at prior 7 completed sessions)
    df['low_7d'] = df['Close'].rolling(7).min().shift(1)
    df['high_7d'] = df['Close'].rolling(7).max().shift(1)

    tr = np.maximum(
        df['High'] - df['Low'],
        np.maximum(abs(df['High'] - df['Close'].shift(1)), abs(df['Low'] - df['Close'].shift(1)))
    )
    df['atr14'] = tr.rolling(14).mean()

    ticker_data[sym] = df
    all_dates.update(df.index.tolist())

sorted_dates = sorted(list(all_dates))

cash = INITIAL_CAPITAL
active_positions = []
trade_log = []
equity_curve = []

for i in range(200, len(sorted_dates)):
    curr_date = sorted_dates[i]

    # 1. Manage Active Positions
    surviving_positions = []
    for pos in active_positions:
        sym = pos['symbol']
        df = ticker_data[sym]
        if curr_date not in df.index:
            surviving_positions.append(pos)
            continue

        bar = df.loc[curr_date]
        pos['bars_held'] += 1

        # Disaster Stop check
        if bar['Low'] <= pos['stop_loss']:
            exit_price = min(bar['Open'], pos['stop_loss'])
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "DISASTER_STOP", "bars_held": pos['bars_held']
            })
            continue

        # Exit Trigger: Close >= 7-Day High Close
        if bar['Close'] >= bar['high_7d']:
            exit_price = bar['Close']
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "7DAY_HIGH_EXIT", "bars_held": pos['bars_held']
            })
            continue

        # Max Time Exit
        if pos['bars_held'] >= MAX_HOLD_DAYS:
            exit_price = bar['Close']
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "TIME_EXIT", "bars_held": pos['bars_held']
            })
            continue

        surviving_positions.append(pos)

    active_positions = surviving_positions

    open_equity = sum(
        p['shares'] * ticker_data[p['symbol']].loc[curr_date]['Close']
        for p in active_positions if curr_date in ticker_data[p['symbol']].index
    )
    total_equity = cash + open_equity
    equity_curve.append(total_equity)

    # 2. Scanner Entries: Double 7's
    available_slots = MAX_POSITIONS - len(active_positions)
    if available_slots <= 0:
        continue

    held_symbols = [p['symbol'] for p in active_positions]
    candidates = []

    for sym in SECTOR_55_SYMBOLS:
        if sym in held_symbols or sym not in ticker_data:
            continue
        df = ticker_data[sym]
        if curr_date not in df.index:
            continue
        loc_idx = df.index.get_loc(curr_date)
        if loc_idx < 1:
            continue

        t0 = df.iloc[loc_idx]

        in_bull_regime = t0['Close'] > t0['sma200']
        is_7d_low = t0['Close'] <= t0['low_7d']

        if in_bull_regime and is_7d_low:
            stop_loss = round(t0['Close'] - (2.5 * t0['atr14']), 2)
            rps = t0['Close'] - stop_loss
            if rps > 0.30:
                # Rank candidates by how far below the 7-day low they closed
                pct_under = (t0['low_7d'] - t0['Close']) / t0['Close']
                candidates.append({
                    "symbol": sym, "entry": t0['Close'], "stop": stop_loss,
                    "rps": rps, "rank": pct_under
                })

    candidates.sort(key=lambda x: x['rank'], reverse=True)

    for cand in candidates:
        if available_slots <= 0:
            break
        risk_dollars = total_equity * RISK_PCT
        shares = int(risk_dollars / cand['rps'])
        cost = shares * cand['entry']

        if shares >= 2 and cost <= cash:
            cash -= cost
            active_positions.append({
                "symbol": cand['symbol'], "shares": shares,
                "entry_price": cand['entry'], "stop_loss": cand['stop'],
                "risk_per_share": cand['rps'], "bars_held": 0
            })
            available_slots -= 1

# Liquidate remaining
for pos in active_positions:
    df = ticker_data[pos['symbol']]
    exit_price = df.iloc[-1]['Close']
    pnl = (exit_price - pos['entry_price']) * pos['shares']
    trade_log.append({
        "symbol": pos['symbol'], "pnl": pnl,
        "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
        "reason": "END_OF_TEST", "bars_held": pos['bars_held']
    })

tdf = pd.DataFrame(trade_log)

peak = INITIAL_CAPITAL
max_dd_pct = 0.0
for eq in equity_curve:
    if eq > peak:
        peak = eq
    dd = (peak - eq) / peak
    if dd > max_dd_pct:
        max_dd_pct = dd

print("\n" + "="*65)
print("  LARRY CONNORS DOUBLE 7'S RESULTS: 55 S&P SECTOR LEADERS")
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
    avg_hold = tdf['bars_held'].mean()

    print(f"Total Trades Executed:   {len(tdf)}")
    print(f"Win Rate:                {win_rate:.2f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Profit Factor:           {pf:.2f}")
    print(f"Average Expectancy:      {avg_r:+.3f} R / trade")
    print(f"Average Holding Time:    {avg_hold:.1f} Days")
    print(f"Gross Profit:            ${gross_profit:,.2f}")
    print(f"Gross Loss:              ${gross_loss:,.2f}")
    print(f"Net Profit:              ${net_pnl:,.2f} (Return: +{(net_pnl/INITIAL_CAPITAL)*100:.2f}%)")
    print(f"Max Portfolio Drawdown:  {max_dd_pct*100:.2f}%")
    print(f"Final Portfolio Equity:  ${(INITIAL_CAPITAL + net_pnl):,.2f}")
    print("-"*65)
    print("Exit Breakdown:")
    print(tdf['reason'].value_counts().to_string())
    print("="*65)
