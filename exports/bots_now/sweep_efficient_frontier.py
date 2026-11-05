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

def prepare_data(df, sector_df, spy_df):
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
    
    # VWAP
    df['cum_pv'] = (df['close'] * df['volume']).cumsum()
    df['cum_v'] = df['volume'].cumsum()
    df['vwap'] = df['cum_pv'] / (df['cum_v'] + 1e-9)

    df['swing_high'] = df['high'].rolling(40).max()
    df['swing_low'] = df['low'].rolling(40).min()

    # Sector & SPY features
    if sector_df is not None:
        sector_df['sec_ema50'] = sector_df['close'].ewm(span=50, adjust=False).mean()
        aligned_sec = sector_df.set_index('time').reindex(df['time'], method='ffill')
        df['sec_pass'] = (aligned_sec['close'] > aligned_sec['sec_ema50'])
    else:
        df['sec_pass'] = True

    if spy_df is not None:
        aligned_spy = spy_df.set_index('time').reindex(df['time'], method='ffill')
        stock_ret = df['close'].pct_change(80) # ~20 trading hours rolling
        spy_ret = aligned_spy['close'].pct_change(80)
        df['rel_strength'] = (stock_ret > spy_ret)
    else:
        df['rel_strength'] = True

    return df

def simulate_scored_grid(prepared_fleet, m1_thresh, m2_thresh):
    # DETERMINISTIC FRICTION CONSTANTS
    SLIP = 0.03
    SPREAD = 0.02
    
    all_trades = []

    for sym, df in prepared_fleet.items():
        in_pos = False
        entry_px = 0.0
        initial_risk = 0.0
        virtual_sl = 0.0
        target_tp1 = 0.0
        target_tp2 = 0.0
        tier = 0
        max_fav_px = 0.0
        max_adv_px = 0.0

        for i in range(150, len(df)):
            row = df.iloc[i]
            prev = df.iloc[i-1]
            atr = row['atr']
            if pd.isna(atr) or atr <= 0: continue

            # DETERMINISTIC STOP & TARGET CHECKS
            if in_pos:
                max_fav_px = max(max_fav_px, row['high'])
                max_adv_px = min(max_adv_px, row['low'])
                
                # Check Stop Loss (Worst-case bar low vs SL + half spread)
                if row['low'] <= (virtual_sl + SPREAD):
                    exit_px = virtual_sl - SLIP
                    pnl_r = (exit_px - entry_px) / initial_risk
                    mae_r = (entry_px - max_adv_px) / initial_risk
                    mfe_r = (max_fav_px - entry_px) / initial_risk
                    all_trades.append({"symbol": sym, "pnl_r": pnl_r, "mae_r": max(0, mae_r), "mfe_r": max(0, mfe_r)})
                    in_pos = False
                    continue

                # Target 1 (BB Upper / Swing High) -> BE Snap
                if row['high'] >= target_tp1 and tier < 1:
                    virtual_sl = max(virtual_sl, entry_px + (0.05 * atr))
                    tier = 1

                # Target 2 (1.272 Fib Extension) -> Trailing EMA21
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

                # MODULE 1: MACRO & TREND QUALITY SCORE (100 Max)
                m1_score = 0
                if row['close'] > row['sma150']: m1_score += 25
                if row['sec_pass']: m1_score += 25
                if row['rel_strength']: m1_score += 20
                if row['ema9'] > row['ema21']: m1_score += 15
                if row['adx'] > 18: m1_score += 15

                if m1_score < m1_thresh:
                    continue

                # MODULE 2: TRADE CONFLUENCE & INTRADAY SCORE (100 Max)
                fib_382 = s_high - (0.382 * fib_range)
                fib_500 = s_high - (0.500 * fib_range)
                fib_618 = s_high - (0.618 * fib_range)

                candidates = [fib_382, fib_500, row['ema21'], row['bb_mid']]
                cluster_window = 0.5 * atr
                best_cluster = max([ [lvl for lvl in candidates if abs(lvl - target) <= cluster_window] for target in candidates ], key=len)

                m2_score = 0
                # Structural Confluence (up to 45 pts)
                if (len(best_cluster) / len(candidates)) >= 0.5: m2_score += 25
                if row['close'] > row['bb_mid']: m2_score += 20
                
                # Execution confirmation (up to 55 pts)
                if row['close'] >= row['vwap']: m2_score += 15
                if row['adx'] > 20 and row['pos_di'] > row['neg_di']: m2_score += 15
                if (row['close'] - row['low']) > (0.30 * (row['high'] - row['low'] + 1e-6)): m2_score += 15
                if row['volume'] >= (0.90 * row['vol_sma']): m2_score += 10

                if m2_score < m2_thresh:
                    continue

                # PULLBACK ENTRY EXECUTION
                buy_zone_max = max(best_cluster) + (0.15 * atr)
                buy_zone_min = min(best_cluster) - (0.15 * atr)

                if (row['low'] <= buy_zone_max) and (row['close'] >= buy_zone_min):
                    in_pos = True
                    entry_px = row['close'] + SLIP + SPREAD
                    initial_sl = fib_618 - (0.5 * atr)
                    initial_risk = max(entry_px - initial_sl, 1.2 * atr)
                    virtual_sl = entry_px - initial_risk
                    target_tp1 = min(row['bb_upper'], s_high)
                    target_tp2 = s_high + (0.272 * fib_range)
                    tier = 0
                    max_fav_px = entry_px
                    max_adv_px = entry_px

    return pd.DataFrame(all_trades)

# 1. Fetch and Prepare Data Once (Deterministic)
print("📥 Fetching deterministic multi-asset bar historical datasets...")
fleet_symbols = ["NVDA", "AAPL", "MSFT", "TSLA", "AMD", "CVX"]
prepared_fleet = {}
spy_df = get_alpaca_bars("SPY", "15Min", days=60)

for sym in fleet_symbols:
    s_df = get_alpaca_bars(sym, "15Min", days=60)
    sec_df = get_alpaca_bars(SECTOR_MAP.get(sym, "XLK"), "15Min", days=60)
    if s_df is not None:
        prepared_fleet[sym] = prepare_data(s_df, sec_df, spy_df)

# 2. Grid Sweep
print("=" * 105)
print(f" 🔬 DETERMINISTIC EFFICIENT FRONTIER GRID SWEEP (TARGET: 30-45 TRADES | EXP >= +0.60R | PF >= 2.70)")
print("=" * 105)
print(f"{'M1 Thresh':<10} | {'M2 Thresh':<10} | {'Trades':<8} | {'Win Rate':<10} | {'Net Harvest':<14} | {'PF':<8} | {'Expectancy':<12} | {'Max DD':<10} | {'MAE/MFE':<12}")
print("-" * 105)

sweep_m1 = [50, 60, 65, 70]
sweep_m2 = [55, 60, 65, 70, 75]

grid_results = []

for m1 in sweep_m1:
    for m2 in sweep_m2:
        tdf = simulate_scored_grid(prepared_fleet, m1, m2)
        if len(tdf) == 0: continue
        
        r = tdf['pnl_r']
        wins = r[r > 0]
        losses = r[r <= 0]
        cum_r = r.cumsum()
        max_dd = (cum_r.cummax() - cum_r).max()
        
        wr = (len(wins) / len(r)) * 100
        pf = abs(wins.sum() / (losses.sum() + 1e-9))
        exp = r.mean()
        net_r = r.sum()
        mae = tdf['mae_r'].mean()
        mfe = tdf['mfe_r'].mean()
        
        grid_results.append({
            "m1": m1, "m2": m2, "n": len(tdf), "wr": wr, "net_r": net_r,
            "pf": pf, "exp": exp, "max_dd": max_dd, "mae": mae, "mfe": mfe
        })
        
        # Highlight Sweet Spot Candidates
        tag = " ⭐" if (30 <= len(tdf) <= 45 and exp >= 0.60 and pf >= 2.70) else ""
        print(f"{m1:<10} | {m2:<10} | {len(tdf):<8} | {wr:5.1f}%    | {net_r:+8.2f}R      | {pf:4.2f}   | {exp:+6.2f}R/tr    | {max_dd:5.2f}R   | {mae:.2f}/{mfe:.2f}{tag}")

print("=" * 105)
