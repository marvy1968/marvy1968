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

# 11 GICS SECTORS (Representative Sector Leaders)
SECTOR_UNIVERSE = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "ADBE", "CRM", "CSCO", "INTC", "QCOM"],
    "XLF": ["JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "PGR"],
    "XLE": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "HES"],
    "XLY": ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "TJX", "LOW", "BKNG", "TGT"],
    "XLI": ["GE", "CAT", "UNP", "HON", "BA", "RTX", "DE", "LMT", "ETN", "UPS"],
    "XLV": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "PFE", "ABT", "AMGN", "DHR"],
    "XLP": ["PG", "COST", "KO", "PEP", "WMT", "PM", "MDLZ", "CL", "MO", "EL"],
    "XLC": ["META", "GOOGL", "GOOG", "NFLX", "DIS", "TMUS", "CMCSA", "VZ", "T", "CHTR"],
    "XLB": ["LIN", "APD", "SHW", "ECL", "FCX", "NEM", "CTVA", "DOW", "DD", "ALB"],
    "XLU": ["NEE", "SO", "DUK", "CEG", "SRE", "AEP", "D", "PEG", "ED", "EXC"],
    "XLRE": ["PLD", "AMT", "EQIX", "PSA", "O", "CCI", "DLR", "SPG", "VICI", "CBRE"]
}

TICKER_SECTOR = {sym: sec for sec, tickers in SECTOR_UNIVERSE.items() for sym in tickers}

def get_alpaca_bars(symbol, timeframe="15Min", days=90):
    headers = {"APCA-API-KEY-ID": APCA_API_KEY_ID, "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY}
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    start_dt = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"timeframe": timeframe, "start": start_dt, "limit": 4000, "adjustment": "raw", "feed": "iex"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200: return None
        bars = r.json().get("bars", [])
        if not bars: return None
        df = pd.DataFrame(bars)
        df.rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
        df['time'] = pd.to_datetime(df['time']).dt.tz_convert(NY_TZ)
        return df
    except Exception:
        return None

def compute_weekly_stage(symbol):
    df_week = get_alpaca_bars(symbol, timeframe="1Week", days=365)
    if df_week is None or len(df_week) < 32:
        return "STAGE_UNKNOWN"
    df_week['sma30w'] = df_week['close'].rolling(30).mean()
    df_week['sma30w_slope'] = df_week['sma30w'].diff(3)
    latest = df_week.iloc[-1]
    prev_20w_high = df_week['high'].iloc[-21:-1].max()
    prev_20w_low = df_week['low'].iloc[-21:-1].min()

    if latest['close'] > latest['sma30w'] and latest['sma30w_slope'] > 0 and latest['close'] >= (0.95 * prev_20w_high):
        return "STAGE_2_ADVANCING"
    elif latest['close'] < latest['sma30w'] and latest['sma30w_slope'] < 0 and latest['close'] <= (1.05 * prev_20w_low):
        return "STAGE_4_DECLINING"
    return "STAGE_CONSOLIDATION"

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
    rth_vwap = cum_pv / (cum_v + 1e-9)
    return rth_vwap.reindex(df.index).ffill()

print("🔍 Pre-computing True 30-Week Weinstein Stage across full 110-stock universe...")
breadth_matrix = {}
sector_stages = {}

for sec, tickers in SECTOR_UNIVERSE.items():
    s2, s4 = 0, 0
    sector_stages[sec] = {}
    for sym in tickers:
        st = compute_weekly_stage(sym)
        sector_stages[sec][sym] = st
        if st == "STAGE_2_ADVANCING": s2 += 1
        elif st == "STAGE_4_DECLINING": s4 += 1
        time.sleep(0.02)
    breadth_matrix[sec] = {
        "bullish": s2 >= 6,
        "bearish": s4 >= 6,
        "s2_score": s2,
        "s4_score": s4
    }

print(f"✅ Breadth Matrix Computed: {sum(1 for b in breadth_matrix.values() if b['bullish'])} Sectors Bullish, {sum(1 for b in breadth_matrix.values() if b['bearish'])} Sectors Bearish.")

# 2. RUN SIMULATION ACROSS UNIVERSE
SLIP = 0.03
SPREAD = 0.02
all_trades = []

# Scan top liquid names across sectors for 60-day 15m confluence
scan_fleet = [
    "NVDA", "AAPL", "MSFT", "AMD", "AVGO", "QCOM", 
    "JPM", "GS", "BAC", "XOM", "CVX", "AMZN", "TSLA", 
    "GE", "CAT", "LLY", "UNH", "COST", "META", "GOOGL"
]

for sym in scan_fleet:
    sec = TICKER_SECTOR.get(sym)
    if not sec or sec not in breadth_matrix: continue
    sec_info = breadth_matrix[sec]
    sym_stage = sector_stages[sec].get(sym, "STAGE_UNKNOWN")

    df = get_alpaca_bars(sym, timeframe="15Min", days=60)
    if df is None or len(df) < 120: continue

    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_mid'] + (2.0 * df['bb_std'])
    df['bb_lower'] = df['bb_mid'] - (2.0 * df['bb_std'])
    
    tr = pd.concat([df['high'] - df['low'], (df['high'] - df['close'].shift(1)).abs(), (df['low'] - df['close'].shift(1)).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    df['vol_sma'] = df['volume'].rolling(20).mean()
    df['vwap'] = calculate_rth_vwap(df)
    df['adx'], df['pos_di'], df['neg_di'] = compute_adx(df)
    df['swing_high'] = df['high'].rolling(40).max()
    df['swing_low'] = df['low'].rolling(40).min()

    in_pos = False
    side = "LONG"
    entry_px = 0.0
    initial_risk = 0.0
    virtual_sl = 0.0
    target_tp1 = 0.0
    target_tp2 = 0.0
    tier = 0
    mult = 1.0
    score = 0
    max_fav_px = 0.0
    max_adv_px = 0.0

    for i in range(50, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        atr = row['atr']
        if pd.isna(atr) or atr <= 0: continue

        # Intraday 10:00 - 15:50 session window
        bar_time = row['time'].time()
        in_session = (bar_time >= datetime.time(10, 0)) and (bar_time <= datetime.time(15, 50))

        if in_pos:
            # 15:50 EOD Liquidation
            if bar_time >= datetime.time(15, 50):
                exit_px = (row['close'] - SLIP - SPREAD) if side == "LONG" else (row['close'] + SLIP + SPREAD)
                raw_r = ((exit_px - entry_px) / initial_risk) if side == "LONG" else ((entry_px - exit_px) / initial_risk)
                mae_r = ((entry_px - max_adv_px) / initial_risk) if side == "LONG" else ((max_adv_px - entry_px) / initial_risk)
                mfe_r = ((max_fav_px - entry_px) / initial_risk) if side == "LONG" else ((entry_px - max_fav_px) / initial_risk)
                all_trades.append({"symbol": sym, "side": side, "raw_r": raw_r, "tiered_r": raw_r * mult, "mae_r": max(0, mae_r), "mfe_r": max(0, mfe_r), "exit": "EOD_FLATTEN"})
                in_pos = False
                continue

            if side == "LONG":
                max_fav_px = max(max_fav_px, row['high'])
                max_adv_px = min(max_adv_px, row['low'])

                if row['low'] <= (virtual_sl + SPREAD):
                    exit_px = virtual_sl - SLIP
                    raw_r = (exit_px - entry_px) / initial_risk
                    mae_r = (entry_px - max_adv_px) / initial_risk
                    mfe_r = (max_fav_px - entry_px) / initial_risk
                    all_trades.append({"symbol": sym, "side": "LONG", "raw_r": raw_r, "tiered_r": raw_r * mult, "mae_r": max(0, mae_r), "mfe_r": max(0, mfe_r), "exit": "STOP"})
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

                if row['high'] >= (virtual_sl - SPREAD):
                    exit_px = virtual_sl + SLIP
                    raw_r = (entry_px - exit_px) / initial_risk
                    mae_r = (max_adv_px - entry_px) / initial_risk
                    mfe_r = (entry_px - max_fav_px) / initial_risk
                    all_trades.append({"symbol": sym, "side": "SHORT", "raw_r": raw_r, "tiered_r": raw_r * mult, "mae_r": max(0, mae_r), "mfe_r": max(0, mfe_r), "exit": "STOP"})
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
            if not in_session: continue
            s_high, s_low = prev['swing_high'], prev['swing_low']
            fib_range = s_high - s_low
            if fib_range <= (1.0 * atr): continue

            # LONG CONFLUENCE ENTRY
            if sym_stage == "STAGE_2_ADVANCING" and sec_info['bullish'] and row['ema9'] > row['ema21'] and row['close'] > row['bb_mid']:
                fib_382, fib_500, fib_618 = s_high - (0.382 * fib_range), s_high - (0.500 * fib_range), s_high - (0.618 * fib_range)
                candidates = [fib_382, fib_500, row['ema21'], row['bb_mid']]
                cluster = max([[lvl for lvl in candidates if abs(lvl - target) <= 0.5 * atr] for target in candidates], key=len)

                if (len(cluster) / len(candidates)) >= 0.5:
                    buy_min, buy_max = min(cluster) - (0.15 * atr), max(cluster) + (0.15 * atr)
                    if (row['low'] <= buy_max) and (row['close'] >= buy_min):
                        score = 50
                        if sec_info['s2_score'] >= 8: score += 15
                        if row['close'] >= row['vwap']: score += 15
                        if row['adx'] > 20 and row['pos_di'] > row['neg_di']: score += 10
                        if row['volume'] >= (0.90 * row['vol_sma']): score += 10

                        mult = 1.50 if score >= 90 else (1.35 if score >= 80 else (1.15 if score >= 70 else 1.00))
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

            # SHORT CONFLUENCE ENTRY
            elif sym_stage == "STAGE_4_DECLINING" and sec_info['bearish'] and row['ema9'] < row['ema21'] and row['close'] < row['bb_mid']:
                fib_382, fib_500, fib_618 = s_low + (0.382 * fib_range), s_low + (0.500 * fib_range), s_low + (0.618 * fib_range)
                candidates = [fib_382, fib_500, row['ema21'], row['bb_mid']]
                cluster = max([[lvl for lvl in candidates if abs(lvl - target) <= 0.5 * atr] for target in candidates], key=len)

                if (len(cluster) / len(candidates)) >= 0.5:
                    sell_min, sell_max = min(cluster) - (0.15 * atr), max(cluster) + (0.15 * atr)
                    if (row['high'] >= sell_min) and (row['close'] <= sell_max):
                        score = 50
                        if sec_info['s4_score'] >= 8: score += 15
                        if row['close'] <= row['vwap']: score += 15
                        if row['adx'] > 20 and row['neg_di'] > row['pos_di']: score += 10
                        if row['volume'] >= (0.90 * row['vol_sma']): score += 10

                        mult = 1.50 if score >= 90 else (1.35 if score >= 80 else (1.15 if score >= 70 else 1.00))
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

tdf = pd.DataFrame(all_trades)

def get_full_stats(trades_df, col='tiered_r'):
    if len(trades_df) == 0: return None
    r = trades_df[col]
    wins, losses = r[r > 0], r[r <= 0]
    cum = r.cumsum()
    peak = cum.cummax()
    max_dd = (peak - cum).max()
    
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
        "n": len(trades_df),
        "wr": (len(wins) / len(r)) * 100.0,
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
        "long_r": longs[col].sum() if len(longs) > 0 else 0.0,
        "short_n": len(shorts),
        "short_r": shorts[col].sum() if len(shorts) > 0 else 0.0
    }

s_flat = get_full_stats(tdf, 'raw_r')
s_tier = get_full_stats(tdf, 'tiered_r')

print("=" * 100)
print(f" 🏛️ V3.3 INSTITUTIONAL PERFORMANCE ATTRIBUTION MATRIX (N = {s_tier['n']} Trades)")
print("=" * 100)
print(f"{'METRIC':<30} | {'FLAT SIZING (1.0x)':<30} | {'TIERED CONVICTION (1.0x-1.5x)':<30}")
print("-" * 100)
print(f"{'Total Trades (N)':<30} | {s_flat['n']:<30} | {s_tier['n']:<30}")
print(f"{'Win Rate (%)':<30} | {s_flat['wr']:<29.1f}% | {s_tier['wr']:<29.1f}%")
print(f"{'Total Harvest (R)':<30} | {s_flat['net_r']:<+29.2f}R | {s_tier['net_r']:<+29.2f}R")
print(f"{'Profit Factor (PF)':<30} | {s_flat['pf']:<30.2f} | {s_tier['pf']:<30.2f}")
print(f"{'Expectancy / Trade':<30} | {s_flat['exp']:<+29.2f}R | {s_tier['exp']:<+29.2f}R")
print(f"{'Average Winner':<30} | {s_flat['avg_win']:<+29.2f}R | {s_tier['avg_win']:<+29.2f}R")
print(f"{'Average Loser':<30} | {s_flat['avg_loss']:<+29.2f}R | {s_tier['avg_loss']:<+29.2f}R")
print(f"{'Max Drawdown (R)':<30} | {s_flat['max_dd']:<29.2f}R | {s_tier['max_dd']:<29.2f}R")
print(f"{'Max Losing Streak':<30} | {s_flat['max_streak']:<30} | {s_tier['max_streak']:<30}")
print(f"{'Mean Adverse Excursion (MAE)':<30} | {s_flat['mae']:<29.2f}R | {s_tier['mae']:<29.2f}R")
print(f"{'Mean Favorable Excursion (MFE)':<30} | {s_flat['mfe']:<29.2f}R | {s_tier['mfe']:<29.2f}R")
print(f"{'Longs (Count / Net R)':<30} | {s_flat['long_n']} trades ({s_flat['long_r']:+.2f}R)            | {s_tier['long_n']} trades ({s_tier['long_r']:+.2f}R)")
print(f"{'Shorts (Count / Net R)':<30} | {s_flat['short_n']} trades ({s_flat['short_r']:+.2f}R)            | {s_tier['short_n']} trades ({s_tier['short_r']:+.2f}R)")
print(f"{'Harvest / MaxDD Ratio':<30} | {s_flat['net_r']/s_flat['max_dd']:<30.2f} | {s_tier['net_r']/s_tier['max_dd']:<30.2f}")
print("=" * 100)
