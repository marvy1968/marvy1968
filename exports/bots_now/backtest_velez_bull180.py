#!/usr/bin/env python3
"""
Oliver Velez Bull 180 / Elephant Bar Reversal Daily Backtest
- Universe: 17 Growth Leaders
- Account Equity: $100,000 baseline
- Risk per Trade: 0.50% account equity
- Max Concurrent Positions: 10
- Target: 1.5R or Daily 20-SMA | Max Hold: 3 Days
"""

import yfinance as yf
import pandas as pd
import numpy as np

SYMBOLS = [
    "META", "AMZN", "MSFT", "AAPL", "PLTR", "ARM", "PANW", 
    "CRWD", "NFLX", "AVGO", "QCOM", "NOW", "UBER", "COIN", 
    "SNOW", "SHOP", "NET"
]

INITIAL_CAPITAL = 100000.0
RISK_PCT = 0.005
MAX_POSITIONS = 10
TARGET_R = 1.5
HOLD_DAYS_MAX = 3

print(f"[*] Fetching historical daily data for {len(SYMBOLS)} symbols (2-Year horizon)...")
raw = yf.download(SYMBOLS, period="2y", interval="1d", progress=False, group_by="ticker")

ticker_data = {}
all_dates = set()

for sym in SYMBOLS:
    try:
        df = raw[sym].dropna().copy()
    except Exception:
        continue
    if len(df) < 60:
        continue

    tr = np.maximum(
        df['High'] - df['Low'],
        np.maximum(abs(df['High'] - df['Close'].shift(1)), abs(df['Low'] - df['Close'].shift(1)))
    )
    df['atr14'] = tr.rolling(14).mean()
    df['range'] = df['High'] - df['Low']
    df['body'] = abs(df['Close'] - df['Open'])
    df['sma20'] = df['Close'].rolling(20).mean()
    
    ticker_data[sym] = df
    all_dates.update(df.index.tolist())

sorted_dates = sorted(list(all_dates))

cash = INITIAL_CAPITAL
active_positions = []
trade_log = []

for i in range(25, len(sorted_dates)):
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
        
        # Stop check
        if bar['Low'] <= pos['stop_loss']:
            exit_price = min(bar['Open'], pos['stop_loss'])
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "STOP_LOSS", "bars_held": pos['bars_held']
            })
            continue

        # Target check (1.5R target or reached daily 20-SMA)
        hit_target = bar['High'] >= pos['target_price']
        hit_sma20 = bar['High'] >= bar['sma20'] and bar['sma20'] > pos['entry_price']
        
        if hit_target or hit_sma20:
            effective_target = pos['target_price'] if hit_target else bar['sma20']
            exit_price = max(bar['Open'], effective_target)
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "TARGET_HIT" if hit_target else "SMA20_REVERSION", "bars_held": pos['bars_held']
            })
            continue

        # 3-Day Time Exit
        if pos['bars_held'] >= HOLD_DAYS_MAX:
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

    # 2. Evaluate Setups
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
        if loc_idx < 4:
            continue

        t0 = df.iloc[loc_idx]      # Today (Bull 180 Green Elephant)
        t1 = df.iloc[loc_idx - 1]  # Yesterday (Red Elephant)
        t2 = df.iloc[loc_idx - 2]
        t3 = df.iloc[loc_idx - 3]
        
        # 1. Prior Context: 2-3 consecutive down moves into exhaustion
        prior_downtrend = (t1['Close'] < t2['Close']) and (t2['Close'] < t3['Close'])
        
        # 2. Bar 1: Red Elephant Bar (Range >= 0.9 * ATR14, solid body, closed near low)
        t1_red = t1['Close'] < t1['Open']
        t1_elephant = t1['range'] >= (0.9 * t1['atr14']) and (t1['body'] >= 0.60 * t1['range'])
        
        # 3. Bar 2: Bull 180 Engulfing Green Elephant Bar
        t0_green = t0['Close'] > t0['Open']
        t0_elephant = t0['range'] >= (0.9 * t0['atr14']) and (t0['body'] >= 0.60 * t0['range'])
        
        # 4. Total Wipeout: Bar 2 closes above Bar 1 High and opens near/below Bar 1 Close
        erased_prior = (t0['Close'] >= t1['High']) and (t0['Open'] <= t1['Close'] + (0.2 * t1['range']))
        
        if prior_downtrend and t1_red and t1_elephant and t0_green and t0_elephant and erased_prior:
            entry_price = t0['Close']
            stop_loss = round(min(t0['Low'], t1['Low']) - 0.10, 2)
            risk_per_share = entry_price - stop_loss
            
            if risk_per_share > 0.30:
                target_price = round(entry_price + (TARGET_R * risk_per_share), 2)
                candidates.append({
                    "symbol": sym, "entry_price": round(entry_price, 2),
                    "stop_loss": stop_loss, "target_price": target_price,
                    "risk_per_share": round(risk_per_share, 2)
                })

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
                "entry_price": cand['entry_price'], "stop_loss": cand['stop_loss'],
                "target_price": cand['target_price'], "risk_per_share": cand['risk_per_share'],
                "bars_held": 0
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
print("\n" + "="*55)
print("  OLIVER VELEZ BULL 180 / ELEPHANT REVERSAL RESULTS")
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
