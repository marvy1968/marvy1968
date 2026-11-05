#!/usr/bin/env python3
"""
Stan Weinstein Stage 2 First Pullback / Dry-Volume Retest
- Universe: 17 High-Beta Growth Leaders (2-Year Daily Horizon)
- Criteria:
    1. Trend: Close > SMA150 (rising slope), Mansfield RS > 0, Close > SMA20
    2. Retest: Low of pullback tests within 0.5x ATR14 of rising 20-SMA
    3. Dry Volume: Prior pullback day's volume <= 0.75x 20-day SMA Volume (Supply dries up)
    4. Trigger: Current bar is green (Close > Open) and closes above prior bar's High
- Risk: 0.50% Account Equity | Max Slots: 10
- Target: 2.0R | Extension Exit: Close - SMA20 >= 2.5x ATR14 | Max Hold: 35 Days
"""

import yfinance as yf
import pandas as pd
import numpy as np

GROWTH_SYMBOLS = [
    "META", "AMZN", "MSFT", "AAPL", "PLTR", "ARM", "PANW",
    "CRWD", "NFLX", "AVGO", "QCOM", "NOW", "UBER", "COIN",
    "SNOW", "SHOP", "NET"
]

BENCHMARK = "SPY"
INITIAL_CAPITAL = 100000.0
RISK_PCT = 0.005
MAX_POSITIONS = 10
TARGET_R = 2.0
MAX_HOLD_DAYS = 35

all_syms = list(set(GROWTH_SYMBOLS + [BENCHMARK]))
print(f"[*] Downloading 2-year daily history for {len(all_syms)} symbols...")
raw = yf.download(all_syms, period="2y", interval="1d", progress=False, group_by="ticker")

spy = raw[BENCHMARK].dropna()
spy_sma50 = spy['Close'].rolling(50).mean()
spy_rs = spy['Close'] / spy_sma50

ticker_data = {}
all_dates = set()

for sym in GROWTH_SYMBOLS:
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
                "symbol": sym, "pnl": pnl,
                "r_mult": (exit_price - pos['entry_price']) / pos['risk_per_share'],
                "reason": "STOP_LOSS", "bars_held": pos['bars_held']
            })
            continue

        # Target check (+2.0R)
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

        # Extension check (2.5x ATR away from 20-SMA)
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

    # 2. Scanner Entries: Weinstein Dry-Volume Retest
    available_slots = MAX_POSITIONS - len(active_positions)
    if available_slots <= 0:
        continue

    held_symbols = [p['symbol'] for p in active_positions]
    candidates = []

    for sym in GROWTH_SYMBOLS:
        if sym in held_symbols or sym not in ticker_data:
            continue
        df = ticker_data[sym]
        if curr_date not in df.index:
            continue
        loc_idx = df.index.get_loc(curr_date)
        if loc_idx < 3:
            continue

        t0 = df.iloc[loc_idx]      # Today (Ignition bar)
        t1 = df.iloc[loc_idx - 1]  # Yesterday (Dry pullback bar)

        in_stage2 = (t0['Close'] > t0['sma150']) and (t0['sma150_slope'] > 0)
        strong_rs = t0['mansfield_rs'] > 0
        above_sma20 = t0['Close'] > t0['sma20']

        if not (in_stage2 and strong_rs and above_sma20):
            continue

        # Retest mechanics:
        # 1. Pullback tests 20-SMA within 0.5x ATR
        tested_sma = abs(t1['Low'] - t1['sma20']) <= (0.5 * t0['atr14'])
        # 2. Dry Volume: Pullback bar traded on light volume (<= 0.75x 20-day SMA Volume)
        dry_volume = t1['Volume'] <= (0.75 * t1['vol_sma20'])
        # 3. Reversal confirmation: Green bar closing above prior bar's High
        reversal_candle = (t0['Close'] > t1['High']) and (t0['Close'] > t0['Open'])

        if tested_sma and dry_volume and reversal_candle:
            stop_loss = round(min(t1['Low'], t0['sma20']) - (0.4 * t0['atr14']), 2)
            rps = t0['Close'] - stop_loss
            if rps > 0.30:
                target_price = round(t0['Close'] + (TARGET_R * rps), 2)
                candidates.append({
                    "symbol": sym, "entry": t0['Close'], "stop": stop_loss,
                    "target": target_price, "rps": rps
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
print("  WEINSTEIN STAGE 2 DRY RETEST: 17 GROWTH LEADERS RESULTS")
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
