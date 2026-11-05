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

def run_simulation(df, sector_df=None, mode="baseline"):
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
    if mode == "layered" and sector_df is not None:
        sector_df['ema50'] = sector_df['close'].ewm(span=50, adjust=False).mean()
        aligned = sector_df.set_index('time').reindex(df['time'], method='ffill')
        sector_pass = (aligned['close'] > aligned['ema50'])

    SLIP = 0.03
    SPREAD = 0.02
    
    trades = []
    in_pos = False
    side = "LONG"
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

        noise = np.random.normal(0, 0.15 * atr)
        n_low = min(row['low'], row['low'] + noise)
        n_high = max(row['high'], row['high'] + noise)

        if in_pos:
            if side == "LONG":
                max_fav_px = max(max_fav_px, row['high'])
                max_adv_px = min(max_adv_px, row['low'])
                
                if n_low <= (virtual_sl + SPREAD):
                    exit_px = virtual_sl - SLIP
                    pnl_r = (exit_px - entry_px) / initial_risk
                    mae_r = (entry_px - max_adv_px) / initial_risk
                    mfe_r = (max_fav_px - entry_px) / initial_risk
                    trades.append({"side": "LONG", "pnl_r": pnl_r, "mae_r": max(0, mae_r), "mfe_r": max(0, mfe_r)})
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

            elif side == "SHORT":
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
            s_high = prev['swing_high']
            s_low = prev['swing_low']
            fib_range = s_high - s_low
            if fib_range <= (1.0 * atr): continue

            # --- LONG SETUP ---
            # Module 1 & 2 Gates
            m1_gate = sector_pass.iloc[i] and (row['close'] > row['sma150']) if mode == "layered" else True
            m2_gate = (row['adx'] > 20 and row['pos_di'] > row['neg_di'] and row['close'] > row['vwap'] and row['volume'] > 1.05 * row['vol_sma']) if mode == "layered" else True

            if row['ema9'] > row['ema21'] and row['close'] > row['bb_mid'] and m1_gate and m2_gate:
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
                        in_pos = True
                        side = "LONG"
                        entry_px = row['close'] + SLIP + SPREAD
                        initial_sl = fib_618 - (0.5 * atr)
                        initial_risk = max(entry_px - initial_sl, 1.2 * atr)
                        virtual_sl = entry_px - initial_risk
                        target_tp1 = min(row['bb_upper'], s_high)
                        target_tp2 = s_high + (0.272 * fib_range)
                        tier = 0
                        max_fav_px = entry_px
                        max_adv_px = entry_px

            # --- SHORT SETUP (Layered Mode Only or Baseline) ---
            elif mode == "layered" and row['ema9'] < row['ema21'] and row['close'] < row['bb_mid'] and m1_gate and m2_gate:
                fib_382 = s_low + (0.382 * fib_range)
                fib_500 = s_low + (0.500 * fib_range)
                fib_618 = s_low + (0.618 * fib_range)

                candidates = [fib_382, fib_500, row['ema21'], row['bb_mid']]
                cluster_window = 0.5 * atr
                best_cluster = max([ [lvl for lvl in candidates if abs(lvl - target) <= cluster_window] for target in candidates ], key=len)

                if (len(best_cluster) / len(candidates)) >= 0.5:
                    sell_zone_min = min(best_cluster) - (0.15 * atr)
                    sell_zone_max = max(best_cluster) + (0.15 * atr)

                    if (row['high'] >= sell_zone_min) and (row['close'] <= sell_zone_max):
                        in_pos = True
                        side = "SHORT"
                        entry_px = row['close'] - SLIP - SPREAD
                        initial_sl = fib_618 + (0.5 * atr)
                        initial_risk = max(initial_sl - entry_px, 1.2 * atr)
                        virtual_sl = entry_px + initial_risk
                        target_tp1 = max(row['bb_lower'], s_low)
                        target_tp2 = s_low - (0.272 * fib_range)
                        tier = 0
                        max_fav_px = entry_px
                        max_adv_px = entry_px

    return pd.DataFrame(trades)

def get_full_attribution(trades_df):
    if len(trades_df) == 0: return None
    r = trades_df['pnl_r']
    wins, losses = r[r > 0], r[r <= 0]
    
    cum_r = r.cumsum()
    peak = cum_r.cummax()
    max_dd = (peak - cum_r).max()
    
    is_loss = (r <= 0).astype(int)
    streak, max_streak = 0, 0
    for val in is_loss:
        if val == 1:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
            
    longs = trades_df[trades_df['side'] == 'LONG']
    shorts = trades_df[trades_df['side'] == 'SHORT']
    
    return {
        "trades": len(trades_df),
        "win_rate": (len(wins) / len(r)) * 100.0,
        "net_r": r.sum(),
        "pf": abs(wins.sum() / (losses.sum() + 1e-9)),
        "exp": r.mean(),
        "avg_win": wins.mean() if len(wins) > 0 else 0.0,
        "avg_loss": losses.mean() if len(losses) > 0 else 0.0,
        "max_dd": max_dd,
        "max_streak": max_streak,
        "mae": trades_df['mae_r'].mean(),
        "mfe": trades_df['mfe_r'].mean(),
        "long_n": len(longs),
        "long_r": longs['pnl_r'].sum() if len(longs) > 0 else 0.0,
        "short_n": len(shorts),
        "short_r": shorts['pnl_r'].sum() if len(shorts) > 0 else 0.0
    }

fleet = ["NVDA", "AAPL", "MSFT", "TSLA", "AMD", "CVX"]
b_all, l_all = [], []

for sym in fleet:
    stock_df = get_alpaca_bars(sym, "15Min", days=60)
    sec_df = get_alpaca_bars(SECTOR_MAP.get(sym, "XLK"), "15Min", days=60)
    if stock_df is None: continue
    
    b_res = run_simulation(stock_df.copy(), sec_df, mode="baseline")
    l_res = run_simulation(stock_df.copy(), sec_df, mode="layered")
    
    if len(b_res) > 0: b_all.append(b_res)
    if len(l_res) > 0: l_all.append(l_res)

bm = get_full_attribution(pd.concat(b_all) if b_all else pd.DataFrame())
lm = get_full_attribution(pd.concat(l_all) if l_all else pd.DataFrame())

print("=" * 95)
print(f"{'INSTITUTIONAL METRIC':<28} | {'BASELINE (Fib/EMA/BB)':<30} | {'LAYERED (M1 + M2 Modules)':<30}")
print("=" * 95)
print(f"{'Total Trades (N)':<28} | {bm['trades']:<30} | {lm['trades']:<30}")
print(f"{'Win Rate (%)':<28} | {bm['win_rate']:<29.1f}% | {lm['win_rate']:<29.1f}%")
print(f"{'Net Profit (R)':<28} | {bm['net_r']:<+29.2f}R | {lm['net_r']:<+29.2f}R")
print(f"{'Profit Factor (PF)':<28} | {bm['pf']:<30.2f} | {lm['pf']:<30.2f}")
print(f"{'Expectancy / Trade':<28} | {bm['exp']:<+29.2f}R | {lm['exp']:<+29.2f}R")
print(f"{'Average Winner':<28} | {bm['avg_win']:<+29.2f}R | {lm['avg_win']:<+29.2f}R")
print(f"{'Average Loser':<28} | {bm['avg_loss']:<+29.2f}R | {lm['avg_loss']:<+29.2f}R")
print(f"{'Maximum Drawdown (R)':<28} | {bm['max_dd']:<29.2f}R | {lm['max_dd']:<29.2f}R")
print(f"{'Max Losing Streak':<28} | {bm['max_streak']:<30} | {lm['max_streak']:<30}")
print(f"{'Mean Adverse Excursion (MAE)':<28} | {bm['mae']:<29.2f}R | {lm['mae']:<29.2f}R")
print(f"{'Mean Favorable Excursion (MFE)':<28} | {bm['mfe']:<29.2f}R | {lm['mfe']:<29.2f}R")
print(f"{'Longs (Count / Net R)':<28} | {bm['long_n']} trades ({bm['long_r']:+.2f}R)            | {lm['long_n']} trades ({lm['long_r']:+.2f}R)")
print(f"{'Shorts (Count / Net R)':<28} | {bm['short_n']} trades ({bm['short_r']:+.2f}R)            | {lm['short_n']} trades ({lm['short_r']:+.2f}R)")
print("=" * 95)
