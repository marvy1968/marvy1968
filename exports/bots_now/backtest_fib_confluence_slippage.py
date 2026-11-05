import os, math, datetime
import numpy as np
import pandas as pd
from dotenv import load_dotenv

# Try importing alpaca_trade_api or requests fallback
load_dotenv("/home/moonrider1968/.env")
APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID", os.getenv("ALPACA_API_KEY"))
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY", os.getenv("ALPACA_SECRET_KEY"))
BASE_URL = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
DATA_URL = "https://data.alpaca.markets/v2"

import requests

def get_alpaca_bars(symbol, timeframe="15Min", limit=1500):
    headers = {
        "APCA-API-KEY-ID": APCA_API_KEY_ID,
        "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY
    }
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    # Fetch last 30 trading days of 15m data
    start_dt = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "timeframe": timeframe,
        "start": start_dt,
        "limit": limit,
        "adjustment": "raw",
        "feed": "iex"
    }
    r = requests.get(url, headers=headers, params=params)
    if r.status_code != 200:
        print(f"Error fetching bars for {symbol}: {r.text}")
        return None
    bars = r.json().get("bars", [])
    if not bars:
        return None
    df = pd.DataFrame(bars)
    df.rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
    return df

def simulate_confluence_strategy(df, symbol):
    # 1. Indicator Calculations
    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_mid'] + (2.0 * df['bb_std'])
    df['bb_lower'] = df['bb_mid'] - (2.0 * df['bb_std'])
    
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()

    # Dynamic Rolling Swing High / Low (over 40 bars ~ 10 hours)
    df['swing_high'] = df['high'].rolling(40).max()
    df['swing_low'] = df['low'].rolling(40).min()

    # Friction Modeling: Live Realistic Parameters
    SLIPPAGE_PER_SHARE = 0.03  # $0.03 adverse market order slip
    BID_ASK_SPREAD = 0.02      # $0.02 half-spread cost
    MIN_CONFLUENCE_SCORE = 75  # High-conviction threshold

    trades = []
    in_pos = False
    entry_px = 0.0
    initial_sl = 0.0
    virtual_sl = 0.0
    initial_risk = 0.0
    target_tp1 = 0.0
    target_tp2 = 0.0
    tier = 0

    for i in range(50, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        atr = row['atr']
        if pd.isna(atr) or atr <= 0: continue

        # Intrabay micro-noise simulator
        noise = np.random.normal(0, 0.15 * atr)
        noisy_low = min(row['low'], row['low'] + noise)
        noisy_high = max(row['high'], row['high'] + noise)

        if in_pos:
            # 1. Stop Loss check with adverse slip
            if noisy_low <= (virtual_sl + BID_ASK_SPREAD):
                exit_px = virtual_sl - SLIPPAGE_PER_SHARE
                pnl_dollars = exit_px - entry_px
                pnl_r = pnl_dollars / initial_risk
                trades.append({
                    "pnl_r": pnl_r, "exit_type": "SL", "pnl_dollars": pnl_dollars,
                    "tier": tier, "time": row['time']
                })
                in_pos = False
                continue

            # 2. Target 1 (BB Upper / Swing High) Hit
            if row['high'] >= target_tp1 and tier < 1:
                # Ratchet Stop to Break-Even + 0.05 ATR
                virtual_sl = max(virtual_sl, entry_px + (0.05 * atr))
                tier = 1

            # 3. Target 2 (1.272 Fib Extension) Hit -> Trail along 21 EMA
            if row['high'] >= target_tp2 and tier < 2:
                virtual_sl = max(virtual_sl, row['ema21'] - (1.2 * atr))
                tier = 2
            elif tier == 2:
                virtual_sl = max(virtual_sl, row['ema21'] - (1.2 * atr))

        else:
            # ENTRY LOGIC: Trend confirmation (EMA9 > EMA21 and Close > BB Mid)
            if row['ema9'] > row['ema21'] and row['close'] > row['bb_mid']:
                s_high = prev['swing_high']
                s_low = prev['swing_low']
                fib_range = s_high - s_low
                if fib_range <= (1.0 * atr): continue # Skip tight flat chop

                fib_382 = s_high - (0.382 * fib_range)
                fib_500 = s_high - (0.500 * fib_range)
                fib_618 = s_high - (0.618 * fib_range)

                # Confluence candidate cluster
                candidates = [fib_382, fib_500, row['ema21'], row['bb_mid']]
                cluster_window = 0.5 * atr
                
                # Check cluster density
                best_cluster = []
                for target in candidates:
                    neighbors = [lvl for lvl in candidates if abs(lvl - target) <= cluster_window]
                    if len(neighbors) > len(best_cluster):
                        best_cluster = neighbors

                density_ratio = len(best_cluster) / len(candidates)
                compactness = 1.0 - (np.std(best_cluster) / (atr + 1e-6))
                confluence_score = int(np.clip((density_ratio * 55 + compactness * 30 + 15), 0, 100))

                buy_zone_min = min(best_cluster) - (0.15 * atr)
                buy_zone_max = max(best_cluster) + (0.15 * atr)

                # Trigger: Pullback dipped into buy zone and bounced above EMA9
                if (row['low'] <= buy_zone_max) and (row['close'] >= buy_zone_min) and (confluence_score >= MIN_CONFLUENCE_SCORE):
                    in_pos = True
                    # Fill price with spread & slippage
                    entry_px = row['close'] + SLIPPAGE_PER_SHARE + BID_ASK_SPREAD
                    # Invalidation SL below 61.8% Fib
                    initial_sl = fib_618 - (0.5 * atr)
                    initial_risk = max(entry_px - initial_sl, 1.2 * atr)
                    virtual_sl = entry_px - initial_risk
                    
                    target_tp1 = min(row['bb_upper'], s_high)
                    target_tp2 = s_high + (0.272 * fib_range)
                    tier = 0

    return pd.DataFrame(trades)

print("=" * 85)
print(" 🔬 EQUITIES CONFLUENCE BACKTEST (FIB + EMA + BOLLINGER) WITH REAL SLIPPAGE")
print("=" * 85)

test_fleet = ["NVDA", "AAPL", "MSFT", "TSLA", "AMD", "CVX"]
all_trades = []

for sym in test_fleet:
    df = get_alpaca_bars(sym, timeframe="15Min", limit=1500)
    if df is None or len(df) < 100:
        continue
    
    res = simulate_confluence_strategy(df, sym)
    if len(res) == 0:
        print(f"Ticker: {sym:<6} | No triggered setups meeting >= 75 score.")
        continue
        
    wins = (res['pnl_r'] > 0).sum()
    wr = (wins / len(res)) * 100
    total_r = res['pnl_r'].sum()
    avg_r = res['pnl_r'].mean()
    profit_factor = abs(res[res['pnl_r'] > 0]['pnl_r'].sum() / (res[res['pnl_r'] < 0]['pnl_r'].sum() + 1e-9))
    
    print(f"Ticker: {sym:<6} | Setups: {len(res):<3} | Win Rate: {wr:5.1f}% | Net R: {total_r:+6.2f}R | PF: {profit_factor:4.2f} | Avg: {avg_r:+.2f}R")
    all_trades.append(res)

if all_trades:
    combined = pd.concat(all_trades)
    total_wins = (combined['pnl_r'] > 0).sum()
    overall_wr = (total_wins / len(combined)) * 100
    overall_r = combined['pnl_r'].sum()
    overall_pf = abs(combined[combined['pnl_r'] > 0]['pnl_r'].sum() / (combined[combined['pnl_r'] < 0]['pnl_r'].sum() + 1e-9))
    print("=" * 85)
    print(f"🏆 FLEET AGGREGATE | Total Trades: {len(combined)} | Win Rate: {overall_wr:.1f}% | Net Profit: {overall_r:+.2f}R | Fleet PF: {overall_pf:.2f}")
    print("=" * 85)
