#!/usr/bin/env python3
"""
Oliver Velez: Momentum Strategy vs. Reversal Strategy (Head-to-Head)
- Strategy A (MOMENTUM): 20-SMA Breakout/Bounce + Bar-by-Bar Trailing Stop
- Strategy B (REVERSAL): 3-Bar Exhaustion Extended >= 1.5x ATR -> 20-SMA Target
- Universe: 17 Growth Leaders (2-Year Daily Horizon)
- Risk: 0.50% Account Equity | Max Slots: 10
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

print(f"[*] Fetching historical daily data for {len(SYMBOLS)} symbols...")
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
    df['sma200'] = df['Close'].rolling(200).mean()
    tr = np.maximum(
        df['High'] - df['Low'],
        np.maximum(abs(df['High'] - df['Close'].shift(1)), abs(df['Low'] - df['Close'].shift(1)))
    )
    df['atr14'] = tr.rolling(14).mean()
    df['range'] = df['High'] - df['Low']
    df['body'] = abs(df['Close'] - df['Open'])

    ticker_data[sym] = df
    all_dates.update(df.index.tolist())

sorted_dates = sorted(list(all_dates))

def run_simulation(strategy_type="MOMENTUM"):
    cash = INITIAL_CAPITAL
    active_positions = []
    trade_log = []

    for i in range(25, len(sorted_dates)):
        curr_date = sorted_dates[i]

        # 1. Manage Active Positions
        surviving = []
        for pos in active_positions:
            sym = pos['symbol']
            df = ticker_data[sym]
            if curr_date not in df.index:
                surviving.append(pos)
                continue

            bar = df.loc[curr_date]
            loc_idx = df.index.get_loc(curr_date)
            prev_bar = df.iloc[loc_idx - 1]
            pos['bars_held'] += 1

            if strategy_type == "MOMENTUM":
                # Velez Staircase: Trail stop to previous bar's Low
                new_trail = round(prev_bar['Low'] - 0.10, 2)
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
                        "reason": "TRAIL_STOP"
                    })
                    continue

                if pos['bars_held'] >= 25:
                    exit_price = bar['Close']
                    pnl = (exit_price - pos['entry_price']) * pos['shares']
                    cash += (pos['shares'] * exit_price)
                    trade_log.append({
                        "symbol": sym, "pnl": pnl,
                        "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                        "reason": "TIME_EXIT"
                    })
                    continue

            elif strategy_type == "REVERSAL":
                # Reversal Target: Touch 20-SMA
                if bar['High'] >= bar['sma20'] and bar['sma20'] > pos['entry_price']:
                    exit_price = max(bar['Open'], bar['sma20'])
                    pnl = (exit_price - pos['entry_price']) * pos['shares']
                    cash += (pos['shares'] * exit_price)
                    trade_log.append({
                        "symbol": sym, "pnl": pnl,
                        "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                        "reason": "SMA20_TARGET"
                    })
                    continue

                # Stop check
                if bar['Low'] <= pos['stop_loss']:
                    exit_price = min(bar['Open'], pos['stop_loss'])
                    pnl = (exit_price - pos['entry_price']) * pos['shares']
                    cash += (pos['shares'] * exit_price)
                    trade_log.append({
                        "symbol": sym, "pnl": pnl,
                        "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                        "reason": "STOP_LOSS"
                    })
                    continue

                if pos['bars_held'] >= 5:
                    exit_price = bar['Close']
                    pnl = (exit_price - pos['entry_price']) * pos['shares']
                    cash += (pos['shares'] * exit_price)
                    trade_log.append({
                        "symbol": sym, "pnl": pnl,
                        "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                        "reason": "TIME_EXIT"
                    })
                    continue

            surviving.append(pos)

        active_positions = surviving

        open_equity = sum(
            p['shares'] * ticker_data[p['symbol']].loc[curr_date]['Close']
            for p in active_positions if curr_date in ticker_data[p['symbol']].index
        )
        total_equity = cash + open_equity

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

            t0 = df.iloc[loc_idx]
            t1 = df.iloc[loc_idx - 1]
            t2 = df.iloc[loc_idx - 2]
            t3 = df.iloc[loc_idx - 3]

            if strategy_type == "MOMENTUM":
                # Velez Momentum: In uptrend, tested 20-SMA, green ignition candle
                in_trend = (t0['Close'] > t0['sma20']) and (t0['sma20_slope'] > 0)
                near_sma = abs(t1['Low'] - t1['sma20']) <= (0.5 * t0['atr14'])
                ignition = (t0['Close'] > t1['High']) and (t0['Close'] > t0['Open'])
                
                if in_trend and near_sma and ignition:
                    stop_loss = round(min(t0['Low'], t1['Low']) - 0.10, 2)
                    rps = t0['Close'] - stop_loss
                    if rps > 0.30:
                        candidates.append({"symbol": sym, "entry": t0['Close'], "stop": stop_loss, "rps": rps})

            elif strategy_type == "REVERSAL":
                # Velez Reversal: 3 consecutive down bars, extended >= 1.5x ATR below 20-SMA, green snapback
                down_three = (t1['Close'] < t2['Close']) and (t2['Close'] < t3['Close'])
                stretched = (t1['sma20'] - t1['Close']) >= (1.5 * t1['atr14'])
                snap_candle = (t0['Close'] > t0['Open']) and (t0['Close'] > (t1['Low'] + 0.5 * t1['range']))

                if down_three and stretched and snap_candle:
                    stop_loss = round(min(t0['Low'], t1['Low']) - 0.10, 2)
                    rps = t0['Close'] - stop_loss
                    if rps > 0.30:
                        candidates.append({"symbol": sym, "entry": t0['Close'], "stop": stop_loss, "rps": rps})

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

    tdf = pd.DataFrame(trade_log)
    return tdf

print("\nRunning Velez Momentum Simulation...")
mom_df = run_simulation("MOMENTUM")

print("Running Velez Reversal Simulation...")
rev_df = run_simulation("REVERSAL")

print("\n" + "="*65)
print("       OLIVER VELEZ HEAD-TO-HEAD STRATEGY COMPARISON")
print("="*65)

for name, df in [("MOMENTUM (20-SMA Staircase Ride)", mom_df), ("REVERSAL (Rubber-Band Reversion)", rev_df)]:
    if len(df) == 0:
        print(f"\n[{name}] No trades executed.")
        continue
    wins = df[df['pnl'] > 0]
    losses = df[df['pnl'] <= 0]
    wr = (len(wins) / len(df)) * 100
    gp = wins['pnl'].sum()
    gl = abs(losses['pnl'].sum())
    pf = (gp / gl) if gl > 0 else 99.99
    net = df['pnl'].sum()
    avg_r = df['r_mult'].mean()

    print(f"\n--- {name} ---")
    print(f"Total Trades:       {len(df):3d}  | Win Rate:       {wr:5.2f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Profit Factor:      {pf:5.2f}  | Expectancy:     {avg_r:+.3f} R / trade")
    print(f"Gross Profit:       ${gp:9,.2f} | Gross Loss:     ${gl:9,.2f}")
    print(f"Net Profit:         ${net:9,.2f} | Final Equity:   ${(INITIAL_CAPITAL + net):9,.2f}")
    print("Exits:\n" + df['reason'].value_counts().to_string())

print("="*65)
