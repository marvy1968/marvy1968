#!/usr/bin/env python3
"""
Multi-Setup Engine Backtest: 1.0% Risk Per Position
- Universe: 17 Growth Leaders (Setups 1 & 2) + 55 S&P Sector Leaders (Setup 3)
- Account Baseline: $100,000
- Risk per Trade: 1.00% Account Equity (RISK_PCT = args.risk)
- Max Concurrent Slots: 10
- Targets: 2.0R (Growth Trends) | 1.5R or SMA20 (Bull 180s)
"""

import argparse
import yfinance as yf

parser = argparse.ArgumentParser()
parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade (e.g. 0.02 for 2%)")
parser.add_argument("--max_positions", type=int, default=5, help="Max concurrent positions")
parser.add_argument("--output", type=str, default="results.json", help="Output JSON")
args = parser.parse_args()

import pandas as pd
import numpy as np

GROWTH_SYMBOLS = [
    "META", "AMZN", "MSFT", "AAPL", "PLTR", "ARM", "PANW",
    "CRWD", "NFLX", "AVGO", "QCOM", "NOW", "UBER", "COIN",
    "SNOW", "SHOP", "NET"
]

SECTOR_55_SYMBOLS = [
    "AAPL", "MSFT", "AVGO", "ORCL", "AMD",
    "GOOGL", "META", "NFLX", "TMUS", "DIS",
    "AMZN", "HD", "MCD", "NKE", "BKNG",
    "BRK-B", "JPM", "V", "MA", "BAC",
    "LLY", "JNJ", "ABBV", "MRK", "UNH",
    "CAT", "GE", "RTX", "HON", "UNP",
    "WMT", "COST", "PG", "KO", "PEP",
    "XOM", "CVX", "COP", "EOG", "SLB",
    "LIN", "SHW", "FCX", "ECL", "APD",
    "PLD", "AMT", "EQIX", "SPG", "PSA",
    "NEE", "SO", "DUK", "CEG", "AEP"
]

BENCHMARK = "SPY"
INITIAL_CAPITAL = 100000.0
RISK_PCT = args.risk          # <-- 1.0% Equity Risk Per Trade
MAX_POSITIONS = args.max_positions

all_syms = list(set(GROWTH_SYMBOLS + SECTOR_55_SYMBOLS + [BENCHMARK]))
print(f"[*] Downloading daily data for {len(all_syms)} tickers (2-Year horizon)...")
raw = yf.download(all_syms, period="2y", interval="1d", progress=False, group_by="ticker")

spy = raw[BENCHMARK].dropna()
spy_sma50 = spy['Close'].rolling(50).mean()
spy_rs = spy['Close'] / spy_sma50

ticker_data = {}
all_dates = set()

for sym in set(GROWTH_SYMBOLS + SECTOR_55_SYMBOLS):
    try:
        df = raw[sym].dropna().copy()
    except Exception:
        continue
    if len(df) < 165:
        continue

    df['sma20'] = df['Close'].rolling(20).mean()
    df['sma150'] = df['Close'].rolling(150).mean()
    df['sma150_slope'] = df['sma150'] - df['sma150'].shift(15)
    df['vol_sma20'] = df['Volume'].rolling(20).mean()

    tr = np.maximum(
        df['High'] - df['Low'],
        np.maximum(abs(df['High'] - df['Close'].shift(1)), abs(df['Low'] - df['Close'].shift(1)))
    )
    df['atr14'] = tr.rolling(14).mean()
    df['range'] = df['High'] - df['Low']
    df['body'] = abs(df['Close'] - df['Open'])
    df['base_high'] = df['High'].rolling(40).max().shift(1)

    sym_rs = df['Close'] / df['Close'].rolling(50).mean()
    df['mansfield_rs'] = ((sym_rs / spy_rs) - 1.0) * 100.0

    ticker_data[sym] = df
    all_dates.update(df.index.tolist())

sorted_dates = sorted(list(all_dates))

cash = INITIAL_CAPITAL
active_positions = []
trade_log = []
equity_curve = []

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

        # Stop check
        if bar['Low'] <= pos['stop_loss']:
            exit_price = min(bar['Open'], pos['stop_loss'])
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "type": pos['type'], "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "STOP_LOSS", "bars_held": pos['bars_held']
            })
            continue

        # Target check
        if bar['High'] >= pos['target_price']:
            exit_price = max(bar['Open'], pos['target_price'])
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "type": pos['type'], "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "TARGET_HIT", "bars_held": pos['bars_held']
            })
            continue

        # Setup 3 SMA20 Reversion Target
        if pos['type'] == "BULL_180_REVERSAL" and bar['High'] >= bar['sma20'] and bar['sma20'] > pos['entry_price']:
            exit_price = max(bar['Open'], bar['sma20'])
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "type": pos['type'], "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "SMA20_REVERSION", "bars_held": pos['bars_held']
            })
            continue

        # Extension check on Growth setups
        if pos['type'] in ["STAGE2_BREAKOUT", "SMA20_BOUNCE"] and (bar['Close'] - bar['sma20']) >= (2.5 * bar['atr14']):
            exit_price = bar['Close']
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "type": pos['type'], "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "EXTENSION_EXIT", "bars_held": pos['bars_held']
            })
            continue

        # Time exits (3 days for Bull 180, 35 days for Growth Swings)
        max_bars = 3 if pos['type'] == "BULL_180_REVERSAL" else 35
        if pos['bars_held'] >= max_bars:
            exit_price = bar['Close']
            pnl = (exit_price - pos['entry_price']) * pos['shares']
            cash += (pos['shares'] * exit_price)
            trade_log.append({
                "symbol": sym, "type": pos['type'], "pnl": pnl,
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

    # 2. Evaluate Setups
    available_slots = MAX_POSITIONS - len(active_positions)
    if available_slots <= 0:
        continue

    held_symbols = [p['symbol'] for p in active_positions]
    candidates = []

    # Growth Candidates (Setups 1 & 2)
    for sym in GROWTH_SYMBOLS:
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

        if in_stage2 and strong_rs and velez_trend:
            # Stage 2 Breakout
            if (curr['Close'] > curr['base_high']) and (curr['Volume'] >= 1.4 * curr['vol_sma20']):
                sl = round(min(curr['Low'], curr['sma20']) - (0.4 * curr['atr14']), 2)
                rps = curr['Close'] - sl
                if rps > 0.30:
                    candidates.append({"symbol": sym, "type": "STAGE2_BREAKOUT", "entry": curr['Close'], "stop": sl, "target": round(curr['Close'] + (2.0 * rps), 2), "rps": rps})
                    continue

            # 20-SMA Dynamic Bounce
            tested_sma = abs(prev['Low'] - prev['sma20']) <= (0.4 * curr['atr14'])
            bounce = (curr['Close'] > prev['High']) and (curr['Close'] > curr['sma20']) and (curr['Close'] > curr['Open'])
            if tested_sma and bounce:
                sl = round(min(prev['Low'], curr['sma20']) - (0.4 * curr['atr14']), 2)
                rps = curr['Close'] - sl
                if rps > 0.30:
                    candidates.append({"symbol": sym, "type": "SMA20_BOUNCE", "entry": curr['Close'], "stop": sl, "target": round(curr['Close'] + (2.0 * rps), 2), "rps": rps})
                    continue

    # 55-Sector Candidates (Setup 3: Bull 180)
    for sym in SECTOR_55_SYMBOLS:
        if any(c['symbol'] == sym for c in candidates) or sym in held_symbols or sym not in ticker_data:
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

        prior_downtrend = (t1['Close'] < t2['Close']) and (t2['Close'] < t3['Close'])
        t1_red = t1['Close'] < t1['Open']
        t1_elephant = t1['range'] >= (0.85 * t1['atr14']) and (t1['body'] >= 0.55 * t1['range'])

        t0_green = t0['Close'] > t0['Open']
        t0_elephant = t0['range'] >= (0.85 * t0['atr14']) and (t0['body'] >= 0.55 * t0['range'])
        erased_prior = (t0['Close'] >= (t1['High'] - (0.10 * t1['range']))) and (t0['Open'] <= t1['Close'] + (0.25 * t1['range']))

        if prior_downtrend and t1_red and t1_elephant and t0_green and t0_elephant and erased_prior:
            sl = round(min(t0['Low'], t1['Low']) - 0.10, 2)
            rps = t0['Close'] - sl
            if rps > 0.30:
                candidates.append({"symbol": sym, "type": "BULL_180_REVERSAL", "entry": t0['Close'], "stop": sl, "target": round(t0['Close'] + (1.5 * rps), 2), "rps": rps})

    # Allocation with 1.0% Risk
    for cand in candidates:
        if available_slots <= 0:
            break
        risk_dollars = total_equity * RISK_PCT
        shares = int(risk_dollars / cand['rps'])
        cost = shares * cand['entry']

        if shares >= 2 and cost <= cash:
            cash -= cost
            active_positions.append({
                "symbol": cand['symbol'], "type": cand['type'], "shares": shares,
                "entry_price": cand['entry'], "stop_loss": cand['stop'],
                "target_price": cand['target'], "risk_per_share": cand['rps'],
                "bars_held": 0
            })
            available_slots -= 1

# Liquidate remaining
for pos in active_positions:
    df = ticker_data[pos['symbol']]
    exit_price = df.iloc[-1]['Close']
    pnl = (exit_price - pos['entry_price']) * pos['shares']
    trade_log.append({
        "symbol": pos['symbol'], "type": pos['type'], "pnl": pnl,
        "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
        "reason": "END_OF_TEST", "bars_held": pos['bars_held']
    })

tdf = pd.DataFrame(trade_log)

# Calculate Drawdown
peak = INITIAL_CAPITAL
max_dd_pct = 0.0
for eq in equity_curve:
    if eq > peak:
        peak = eq
    dd = (peak - eq) / peak
    if dd > max_dd_pct:
        max_dd_pct = dd

print("\n" + "="*60)
print("  MULTI-SETUP ENGINE: 1.0% RISK PER POSITION RESULTS")
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
    print(f"Net Profit:              ${net_pnl:,.2f} (Return: +{(net_pnl/INITIAL_CAPITAL)*100:.2f}%)")
    print(f"Max Portfolio Drawdown:  {max_dd_pct*100:.2f}%")
    print(f"Final Portfolio Equity:  ${(INITIAL_CAPITAL + net_pnl):,.2f}")
    print("-"*60)
    print("Breakdown by Setup Type:")
    for stype in ["STAGE2_BREAKOUT", "SMA20_BOUNCE", "BULL_180_REVERSAL"]:
        sub = tdf[tdf['type'] == stype]
        if len(sub) > 0:
            s_wins = len(sub[sub['pnl'] > 0])
            s_wr = (s_wins / len(sub)) * 100
            print(f"  [{stype:18s}] Trades: {len(sub):2d} | Win Rate: {s_wr:5.1f}% | Net PnL: ${sub['pnl'].sum():9,.2f} | Exp: {sub['r_mult'].mean():+.3f}R")
    print("="*60)
