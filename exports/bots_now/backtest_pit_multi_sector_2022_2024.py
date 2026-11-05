import os, time, datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import requests

load_dotenv("/home/moonrider1968/.env")
APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID", os.getenv("ALPACA_API_KEY"))
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY", os.getenv("ALPACA_SECRET_KEY"))
DATA_URL = "https://data.alpaca.markets/v2"
NY_TZ = ZoneInfo("America/New_York")

# 11 GICS SECTORS (Representative Leaders)
SECTOR_UNIVERSE = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "QCOM"],
    "XLF": ["JPM", "BAC", "WFC", "GS", "MS", "BLK"],
    "XLE": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC"],
    "XLY": ["AMZN", "TSLA", "HD", "NKE", "TJX", "BKNG"],
    "XLI": ["GE", "CAT", "UNP", "HON", "BA", "DE"],
    "XLV": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "AMGN"]
}
TICKER_SECTOR = {sym: sec for sec, tickers in SECTOR_UNIVERSE.items() for sym in tickers}

def get_alpaca_bars(symbol, timeframe="15Min", start_str="2022-01-01", end_str="2024-06-30"):
    headers = {"APCA-API-KEY-ID": APCA_API_KEY_ID, "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY}
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    all_bars = []
    page_token = None
    
    while True:
        params = {
            "timeframe": timeframe,
            "start": f"{start_str}T00:00:00Z",
            "end": f"{end_str}T23:59:59Z",
            "limit": 10000,
            "adjustment": "raw",
            "feed": "sip"
        }
        if page_token:
            params["page_token"] = page_token
            
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            if r.status_code != 200: break
            data = r.json()
            bars = data.get("bars", [])
            if not bars: break
            all_bars.extend(bars)
            page_token = data.get("next_page_token")
            if not page_token: break
        except Exception:
            break
            
    if not all_bars: return None
    df = pd.DataFrame(all_bars)
    df.rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
    df['time'] = pd.to_datetime(df['time']).dt.tz_convert(NY_TZ)
    df.sort_values('time', inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def compute_adx(df, n=14):
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n).mean()
    up_move = h - h.shift(1)
    down_move = l.shift(1) - l
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    pos_di = 100 * (pd.Series(pos_dm, index=df.index).rolling(n).mean() / (atr + 1e-9))
    neg_di = 100 * (pd.Series(neg_dm, index=df.index).rolling(n).mean() / (atr + 1e-9))
    dx = 100 * (np.abs(pos_di - neg_di) / (pos_di + neg_di + 1e-9))
    return dx.rolling(n).mean(), pos_di, neg_di

def calculate_rth_vwap(df):
    rth_df = df[(df['time'].dt.time >= datetime.time(9, 30)) & (df['time'].dt.time <= datetime.time(16, 0))].copy()
    if rth_df.empty: return pd.Series(index=df.index, dtype=float)
    rth_df['date'] = rth_df['time'].dt.date
    rth_df['pv'] = rth_df['close'] * rth_df['volume']
    cum_pv = rth_df.groupby('date')['pv'].cumsum()
    cum_v = rth_df.groupby('date')['volume'].cumsum()
    return (cum_pv / (cum_v + 1e-9)).reindex(df.index).ffill()

print("📥 Fetching historical weekly and 15m bar data (2022 - 2024)...")
weekly_data = {}
intraday_data = {}

all_symbols = [s for tickers in SECTOR_UNIVERSE.values() for s in tickers]

for sym in all_symbols:
    # 1. Weekly Bars for Point-in-Time Weinstein Stage (from 2021 for warm-up)
    df_w = get_alpaca_bars(sym, timeframe="1Week", start_str="2021-01-01", end_str="2024-06-30")
    if df_w is not None and len(df_w) >= 35:
        df_w['sma30w'] = df_w['close'].rolling(30).mean()
        df_w['sma30w_slope'] = df_w['sma30w'].diff(3)
        df_w['prev_20w_high'] = df_w['high'].rolling(20).max().shift(1)
        df_w['prev_20w_low'] = df_w['low'].rolling(20).min().shift(1)
        weekly_data[sym] = df_w
        
    # 2. 15m Bars for Trade Simulation
    df_15 = get_alpaca_bars(sym, timeframe="15Min", start_str="2022-01-01", end_str="2024-06-30")
    if df_15 is not None and len(df_15) >= 200:
        df_15['ema9'] = df_15['close'].ewm(span=9, adjust=False).mean()
        df_15['ema21'] = df_15['close'].ewm(span=21, adjust=False).mean()
        df_15['bb_mid'] = df_15['close'].rolling(20).mean()
        df_15['bb_std'] = df_15['close'].rolling(20).std()
        df_15['bb_upper'] = df_15['bb_mid'] + (2.0 * df_15['bb_std'])
        df_15['bb_lower'] = df_15['bb_mid'] - (2.0 * df_15['bb_std'])
        tr = pd.concat([df_15['high'] - df_15['low'], (df_15['high'] - df_15['close'].shift(1)).abs(), (df_15['low'] - df_15['close'].shift(1)).abs()], axis=1).max(axis=1)
        df_15['atr'] = tr.rolling(14).mean()
        df_15['vol_sma'] = df_15['volume'].rolling(20).mean()
        df_15['vwap'] = calculate_rth_vwap(df_15)
        df_15['adx'], df_15['pos_di'], df_15['neg_di'] = compute_adx(df_15)
        df_15['swing_high'] = df_15['high'].rolling(40).max()
        df_15['swing_low'] = df_15['low'].rolling(40).min()
        intraday_data[sym] = df_15
    time.sleep(0.05)

print(f"✅ Data Ready: Loaded {len(weekly_data)} Weekly series and {len(intraday_data)} 15m series.")

# SIMULATION ENGINE
SLIP, SPREAD = 0.03, 0.02
all_trades = []

def get_pit_stage(sym, current_time):
    df_w = weekly_data.get(sym)
    if df_w is None: return "STAGE_UNKNOWN"
    past_w = df_w[df_w['time'] < current_time]
    if len(past_w) < 32: return "STAGE_UNKNOWN"
    row = past_w.iloc[-1]
    if row['close'] > row['sma30w'] and row['sma30w_slope'] > 0 and row['close'] >= (0.95 * row['prev_20w_high']):
        return "STAGE_2_ADVANCING"
    elif row['close'] < row['sma30w'] and row['sma30w_slope'] < 0 and row['close'] <= (1.05 * row['prev_20w_low']):
        return "STAGE_4_DECLINING"
    return "STAGE_CONSOLIDATION"

def get_pit_sector_breadth(sec, current_time):
    tickers = SECTOR_UNIVERSE.get(sec, [])
    s2, s4 = 0, 0
    for s in tickers:
        st = get_pit_stage(s, current_time)
        if st == "STAGE_2_ADVANCING": s2 += 1
        elif st == "STAGE_4_DECLINING": s4 += 1
    return {
        "bullish": s2 >= 4,
        "bearish": s4 >= 4,
        "s2_score": s2,
        "s4_score": s4
    }

print("⚙️ Running Dynamic Point-in-Time Multi-Sector Backtest...")

for sym, df in intraday_data.items():
    sec = TICKER_SECTOR.get(sym)
    in_pos = False
    side = "LONG"
    entry_px, initial_risk, virtual_sl, target_tp1, target_tp2 = 0.0, 0.0, 0.0, 0.0, 0.0
    tier = 0
    max_fav_px, max_adv_px = 0.0, 0.0
    entry_time = None
    last_breadth_check_date = None
    cached_breadth = None

    for i in range(50, len(df)):
        row, prev = df.iloc[i], df.iloc[i-1]
        atr = row['atr']
        if pd.isna(atr) or atr <= 0: continue
        
        curr_time = row['time']
        bar_time = curr_time.time()
        in_session = (bar_time >= datetime.time(10, 0)) and (bar_time <= datetime.time(15, 50))

        # Position Management
        if in_pos:
            if side == "LONG":
                max_fav_px = max(max_fav_px, row['high'])
                max_adv_px = min(max_adv_px, row['low'])

                # Stop Hit
                if row['low'] <= (virtual_sl + SPREAD):
                    exit_px = virtual_sl - SLIP
                    all_trades.append({
                        "symbol": sym, "sector": sec, "side": "LONG",
                        "entry_time": entry_time, "exit_time": curr_time,
                        "raw_r": (exit_px - entry_px) / initial_risk,
                        "mae_r": max(0, (entry_px - max_adv_px) / initial_risk),
                        "mfe_r": max(0, (max_fav_px - entry_px) / initial_risk)
                    })
                    in_pos = False
                    continue

                # Two-Tier Ratchet Progression
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

                # Stop Hit
                if row['high'] >= (virtual_sl - SPREAD):
                    exit_px = virtual_sl + SLIP
                    all_trades.append({
                        "symbol": sym, "sector": sec, "side": "SHORT",
                        "entry_time": entry_time, "exit_time": curr_time,
                        "raw_r": (entry_px - exit_px) / initial_risk,
                        "mae_r": max(0, (max_adv_px - entry_px) / initial_risk),
                        "mfe_r": max(0, (entry_px - max_fav_px) / initial_risk)
                    })
                    in_pos = False
                    continue

                # Two-Tier Ratchet Progression
                if row['low'] <= target_tp1 and tier < 1:
                    virtual_sl = min(virtual_sl, entry_px - (0.05 * atr))
                    tier = 1
                if row['low'] <= target_tp2 and tier < 2:
                    virtual_sl = min(virtual_sl, row['ema21'] + (1.2 * atr))
                    tier = 2
                elif tier == 2:
                    virtual_sl = min(virtual_sl, row['ema21'] + (1.2 * atr))

        else:
            if not in_session: continue
            
            # Point-in-Time Breadth & Stage update once per day/week
            current_date = curr_time.date()
            if current_date != last_breadth_check_date:
                cached_breadth = get_pit_sector_breadth(sec, curr_time)
                last_breadth_check_date = current_date

            sym_stage = get_pit_stage(sym, curr_time)
            s_high, s_low = prev['swing_high'], prev['swing_low']
            fib_range = s_high - s_low
            if fib_range <= (1.0 * atr): continue

            # LONG CONFLUENCE ENTRY
            if sym_stage == "STAGE_2_ADVANCING" and cached_breadth['bullish'] and row['ema9'] > row['ema21'] and row['close'] > row['bb_mid']:
                fib_382 = s_high - (0.382 * fib_range)
                fib_500 = s_high - (0.500 * fib_range)
                fib_618 = s_high - (0.618 * fib_range)
                candidates = [fib_382, fib_500, row['ema21'], row['bb_mid']]
                cluster = max([[lvl for lvl in candidates if abs(lvl - target) <= 0.5 * atr] for target in candidates], key=len)

                if (len(cluster) / len(candidates)) >= 0.5:
                    buy_min, buy_max = min(cluster) - (0.15 * atr), max(cluster) + (0.15 * atr)
                    if (row['low'] <= buy_max) and (row['close'] >= buy_min):
                        in_pos = True
                        side = "LONG"
                        entry_px = row['close'] + SLIP + SPREAD
                        initial_sl = fib_618 - (0.5 * atr)
                        initial_risk = max(entry_px - initial_sl, 1.2 * atr)
                        virtual_sl = entry_px - initial_risk
                        target_tp1 = min(row['bb_upper'], s_high)
                        target_tp2 = s_high + (0.272 * fib_range)
                        tier = 0
                        max_fav_px, max_adv_px = entry_px, entry_px
                        entry_time = curr_time

            # SHORT CONFLUENCE ENTRY
            elif sym_stage == "STAGE_4_DECLINING" and cached_breadth['bearish'] and row['ema9'] < row['ema21'] and row['close'] < row['bb_mid']:
                fib_382 = s_low + (0.382 * fib_range)
                fib_500 = s_low + (0.500 * fib_range)
                fib_618 = s_low + (0.618 * fib_range)
                candidates = [fib_382, fib_500, row['ema21'], row['bb_mid']]
                cluster = max([[lvl for lvl in candidates if abs(lvl - target) <= 0.5 * atr] for target in candidates], key=len)

                if (len(cluster) / len(candidates)) >= 0.5:
                    sell_min, sell_max = min(cluster) - (0.15 * atr), max(cluster) + (0.15 * atr)
                    if (row['high'] >= sell_min) and (row['close'] <= sell_max):
                        in_pos = True
                        side = "SHORT"
                        entry_px = row['close'] - SLIP - SPREAD
                        initial_sl = fib_618 + (0.5 * atr)
                        initial_risk = max(initial_sl - entry_px, 1.2 * atr)
                        virtual_sl = entry_px + initial_risk
                        target_tp1 = max(row['bb_lower'], s_low)
                        target_tp2 = s_low - (0.272 * fib_range)
                        tier = 0
                        max_fav_px, max_adv_px = entry_px, entry_px
                        entry_time = curr_time

tdf = pd.DataFrame(all_trades)

if len(tdf) == 0:
    print("❌ No trades triggered across the 2022-2024 testing period.")
    exit()

r = tdf['raw_r']
wins, losses = r[r > 0], r[r <= 0]
cum = r.cumsum()
peak = cum.cummax()
max_dd = (peak - cum).max()
wr = (len(wins) / len(r)) * 100.0
pf = abs(wins.sum() / (losses.sum() + 1e-9))
exp = r.mean()
avg_w = wins.mean() if len(wins) > 0 else 0.0
avg_l = losses.mean() if len(losses) > 0 else 0.0

print("\n" + "=" * 115)
print(f" 🏛️ V3.3 DYNAMIC POINT-IN-TIME MULTI-SECTOR PERFORMANCE MATRIX (2022 - 2024)")
print("=" * 115)
print(f"{'Metric':<36} | {'Value':<30} | {'Institutional Benchmark'}")
print("-" * 115)
print(f"{'Total Trades Evaluated (N)':<36} | {len(tdf):<30} | Multi-Year Universe")
print(f"{'Win Rate (%)':<36} | {wr:<29.1f}% | 40.0% - 48.0%")
print(f"{'Total Cumulative Harvest (R)':<36} | {r.sum():<+29.2f}R | Calmar Growth")
print(f"{'Profit Factor (PF)':<36} | {pf:<30.2f} | >= 2.50")
print(f"{'Expectancy per Trade':<36} | {exp:<+29.2f}R | >= +0.60R")
print(f"{'Average Winner':<36} | {avg_w:<+29.2f}R | Multi-Tier Harvest")
print(f"{'Average Loser':<36} | {avg_l:<+29.2f}R | Controlled Loss")
print(f"{'Payoff Ratio (Avg Win / Avg Loss)':<36} | {abs(avg_w/(avg_l+1e-9)):<30.2f} | >= 3.50")
print(f"{'Max Drawdown (R)':<36} | {max_dd:<29.2f}R | <= 7.50R")
print(f"{'Harvest / MaxDD Ratio':<36} | {r.sum()/max_dd:<30.2f} | >= 7.00")
print(f"{'Mean Adverse Excursion (MAE)':<36} | {tdf['mae_r'].mean():<29.2f}R | <= 0.65R")
print(f"{'Mean Favorable Excursion (MFE)':<36} | {tdf['mfe_r'].mean():<29.2f}R | Runner Expansion")
print("=" * 115)

# Sector Attribution Matrix
print("\n📊 SECTOR CONTRIBUTION & CONCENTRATION AUDIT:")
sec_grp = tdf.groupby('sector').agg(
    Trades=('raw_r', 'count'),
    WinRate=('raw_r', lambda x: f"{(x > 0).mean()*100:.1f}%"),
    Net_R=('raw_r', 'sum'),
    Avg_Win=('raw_r', lambda x: x[x > 0].mean() if (x > 0).any() else 0.0),
    Avg_Loss=('raw_r', lambda x: x[x <= 0].mean() if (x <= 0).any() else 0.0)
).reset_index()
print(sec_grp.to_string(index=False))

# Yearly Breakdown
tdf['year'] = pd.to_datetime(tdf['entry_time']).dt.year
print("\n📅 YEAR-OVER-YEAR STABILITY BREAKDOWN:")
yr_grp = tdf.groupby('year').agg(
    Trades=('raw_r', 'count'),
    WinRate=('raw_r', lambda x: f"{(x > 0).mean()*100:.1f}%"),
    Net_R=('raw_r', 'sum'),
    PF=('raw_r', lambda x: abs(x[x > 0].sum() / (x[x <= 0].sum() + 1e-9)))
).reset_index()
print(yr_grp.to_string(index=False))
print("=" * 115)
