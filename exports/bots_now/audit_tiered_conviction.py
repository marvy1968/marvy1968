import os, datetime
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import requests

load_dotenv("/home/moonrider1968/.env")
APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID", os.getenv("ALPACA_API_KEY"))
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY", os.getenv("ALPACA_SECRET_KEY"))
DATA_URL = "https://data.alpaca.markets/v2"

SECTOR_MAP = {
    "NVDA": "XLK", "AAPL": "XLK", "MSFT": "XLK",
    "TSLA": "XLY", "AMD":  "XLK", "CVX":  "XLE"
}

def get_alpaca_bars(symbol, timeframe="15Min", days=60):
    headers = {"APCA-API-KEY-ID": APCA_API_KEY_ID, "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY}
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    start_dt = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"timeframe": timeframe, "start": start_dt, "limit": 3000, "adjustment": "raw", "feed": "iex"}
    r = requests.get(url, headers=headers, params=params)
    if r.status_code != 200: return None
    bars = r.json().get("bars", [])
    if not bars: return None
    df = pd.DataFrame(bars)
    df.rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
    df['time'] = pd.to_datetime(df['time'])
    return df

def calculate_adx(df, n=14):
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n).mean()
    up_move = h - h.shift(1)
    down_move = l.shift(1) - l
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    pos_di = 100 * (pd.Series(pos_dm).rolling(n).mean() / (atr + 1e-9))
    neg_di = 100 * (pd.Series(neg_dm).rolling(n).mean() / (atr + 1e-9))
    dx = 100 * (np.abs(pos_di - neg_di) / (pos_di + neg_di + 1e-9))
    return dx.rolling(n).mean(), pos_di, neg_di

def run_simulation(df, sector_df):
    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_mid'] + (2.0 * df['bb_std'])
    df['bb_lower'] = df['bb_mid'] - (2.0 * df['bb_std'])
    
    tr = pd.concat([df['high'] - df['low'], (df['high'] - df['close'].shift(1)).abs(), (df['low'] - df['close'].shift(1)).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    df['vol_sma'] = df['volume'].rolling(20).mean()
    df['sma150'] = df['close'].rolling(150).mean()
    df['adx'], df['pos_di'], df['neg_di'] = calculate_adx(df)
    
    df['cum_pv'] = (df['close'] * df['volume']).cumsum()
    df['cum_v'] = df['volume'].cumsum()
    df['vwap'] = df['cum_pv'] / (df['cum_v'] + 1e-9)

    df['swing_high'] = df['high'].rolling(40).max()
    df['swing_low'] = df['low'].rolling(40).min()

    sector_pass = pd.Series(True, index=df.index)
    if sector_df is not None:
        sector_df['ema50'] = sector_df['close'].ewm(span=50, adjust=False).mean()
        aligned = sector_df.set_index('time').reindex(df['time'], method='ffill')
        sector_pass = (aligned['close'] > aligned['ema50'])

    SLIP = 0.03
    SPREAD = 0.02
    trades = []
    in_pos = False
    entry_px = 0.0
    initial_risk = 0.0
    virtual_sl = 0.0
    target_tp1 = 0.0
    target_tp2 = 0.0
    tier = 0
    mult = 1.0
    score = 0

    for i in range(150, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        atr = row['atr']
        if pd.isna(atr) or atr <= 0: continue

        if in_pos:
            if row['low'] <= (virtual_sl + SPREAD):
                exit_px = virtual_sl - SLIP
                raw_r = (exit_px - entry_px) / initial_risk
                trades.append({
                    "raw_r": raw_r,
                    "score": score,
                    "mult": mult,
                    "tiered_r": raw_r * mult
                })
                in_pos = False
                continue

            if row['high'] >= target_tp1 and tier < 1:
                virtual_sl = max(virtual_sl, entry_px + (0.05 * atr))
                tier = 1
            if row['high'] >= target_tp2 and tier < 2:
                virtual_sl = max(virtual_sl, row['ema21'] - (1.2 * atr))
                tier = 2
            elif tier == 2:
                virtual_sl = max(virtual_sl, row['ema21'] - (1.2 * atr))

        else:
            s_high = prev['swing_high']
            s_low = prev['swing_low']
            fib_range = s_high - s_low
            if fib_range <= (1.0 * atr): continue

            if row['ema9'] > row['ema21'] and row['close'] > row['bb_mid']:
                fib_382 = s_high - (0.382 * fib_range)
                fib_500 = s_high - (0.500 * fib_range)
                fib_618 = s_high - (0.618 * fib_range)

                candidates = [fib_382, fib_500, row['ema21'], row['bb_mid']]
                cluster_window = 0.5 * atr
                best_cluster = max([ [lvl for lvl in candidates if abs(lvl - target) <= cluster_window] for target in candidates ], key=len)

                if (len(best_cluster) / len(candidates)) >= 0.5:
                    buy_zone_max = max(best_cluster) + (0.15 * atr)
                    buy_zone_min = min(best_cluster) - (0.15 * atr)

                    if (row['low'] <= buy_zone_max) and (row['close'] >= buy_zone_min):
                        # Calculate Continuous Confluence Score (0 - 100)
                        score = 50 # Baseline confluence floor
                        if sector_pass.iloc[i]: score += 15
                        if row['close'] > row['sma150']: score += 15
                        if row['close'] >= row['vwap']: score += 10
                        if row['adx'] > 20 and row['pos_di'] > row['neg_di']: score += 10
                        
                        # Tiered Sizing Multiplier
                        if score >= 90:   mult = 1.50
                        elif score >= 80: mult = 1.35
                        elif score >= 70: mult = 1.15
                        else:             mult = 1.00

                        in_pos = True
                        entry_px = row['close'] + SLIP + SPREAD
                        initial_sl = fib_618 - (0.5 * atr)
                        initial_risk = max(entry_px - initial_sl, 1.2 * atr)
                        virtual_sl = entry_px - initial_risk
                        target_tp1 = min(row['bb_upper'], s_high)
                        target_tp2 = s_high + (0.272 * fib_range)
                        tier = 0

    return pd.DataFrame(trades)

def get_stats(r_series):
    r = pd.Series(r_series)
    wins = r[r > 0]
    losses = r[r <= 0]
    cum = r.cumsum()
    peak = cum.cummax()
    max_dd = (peak - cum).max()
    return {
        "net_r": r.sum(),
        "pf": abs(wins.sum() / (losses.sum() + 1e-9)),
        "exp": r.mean(),
        "avg_win": wins.mean() if len(wins) > 0 else 0.0,
        "avg_loss": losses.mean() if len(losses) > 0 else 0.0,
        "max_dd": max_dd
    }

fleet = ["NVDA", "AAPL", "MSFT", "TSLA", "AMD", "CVX"]
all_dfs = []
for sym in fleet:
    stock_df = get_alpaca_bars(sym, "15Min", days=60)
    sec_df = get_alpaca_bars(SECTOR_MAP.get(sym, "XLK"), "15Min", days=60)
    if stock_df is None: continue
    res = run_simulation(stock_df.copy(), sec_df)
    if len(res) > 0: all_dfs.append(res)

tdf = pd.concat(all_dfs) if all_dfs else pd.DataFrame()
s_raw = get_stats(tdf['raw_r'])
s_tier = get_stats(tdf['tiered_r'])

print("=" * 95)
print(f" 🔬 TIERED CONVICTION SIZING ATTRIBUTION (N = {len(tdf)} TRADES)")
print("=" * 95)
print(f"Tier Breakdown: Score <70 (1.0x): {(tdf['mult']==1.0).sum()} | 70-79 (1.15x): {(tdf['mult']==1.15).sum()} | 80-89 (1.35x): {(tdf['mult']==1.35).sum()} | 90+ (1.50x): {(tdf['mult']==1.50).sum()}")
print("-" * 95)
print(f"{'METRIC':<26} | {'FLAT CORE (1.0x)':<30} | {'CONTINUOUS TIERED (1.0x - 1.5x)':<30}")
print("-" * 95)
print(f"{'Total Harvest (R)':<26} | {s_raw['net_r']:<29.2f}R | {s_tier['net_r']:<29.2f}R")
print(f"{'Profit Factor (PF)':<26} | {s_raw['pf']:<30.2f} | {s_tier['pf']:<30.2f}")
print(f"{'Expectancy / Trade':<26} | {s_raw['exp']:<29.2f}R | {s_tier['exp']:<29.2f}R")
print(f"{'Average Winner':<26} | {s_raw['avg_win']:<29.2f}R | {s_tier['avg_win']:<29.2f}R")
print(f"{'Average Loser':<26} | {s_raw['avg_loss']:<29.2f}R | {s_tier['avg_loss']:<29.2f}R")
print(f"{'Max Drawdown (R)':<26} | {s_raw['max_dd']:<29.2f}R | {s_tier['max_dd']:<29.2f}R")
print(f"{'Harvest / MaxDD Ratio':<26} | {s_raw['net_r']/s_raw['max_dd']:<30.2f} | {s_tier['net_r']/s_tier['max_dd']:<30.2f}")
print("=" * 95)
