#!/usr/bin/env python3
"""
Oliver Velez Guerrilla "Gap-and-Snap" Daily Backtest Engine
- Universe: 17 Growth Leaders
- Account Equity: $100,000 (starting baseline)
- Risk per Trade: 0.50% account equity
- Max Concurrent Positions: 10
- Horizon: 2 Years Daily Bars
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

SYMBOLS = [
    "META", "AMZN", "MSFT", "AAPL", "PLTR", "ARM", "PANW", 
    "CRWD", "NFLX", "AVGO", "QCOM", "NOW", "UBER", "COIN", 
    "SNOW", "SHOP", "NET"
]

INITIAL_CAPITAL = 100000.0
RISK_PCT = 0.005
MAX_POSITIONS = 10
TARGET_R = 1.5
HOLD_DAYS_MAX = 2

print(f"[*] Fetching historical daily data for {len(SYMBOLS)} symbols (2-Year horizon)...")
raw = yf.download(SYMBOLS, period="2y", interval="1d", progress=False, group_by="ticker")

# Process individual ticker data
ticker_data = {}
all_dates = set()

for sym in SYMBOLS:
    try:
        df = raw[sym].dropna().copy()
    except Exception:
        continue
    if len(df) < 50:
        continue

    # Range metrics & ATR
    tr = np.maximum(
        df['High'] - df['Low'],
        np.maximum(abs(df['High'] - df['Close'].shift(1)), abs(df['Low'] - df['Close'].shift(1)))
    )
    df['atr14'] = tr.rolling(14).mean()
    df['range'] = df['High'] - df['Low']
    df['close_pos'] = (df['Close'] - df['Low']) / np.where(df['range'] > 0, df['range'], 1e-5)
    
    # Trend reference
    df['sma20'] = df['Close'].rolling(20).mean()
    
    ticker_data[sym] = df
    all_dates.update(df.index.tolist())

sorted_dates = sorted(list(all_dates))

# Backtest State
cash = INITIAL_CAPITAL
active_positions = [] # list of dicts
trade_log = []

for i in range(20, len(sorted_dates)):
    curr_date = sorted_dates[i]
    
    # 1. Manage Active Positions (Check Target, Stop, or Time Exits)
    surviving_positions = []
    for pos in active_positions:
        sym = pos['symbol']
        df = ticker_data[sym]
        if curr_date not in df.index:
            surviving_positions.append(pos)
            continue
            
        bar = df.loc[curr_date]
        pos['bars_held'] += 1
        
        # Check Stop Hit
        if bar['Low'] <= pos['stop_loss']:
            exit_price = min(bar['Open'], pos['stop_loss'])
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "entry_date": pos['entry_date'], "exit_date": curr_date,
                "entry": pos['entry_price'], "exit": exit_price, "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "STOP_LOSS", "bars_held": pos['bars_held']
            })
            continue

        # Check Target Hit
        if bar['High'] >= pos['target_price']:
            exit_price = max(bar['Open'], pos['target_price'])
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "entry_date": pos['entry_date'], "exit_date": curr_date,
                "entry": pos['entry_price'], "exit": exit_price, "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "TARGET_HIT", "bars_held": pos['bars_held']
            })
            continue

        # Check Time Stop (Max Hold Reached)
        if pos['bars_held'] >= HOLD_DAYS_MAX:
            exit_price = bar['Close']
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "entry_date": pos['entry_date'], "exit_date": curr_date,
                "entry": pos['entry_price'], "exit": exit_price, "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "TIME_EXIT", "bars_held": pos['bars_held']
            })
            continue

        surviving_positions.append(pos)

    active_positions = surviving_positions

    # Current Portfolio Equity
    open_equity = sum(
        p['shares'] * ticker_data[p['symbol']].loc[curr_date]['Close']
        for p in active_positions if curr_date in ticker_data[p['symbol']].index
    )
    total_equity = cash + open_equity

    # 2. Identify Candidates for Today's Setup
    available_slots = MAX_POSITIONS - len(active_positions)
    if available_slots <= 0:
        continue

    held_symbols = [p['symbol'] for p in active_positions]
    candidates = []

    for sym in SYMBOLS:
        if sym in held_symbols or sym not in ticker_data:
            continue
        df = ticker_data[sym]
        if curr_date not in df.index:
            continue
            
        loc_idx = df.index.get_loc(curr_date)
        if loc_idx < 3:
            continue

        t0 = df.iloc[loc_idx]      # Today
        t1 = df.iloc[loc_idx - 1]  # Yesterday
        t2 = df.iloc[loc_idx - 2]  # Two days ago
        
        # Rule 1: Down at least 2 days in a row
        down_two = (t1['Close'] < t2['Close']) and (t2['Close'] < df.iloc[loc_idx - 3]['Close'])
        
        # Rule 2: Yesterday was a wide-range down bar closing near bottom
        wide_bar = t1['range'] >= (0.9 * t1['atr14'])
        closed_weak = t1['close_pos'] <= 0.25
        
        # Rule 3: Today Gaps Down at the open
        gapped_down = t0['Open'] < t1['Close']
        
        # Rule 4: Snap Trigger - High of today reclaims above prior day's Low
        snap_trigger = t0['High'] > (t1['Low'] + 0.10)
        
        if down_two and wide_bar and closed_weak and gapped_down and snap_trigger:
            entry_price = t1['Low'] + 0.10
            stop_loss = max(t0['Low'] - 0.10, entry_price * 0.90) # fail-safe cap
            risk_per_share = entry_price - stop_loss
            
            if risk_per_share > 0.30: # Filter out sub-penny noise
                target_price = round(entry_price + (TARGET_R * risk_per_share), 2)
                candidates.append({
                    "symbol": sym, "entry_price": round(entry_price, 2),
                    "stop_loss": round(stop_loss, 2), "target_price": target_price,
                    "risk_per_share": round(risk_per_share, 2)
                })

    # 3. Size and Allocate Slots
    for cand in candidates:
        if available_slots <= 0:
            break
        risk_dollars = total_equity * RISK_PCT
        shares = int(risk_dollars / cand['risk_per_share'])
        cost = shares * cand['entry_price']
        
        if shares >= 2 and cost <= cash:
            cash -= cost
            active_positions.append({
                "symbol": cand['symbol'], "shares": shares,
                "entry_date": curr_date, "entry_price": cand['entry_price'],
                "stop_loss": cand['stop_loss'], "target_price": cand['target_price'],
                "risk_per_share": cand['risk_per_share'], "bars_held": 0
            })
            available_slots -= 1

# Liquidate any remaining positions at end of test
final_date = sorted_dates[-1]
for pos in active_positions:
    sym = pos['symbol']
    df = ticker_data[sym]
    exit_price = df.iloc[-1]['Close']
    pnl = (exit_price - pos['entry_price']) * pos['shares']
    trade_log.append({
        "symbol": sym, "entry_date": pos['entry_date'], "exit_date": final_date,
        "entry": pos['entry_price'], "exit": exit_price, "pnl": pnl,
        "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
        "reason": "END_OF_TEST", "bars_held": pos['bars_held']
    })

# Compute Statistics
tdf = pd.DataFrame(trade_log)
print("\n" + "="*55)
print("  OLIVER VELEZ 'GAP-AND-SNAP' GUERRILLA RESULTS")
print("="*55)

if len(tdf) == 0:
    print("[!] Zero qualifying setups triggered during period.")
else:
    wins = tdf[tdf['pnl'] > 0]
    losses = tdf[tdf['pnl'] <= 0]
    win_rate = (len(wins) / len(tdf)) * 100
    gross_profit = wins['pnl'].sum()
    gross_loss = abs(losses['pnl'].sum())
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 99.99
    net_pnl = tdf['pnl'].sum()
    avg_r = tdf['r_mult'].mean()
    
    print(f"Total Trades Executed:   {len(tdf)}")
    print(f"Win Rate:                {win_rate:.2f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Profit Factor:           {pf:.2f}")
    print(f"Average Expectancy:      {avg_r:+.3f} R / trade")
    print(f"Gross Profit:            ${gross_profit:,.2f}")
    print(f"Gross Loss:              ${gross_loss:,.2f}")
    print(f"Net Profit:              ${net_pnl:,.2f}")
    print(f"Final Portfolio Equity:  ${(INITIAL_CAPITAL + net_pnl):,.2f}")
    print("-"*55)
    print("Exit Breakdown:")
    print(tdf['reason'].value_counts().to_string())
    print("="*55)
