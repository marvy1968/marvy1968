import os, math, datetime
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import requests

load_dotenv("/home/moonrider1968/.env")
APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID", os.getenv("ALPACA_API_KEY"))
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY", os.getenv("ALPACA_SECRET_KEY"))
DATA_URL = "https://data.alpaca.markets/v2"

SECTOR_MAP = {
    "NVDA": "XLK",
    "AAPL": "XLK",
    "MSFT": "XLK",
    "TSLA": "XLY",
    "AMD":  "XLK",
    "CVX":  "XLE"
}

def get_alpaca_bars(symbol, timeframe="15Min", days=60):
    headers = {
        "APCA-API-KEY-ID": APCA_API_KEY_ID,
        "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY
    }
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    start_dt = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "timeframe": timeframe,
        "start": start_dt,
        "limit": 3000,
        "adjustment": "raw",
        "feed": "iex"
    }
    r = requests.get(url, headers=headers, params=params)
    if r.status_code != 200:
        return None
    bars = r.json().get("bars", [])
    if not bars:
        return None
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
    adx = dx.rolling(n).mean()
    return adx, pos_di, neg_di

def simulate_system(df, sector_df=None, use_layered_filters=False):
    # Technical Indicators
    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_mid'] + (2.0 * df['bb_std'])
    df['bb_lower'] = df['bb_mid'] - (2.0 * df['bb_std'])
    
    tr = pd.concat([df['high'] - df['low'], (df['high'] - df['close'].shift(1)).abs(), (df['low'] - df['close'].shift(1)).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    df['vol_sma'] = df['volume'].rolling(20).mean()
    
    # Weinstein Stage proxy (150-period SMA on 15m as intermediate baseline)
    df['sma150'] = df['close'].rolling(150).mean()
    
    # ADX / Directional Movement
    df['adx'], df['pos_di'], df['neg_di'] = calculate_adx(df)
    
    # Intraday Cumulative VWAP
    df['cum_pv'] = (df['close'] * df['volume']).cumsum()
    df['cum_v'] = df['volume'].cumsum()
    df['vwap'] = df['cum_pv'] / (df['cum_v'] + 1e-9)

    df['swing_high'] = df['high'].rolling(40).max()
    df['swing_low'] = df['low'].rolling(40).min()

    # Sector Filter Pre-computation
    sector_pass = pd.Series(True, index=df.index)
    if use_layered_filters and sector_df is not None:
        sector_df['ema20'] = sector_df['close'].ewm(span=20, adjust=False).mean()
        sector_df['ema50'] = sector_df['close'].ewm(span=50, adjust=False).mean()
        # Align sector trend to 15m timestamps
        aligned_sector = sector_df.set_index('time').reindex(df['time'], method='ffill')
        sector_pass = (aligned_sector['close'] > aligned_sector['ema20']) & (aligned_sector['ema20'] > aligned_sector['ema50'])

    # Realistic Friction Parameters
    SLIP = 0.03
    SPREAD = 0.02
    
    trades = []
    in_pos = False
    side = 0 # 1 = Long, -1 = Short
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

        # Intrabar Adverse Noise
        noise = np.random.normal(0, 0.15 * atr)
        n_low = min(row['low'], row['low'] + noise)
        n_high = max(row['high'], row['high'] + noise)

        if in_pos:
            # Track MAE / MFE
            if side == 1:
                max_fav_px = max(max_fav_px, row['high'])
                max_adv_px = min(max_adv_px, row['low'])
                
                # Check Hard Stop Hit
                if n_low <= (virtual_sl + SPREAD):
                    exit_px = virtual_sl - SLIP
                    pnl_r = (exit_px - entry_px) / initial_risk
                    mae_r = (entry_px - max_adv_px) / initial_risk
                    mfe_r = (max_fav_px - entry_px) / initial_risk
                    trades.append({"side": "LONG", "pnl_r": pnl_r, "mae_r": max(0, mae_r), "mfe_r": max(0, mfe_r)})
                    in_pos = False
                    continue

                # Target 1: De-risk at Upper Band / Swing High
                if row['high'] >= target_tp1 and tier < 1:
                    virtual_sl = max(virtual_sl, entry_px + (0.05 * atr))
                    tier = 1

                # Target 2: Fib Extension -> Dynamic Trail
                if row['high'] >= target_tp2 and tier < 2:
                    virtual_sl = max(virtual_sl, row['ema21'] - (1.2 * atr))
                    tier = 2
                elif tier == 2:
                    virtual_sl = max(virtual_sl, row['ema21'] - (1.2 * atr))

            elif side == -1:
                max_fav_px = min(max_fav_px, row['low'])
                max_adv_px = max(max_adv_px, row['high'])

                if n_high >= (virtual_sl - SPREAD):
                    exit_px = virtual_sl + SLIP
                    pnl_r = (entry_px - exit_px) / initial_risk
                    mae_r = (max_adv_px - entry_px) / initial_risk
                    mfe_r = (entry_px - max_fav_px) / initial_risk
                    trades.append({"side": "SHORT", "pnl_r": pnl_r, "mae_r": max(0, mae_r), "mfe_r": max(0, mfe_r)})
                    in_pos = False
                    continue

                if row['low'] <= target_tp1 and tier < 1:
                    virtual_sl = min(virtual_sl, entry_px - (0.05 * atr))
                    tier = 1

                if row['low'] <= target_tp2 and tier < 2:
                    virtual_sl = min(virtual_sl, row['ema21'] + (1.2 * atr))
                    tier = 2
                elif tier == 2:
                    virtual_sl = min(virtual_sl, row['ema21'] + (1.2 * atr))

        else:
            # Confluence Logic
            s_high = prev['swing_high']
            s_low = prev['swing_low']
            fib_range = s_high - s_low
            if fib_range <= (1.0 * atr): continue

            # LONG Pipeline
            long_gate = (row['close'] > row['sma150']) if use_layered_filters else True
            long_sector = sector_pass.iloc[i] if use_layered_filters else True
            long_mtf = (row['adx'] > 22 and row['pos_di'] > row['neg_di'] and row['close'] > row['vwap'] and row['volume'] > 1.1 * row['vol_sma']) if use_layered_filters else True

            if row['ema9'] > row['ema21'] and row['close'] > row['bb_mid'] and long_gate and long_sector and long_mtf:
                fib_382 = s_high - (0.382 * fib_range)
                fib_500 = s_high - (0.500 * fib_range)
                fib_618 = s_high - (0.618 * fib_range)

                candidates = [fib_382, fib_500, row['ema21'], row['bb_mid']]
                cluster_window = 0.5 * atr
                best_cluster = max([ [lvl for lvl in candidates if abs(lvl - target) <= cluster_window] for target in candidates ], key=len)

                if (len(best_cluster) / len(candidates)) >= 0.5: # Confluence threshold
                    buy_zone_max = max(best_cluster) + (0.15 * atr)
                    buy_zone_min = min(best_cluster) - (0.15 * atr)
                    
                    if (row['low'] <= buy_zone_max) and (row['close'] >= buy_zone_min):
                        in_pos = True
                        side = 1
                        entry_px = row['close'] + SLIP + SPREAD
                        initial_sl = fib_618 - (0.5 * atr)
                        initial_risk = max(entry_px - initial_sl, 1.2 * atr)
                        virtual_sl = entry_px - initial_risk
                        target_tp1 = min(row['bb_upper'], s_high)
                        target_tp2 = s_high + (0.272 * fib_range)
                        tier = 0
                        max_fav_px = entry_px
                        max_adv_px = entry_px

    return pd.DataFrame(trades)

def compute_institutional_metrics(trades_df):
    if len(trades_df) == 0:
        return None
    
    r = trades_df['pnl_r']
    wins = r[r > 0]
    losses = r[r <= 0]
    
    win_rate = (len(wins) / len(r)) * 100.0
    profit_factor = abs(wins.sum() / (losses.sum() + 1e-9))
    net_r = r.sum()
    exp_per_trade = r.mean()
    
    avg_winner = wins.mean() if len(wins) > 0 else 0.0
    avg_loser = losses.mean() if len(losses) > 0 else 0.0
    payoff_ratio = abs(avg_winner / (avg_loser + 1e-9))
    
    # Max Drawdown in R
    cum_r = r.cumsum()
    peak = cum_r.cummax()
    drawdown = peak - cum_r
    max_dd_r = drawdown.max()
    
    # Longest losing streak
    is_loss = (r <= 0).astype(int)
    streak = 0
    max_streak = 0
    for val in is_loss:
        if val == 1:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
            
    # Long vs Short
    longs = trades_df[trades_df['side'] == 'LONG']
    shorts = trades_df[trades_df['side'] == 'SHORT']
    
    return {
        "trades": len(trades_df),
        "win_rate": win_rate,
        "net_r": net_r,
        "pf": profit_factor,
        "exp": exp_per_trade,
        "avg_win": avg_winner,
        "avg_loss": avg_loser,
        "payoff": payoff_ratio,
        "max_dd": max_dd_r,
        "max_streak": max_streak,
        "mae": trades_df['mae_r'].mean(),
        "mfe": trades_df['mfe_r'].mean(),
        "long_n": len(longs),
        "long_r": longs['pnl_r'].sum() if len(longs) > 0 else 0.0,
        "short_n": len(shorts),
        "short_r": shorts['pnl_r'].sum() if len(shorts) > 0 else 0.0
    }

print("=" * 90)
print(" 🔬 INSTITUTIONAL ATTRIBUTION: BASELINE VS. LAYERED SECTOR/STAGE/MTF ENGINE")
print("=" * 90)

fleet = ["NVDA", "AAPL", "MSFT", "TSLA", "AMD", "CVX"]
base_all = []
filtered_all = []

for sym in fleet:
    sec = SECTOR_MAP.get(sym, "XLK")
    stock_df = get_alpaca_bars(sym, "15Min", days=60)
    sec_df = get_alpaca_bars(sec, "15Min", days=60)
    
    if stock_df is None: continue
    
    b_res = simulate_system(stock_df.copy(), sec_df, use_layered_filters=False)
    f_res = simulate_system(stock_df.copy(), sec_df, use_layered_filters=True)
    
    if len(b_res) > 0: base_all.append(b_res)
    if len(f_res) > 0: filtered_all.append(f_res)

b_comb = pd.concat(base_all) if base_all else pd.DataFrame()
f_comb = pd.concat(filtered_all) if filtered_all else pd.DataFrame()

bm = compute_institutional_metrics(b_comb)
fm = compute_institutional_metrics(f_comb)

print(f"{'Metric':<28} | {'Baseline (Fib/EMA/BB)':<24} | {'Layered (+Stage/Sector/MTF)':<24}")
print("-" * 90)
print(f"{'Total Trades (N)':<28} | {bm['trades']:<24} | {fm['trades']:<24}")
print(f"{'Win Rate (%)':<28} | {bm['win_rate']:<23.1f}% | {fm['win_rate']:<23.1f}%")
print(f"{'Net Profit (R)':<28} | {bm['net_r']:<+23.2f}R | {fm['net_r']:<+23.2f}R")
print(f"{'Profit Factor (PF)':<28} | {bm['pf']:<24.2f} | {fm['pf']:<24.2f}")
print(f"{'Expectancy / Trade':<28} | {bm['exp']:<+23.2f}R | {fm['exp']:<+23.2f}R")
print(f"{'Payoff Ratio (Avg W/L)':<28} | {bm['payoff']:<24.2f} | {fm['payoff']:<24.2f}")
print(f"{'Average Winner':<28} | {bm['avg_win']:<+23.2f}R | {fm['avg_win']:<+23.2f}R")
print(f"{'Average Loser':<28} | {bm['avg_loss']:<+23.2f}R | {fm['avg_loss']:<+23.2f}R")
print(f"{'Maximum Drawdown (R)':<28} | {bm['max_dd']:<23.2f}R | {fm['max_dd']:<23.2f}R")
print(f"{'Max Losing Streak':<28} | {bm['max_streak']:<24} | {fm['max_streak']:<24}")
print(f"{'Mean Adverse Excursion (MAE)':<28} | {bm['mae']:<23.2f}R | {fm['mae']:<23.2f}R")
print(f"{'Mean Favorable Excursion (MFE)':<28} | {bm['mfe']:<23.2f}R | {fm['mfe']:<23.2f}R")
print(f"{'Longs (Trades / Net R)':<28} | {bm['long_n']} trades ({bm['long_r']:+.2f}R)       | {fm['long_n']} trades ({fm['long_r']:+.2f}R)")
print("=" * 90)
