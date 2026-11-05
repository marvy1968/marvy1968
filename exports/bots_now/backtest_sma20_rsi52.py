#!/usr/bin/env python3
"""
Test 20-SMA Dynamic Bounce with Calibrated RSI(14) <= 52 Pullback Filter
- Universe: 55 S&P Sector Leaders
- Macro: Stage 2 Uptrend (Close > SMA150 sloping up, Mansfield RS > 0)
- Filter: Prior Day RSI(14) <= 52 (Realistic pullback zone in an active trend)
- Horizon: 2 Years Daily Bars | Risk: 0.50% Equity | Max Slots: 10
- Target: 2.0R | Extension Exit: 2.5x ATR
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

BENCHMARK = "SPY"
INITIAL_CAPITAL = 100000.0
RISK_PCT = 0.005
MAX_POSITIONS = 10
TARGET_R = 2.0
MAX_HOLD_DAYS = 35

all_syms = list(set(SECTOR_55_SYMBOLS + [BENCHMARK]))
print(f"[*] Downloading 2-year daily data for {len(all_syms)} symbols...")
raw = yf.download(all_syms, period="2y", interval="1d", progress=False, group_by="ticker")

spy = raw[BENCHMARK].dropna()
spy_sma50 = spy['Close'].rolling(50).mean()
spy_rs = spy['Close'] / spy_sma50

ticker_data = {}
all_dates = set()

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / np.where(avg_loss == 0, 1e-5, avg_loss)
    return 100.0 - (100.0 / (1.0 + rs))

for sym in SECTOR_55_SYMBOLS:
    try:
        df = raw[sym].dropna().copy()
    except Exception:
        continue
    if len(df) < 165:
        continue

    df['sma20'] = df['Close'].rolling(20).mean()
    df['sma150'] = df['Close'].rolling(150).mean()
    df['sma150_slope'] = df['sma150'] - df['sma150'].shift(15)
    df['rsi14'] = calc_rsi(df['Close'], 14)

    tr = np.maximum(
        df['High'] - df['Low'],
        np.maximum(abs(df['High'] - df['Close'].shift(1)), abs(df['Low'] - df['Close'].shift(1)))
    )
    df['atr14'] = tr.rolling(14).mean()
    
    sym_rs = df['Close'] / df['Close'].rolling(50).mean()
    df['mansfield_rs'] = ((sym_rs / spy_rs) - 1.0) * 100.0

    ticker_data[sym] = df
    all_dates.update(df.index.tolist())

sorted_dates = sorted(list(all_dates))

cash = INITIAL_CAPITAL
active_positions = []
trade_log = []

for i in range(160, len(sorted_dates)):
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

        if bar['High'] >= pos['target_price']:
            exit_price = max(bar['Open'], pos['target_price'])
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "TARGET_HIT", "bars_held": pos['bars_held']
            })
            continue

        if (bar['Close'] - bar['sma20']) >= (2.5 * bar['atr14']):
            exit_price = bar['Close']
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "EXTENSION_EXIT", "bars_held": pos['bars_held']
            })
            continue

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

    # 2. Evaluate Setups
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
        if loc_idx < 3:
            continue

        curr = df.iloc[loc_idx]
        prev = df.iloc[loc_idx - 1]

        in_stage2 = (curr['Close'] > curr['sma150']) and (curr['sma150_slope'] > 0)
        strong_rs = curr['mansfield_rs'] > 0
        velez_trend = (curr['Close'] > curr['sma20']) and (curr['sma20'] > curr['sma150'])

        if not (in_stage2 and strong_rs and velez_trend):
            continue

        tested_sma = abs(prev['Low'] - prev['sma20']) <= (0.4 * curr['atr14'])
        bounce = (curr['Close'] > prev['High']) and (curr['Close'] > curr['sma20']) and (curr['Close'] > curr['Open'])
        rsi_pulled_back = prev['rsi14'] <= 52.0

        if tested_sma and bounce and rsi_pulled_back:
            stop_loss = round(min(prev['Low'], curr['sma20']) - (0.4 * curr['atr14']), 2)
            risk_per_share = curr['Close'] - stop_loss
            if risk_per_share > 0.30:
                target_price = round(curr['Close'] + (TARGET_R * risk_per_share), 2)
                candidates.append({
                    "symbol": sym, "entry_price": curr['Close'],
                    "stop_loss": stop_loss, "target_price": target_price,
                    "risk_per_share": risk_per_share
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
print("\n" + "="*60)
print("  55 S&P SECTORS: 20-SMA BOUNCE (RSI14 <= 52 CALIBRATED)")
print("="*60)

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
    print("-"*60)
    print("Exit Breakdown:")
    print(tdf['reason'].value_counts().to_string())
    print("="*60)
