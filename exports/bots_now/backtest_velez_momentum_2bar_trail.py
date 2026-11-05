#!/usr/bin/env python3
"""
Oliver Velez Momentum Strategy: 2-Bar Trailing Pivot Stop Evaluation
- Universe: 17 Growth Leaders (2-Year Daily Horizon)
- Trigger: Above Rising 20-SMA, Test of 20-SMA (within 0.5x ATR), Green Ignition Bar
- Exit Model: 2-Bar Trailing Pivot Stop (min(Low_t1, Low_t2) - $0.10)
- Capital & Risk: $100,000 Equity Baseline | 0.50% Equity Risk | Max Slots: 10
- Max Holding Cap: 35 Trading Days
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
MAX_HOLD_DAYS = 35

print(f"[*] Downloading 2-year daily history for {len(SYMBOLS)} symbols...")
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

    df['sma20'] = df['Close'].rolling(20).mean()
    df['sma20_slope'] = df['sma20'] - df['sma20'].shift(5)
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
        loc_idx = df.index.get_loc(curr_date)
        pos['bars_held'] += 1

        # Calculate 2-Bar Trailing Pivot Stop: min(Low_{t-1}, Low_{t-2}) - $0.10
        if loc_idx >= 2:
            t1 = df.iloc[loc_idx - 1]
            t2 = df.iloc[loc_idx - 2]
            pivot_low = min(t1['Low'], t2['Low'])
            new_trail = round(pivot_low - 0.10, 2)
            if new_trail > pos['stop_loss']:
                pos['stop_loss'] = new_trail

        # Check Stop Hit
        if bar['Low'] <= pos['stop_loss']:
            exit_price = min(bar['Open'], pos['stop_loss'])
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "2BAR_TRAIL_STOP", "bars_held": pos['bars_held']
            })
            continue

        # Time Exit Cap (35 Days)
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

    # 2. Scanner Entries
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

        t0 = df.iloc[loc_idx]      # Today
        t1 = df.iloc[loc_idx - 1]  # Yesterday

        # Velez Momentum Criteria:
        # 1. Active uptrend above rising 20-SMA
        in_trend = (t0['Close'] > t0['sma20']) and (t0['sma20_slope'] > 0)
        # 2. Prior bar tested within 0.5x ATR of the 20-SMA
        near_sma = abs(t1['Low'] - t1['sma20']) <= (0.5 * t0['atr14'])
        # 3. Green ignition candle clearing prior high
        ignition = (t0['Close'] > t1['High']) and (t0['Close'] > t0['Open'])

        if in_trend and near_sma and ignition:
            # Initial stop below recent 2-bar low
            initial_stop = round(min(t0['Low'], t1['Low']) - 0.10, 2)
            rps = t0['Close'] - initial_stop
            if rps > 0.30:
                candidates.append({
                    "symbol": sym, "entry": t0['Close'],
                    "stop": initial_stop, "rps": rps
                })

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

print("\n" + "="*65)
print("  VELEZ MOMENTUM: 2-BAR TRAILING PIVOT STOP RESULTS")
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

    print(f"Total Trades Executed:   {len(tdf)}")
    print(f"Win Rate:                {win_rate:.2f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Profit Factor:           {pf:.2f}")
    print(f"Average Expectancy:      {avg_r:+.3f} R / trade")
    print(f"Gross Profit:            ${gross_profit:,.2f}")
    print(f"Gross Loss:              ${gross_loss:,.2f}")
    print(f"Net Profit:              ${net_pnl:,.2f}")
    print(f"Final Portfolio Equity:  ${(INITIAL_CAPITAL + net_pnl):,.2f}")
    print("-"*65)
    print("Exit Breakdown:")
    print(tdf['reason'].value_counts().to_string())
    print("="*65)
