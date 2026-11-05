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

# 11-SECTOR UNIVERSE (Top representative liquid names)
SECTOR_UNIVERSE = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "ADBE", "CRM", "CSCO", "INTC", "QCOM"],
    "XLF": ["JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "PGR"],
    "XLE": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "HES"],
    "XLY": ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "TJX", "LOW", "BKNG", "TGT"],
    "XLI": ["GE", "CAT", "UNP", "HON", "BA", "RTX", "DE", "LMT", "ETN", "UPS"],
    "XLV": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "PFE", "ABT", "AMGN", "DHR"]
}
TICKER_SECTOR = {sym: sec for sec, tickers in SECTOR_UNIVERSE.items() for sym in tickers}

REGIMES = {
    "1_BULL_EXPANSION": ("2023-11-01", "2024-03-28", "Persistent Stage 2 Rally"),
    "2_SIDEWAYS_CHOP":  ("2023-05-01", "2023-07-31", "Compression / Range-Bound"),
    "3_CORRECTION":     ("2023-08-01", "2023-10-31", "Pullback to 200-day / 30W SMA"),
    "4_BEAR_STAGE4":    ("2022-01-03", "2022-06-30", "Stage 4 Breakdown & Tech Selloff"),
    "5_HIGH_VOLATILITY":("2022-09-01", "2022-11-15", "VIX > 25 Volatility Spike"),
    "6_LOW_VOL_DRIFT":  ("2024-05-01", "2024-07-15", "Low Volatility Summer Drift")
}

def get_alpaca_bars(symbol, timeframe="15Min", start_str="", end_str=""):
    headers = {"APCA-API-KEY-ID": APCA_API_KEY_ID, "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY}
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    params = {
        "timeframe": timeframe,
        "start": f"{start_str}T00:00:00Z",
        "end": f"{end_str}T23:59:59Z",
        "limit": 10000,
        "adjustment": "raw",
        "feed": "sip"
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code != 200: return None
        bars = r.json().get("bars", [])
        if not bars: return None
        df = pd.DataFrame(bars)
        df.rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
        df['time'] = pd.to_datetime(df['time']).dt.tz_convert(NY_TZ)
        return df
    except Exception:
        return None

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

def evaluate_regime_breadth(start_date, end_date):
    breadth = {}
    lead_start = (pd.to_datetime(start_date) - pd.Timedelta(days=260)).strftime("%Y-%m-%d")
    for sec, tickers in SECTOR_UNIVERSE.items():
        s2, s4 = 0, 0
        for sym in tickers[:6]:
            df_w = get_alpaca_bars(sym, "1Week", start_str=lead_start, end_str=end_date)
            if df_w is not None and len(df_w) >= 30:
                sma30 = df_w['close'].rolling(30).mean()
                if len(sma30) >= 3:
                    slope = sma30.iloc[-1] - sma30.iloc[-3]
                    latest = df_w['close'].iloc[-1]
                    if latest > sma30.iloc[-1] and slope > 0: s2 += 1
                    elif latest < sma30.iloc[-1] and slope < 0: s4 += 1
        breadth[sec] = {
            "bullish": s2 >= 4,
            "bearish": s4 >= 4,
            "s2": s2,
            "s4": s4
        }
    return breadth

def run_regime_simulation(start_date, end_date):
    SLIP, SPREAD = 0.03, 0.02
    trades = []
    breadth = evaluate_regime_breadth(start_date, end_date)
    
    fleet = ["NVDA", "AAPL", "MSFT", "AMD", "AVGO", "XOM", "CVX", "JPM", "GS", "AMZN", "TSLA", "LLY", "UNH", "GE", "CAT"]
    
    for sym in fleet:
        sec = TICKER_SECTOR.get(sym)
        if not sec or sec not in breadth: continue
        sec_info = breadth[sec]
        
        df = get_alpaca_bars(sym, "15Min", start_str=start_date, end_str=end_date)
        if df is None or len(df) < 80: continue

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
        entry_px, initial_risk, virtual_sl, target_tp1, target_tp2 = 0.0, 0.0, 0.0, 0.0, 0.0
        tier = 0
        max_fav_px, max_adv_px = 0.0, 0.0

        for i in range(50, len(df)):
            row, prev = df.iloc[i], df.iloc[i-1]
            atr = row['atr']
            if pd.isna(atr) or atr <= 0: continue
            
            bar_time = row['time'].time()
            in_session = (bar_time >= datetime.time(10, 0)) and (bar_time <= datetime.time(15, 50))

            if in_pos:
                # 15:50 EOD Liquidation
                if bar_time >= datetime.time(15, 50):
                    exit_px = (row['close'] - SLIP - SPREAD) if side == "LONG" else (row['close'] + SLIP + SPREAD)
                    raw_r = ((exit_px - entry_px) / initial_risk) if side == "LONG" else ((entry_px - exit_px) / initial_risk)
                    trades.append({"symbol": sym, "sector": sec, "side": side, "raw_r": raw_r, "mae_r": max(0, ((entry_px - max_adv_px)/initial_risk) if side=="LONG" else ((max_adv_px - entry_px)/initial_risk)), "mfe_r": max(0, ((max_fav_px - entry_px)/initial_risk) if side=="LONG" else ((entry_px - max_fav_px)/initial_risk)), "exit": "EOD"})
                    in_pos = False
                    continue

                if side == "LONG":
                    max_fav_px = max(max_fav_px, row['high'])
                    max_adv_px = min(max_adv_px, row['low'])
                    if row['low'] <= (virtual_sl + SPREAD):
                        exit_px = virtual_sl - SLIP
                        trades.append({"symbol": sym, "sector": sec, "side": "LONG", "raw_r": (exit_px - entry_px)/initial_risk, "mae_r": max(0, (entry_px - max_adv_px)/initial_risk), "mfe_r": max(0, (max_fav_px - entry_px)/initial_risk), "exit": "STOP"})
                        in_pos = False
                        continue
                    if row['high'] >= target_tp1 and tier < 1:
                        virtual_sl = max(virtual_sl, entry_px + (0.05 * atr)); tier = 1
                    if row['high'] >= target_tp2 and tier < 2:
                        virtual_sl = max(virtual_sl, row['ema21'] - (1.2 * atr)); tier = 2
                    elif tier == 2:
                        virtual_sl = max(virtual_sl, row['ema21'] - (1.2 * atr))

                elif side == "SHORT":
                    max_fav_px = min(max_fav_px, row['low'])
                    max_adv_px = max(max_adv_px, row['high'])
                    if row['high'] >= (virtual_sl - SPREAD):
                        exit_px = virtual_sl + SLIP
                        trades.append({"symbol": sym, "sector": sec, "side": "SHORT", "raw_r": (entry_px - exit_px)/initial_risk, "mae_r": max(0, (max_adv_px - entry_px)/initial_risk), "mfe_r": max(0, (entry_px - max_fav_px)/initial_risk), "exit": "STOP"})
                        in_pos = False
                        continue
                    if row['low'] <= target_tp1 and tier < 1:
                        virtual_sl = min(virtual_sl, entry_px - (0.05 * atr)); tier = 1
                    if row['low'] <= target_tp2 and tier < 2:
                        virtual_sl = min(virtual_sl, row['ema21'] + (1.2 * atr)); tier = 2
                    elif tier == 2:
                        virtual_sl = min(virtual_sl, row['ema21'] + (1.2 * atr))

            else:
                if not in_session: continue
                s_high, s_low = prev['swing_high'], prev['swing_low']
                fib_range = s_high - s_low
                if fib_range <= (1.0 * atr): continue

                # LONG SETUP
                if sec_info['bullish'] and row['ema9'] > row['ema21'] and row['close'] > row['bb_mid']:
                    fib_382, fib_500, fib_618 = s_high - (0.382 * fib_range), s_high - (0.500 * fib_range), s_high - (0.618 * fib_range)
                    candidates = [fib_382, fib_500, row['ema21'], row['bb_mid']]
                    cluster = max([[lvl for lvl in candidates if abs(lvl - target) <= 0.5 * atr] for target in candidates], key=len)
                    if (len(cluster) / len(candidates)) >= 0.5:
                        buy_min, buy_max = min(cluster) - (0.15 * atr), max(cluster) + (0.15 * atr)
                        if (row['low'] <= buy_max) and (row['close'] >= buy_min):
                            in_pos = True; side = "LONG"
                            entry_px = row['close'] + SLIP + SPREAD
                            initial_sl = fib_618 - (0.5 * atr)
                            initial_risk = max(entry_px - initial_sl, 1.2 * atr)
                            virtual_sl = entry_px - initial_risk
                            target_tp1 = min(row['bb_upper'], s_high)
                            target_tp2 = s_high + (0.272 * fib_range)
                            tier = 0
                            max_fav_px, max_adv_px = entry_px, entry_px

                # SHORT SETUP
                elif sec_info['bearish'] and row['ema9'] < row['ema21'] and row['close'] < row['bb_mid']:
                    fib_382, fib_500, fib_618 = s_low + (0.382 * fib_range), s_low + (0.500 * fib_range), s_low + (0.618 * fib_range)
                    candidates = [fib_382, fib_500, row['ema21'], row['bb_mid']]
                    cluster = max([[lvl for lvl in candidates if abs(lvl - target) <= 0.5 * atr] for target in candidates], key=len)
                    if (len(cluster) / len(candidates)) >= 0.5:
                        sell_min, sell_max = min(cluster) - (0.15 * atr), max(cluster) + (0.15 * atr)
                        if (row['high'] >= sell_min) and (row['close'] <= sell_max):
                            in_pos = True; side = "SHORT"
                            entry_px = row['close'] - SLIP - SPREAD
                            initial_sl = fib_618 + (0.5 * atr)
                            initial_risk = max(initial_sl - entry_px, 1.2 * atr)
                            virtual_sl = entry_px + initial_risk
                            target_tp1 = max(row['bb_lower'], s_low)
                            target_tp2 = s_low - (0.272 * fib_range)
                            tier = 0
                            max_fav_px, max_adv_px = entry_px, entry_px

    return pd.DataFrame(trades)

def run_monte_carlo(r_values, n_sims=10000):
    if len(r_values) == 0: return 0.0, 0.0, 0.0, 0
    max_dds = []
    max_streaks = []
    np.random.seed(42)
    for _ in range(n_sims):
        shuffled = np.random.choice(r_values, size=len(r_values), replace=True)
        cum = np.cumsum(shuffled)
        peak = np.maximum.accumulate(cum)
        max_dds.append(np.max(peak - cum))
        
        is_loss = (shuffled <= 0).astype(int)
        s, m_s = 0, 0
        for v in is_loss:
            if v == 1: s += 1; m_s = max(m_s, s)
            else: s = 0
        max_streaks.append(m_s)
        
    return np.percentile(max_dds, 50), np.percentile(max_dds, 95), np.percentile(max_dds, 99), int(np.percentile(max_streaks, 95))

print("=" * 115)
print(" 🏛️ V3.3 MULTI-REGIME INSTITUTIONAL STRESS TEST (FROZEN 1.0X FLAT RISK)")
print("=" * 115)

master_trades = []

for r_id, (s_dt, e_dt, desc) in REGIMES.items():
    print(f"\n▶ Executing: {r_id} ({s_dt} to {e_dt}) | {desc}")
    tdf = run_regime_simulation(s_dt, e_dt)
    if len(tdf) == 0:
        print(f"  • Result: 0 trades taken (Macro 6/10 Breadth held 100% Cash / Dormant).")
        continue
    
    tdf['regime'] = r_id
    master_trades.append(tdf)
    
    r = tdf['raw_r']
    wins, losses = r[r > 0], r[r <= 0]
    cum = r.cumsum()
    max_dd = (cum.cummax() - cum).max()
    wr = (len(wins) / len(r)) * 100
    pf = abs(wins.sum() / (losses.sum() + 1e-9))
    exp = r.mean()
    net_r = r.sum()
    avg_w = wins.mean() if len(wins) > 0 else 0.0
    avg_l = losses.mean() if len(losses) > 0 else 0.0
    
    longs = tdf[tdf['side'] == 'LONG']
    shorts = tdf[tdf['side'] == 'SHORT']
    l_net = longs['raw_r'].sum() if len(longs) > 0 else 0.0
    s_net = shorts['raw_r'].sum() if len(shorts) > 0 else 0.0
    
    mc_50, mc_95, mc_99, mc_s95 = run_monte_carlo(r.values)
    
    print(f"  • Trades: {len(tdf):<3} | WR: {wr:4.1f}% | Net R: {net_r:+6.2f}R | PF: {pf:4.2f} | Exp: {exp:+5.2f}R/tr | Payoff: {abs(avg_w/(avg_l+1e-9)):4.2f}")
    print(f"  • Winner/Loser: Avg Win: {avg_w:+5.2f}R | Avg Loss: {avg_l:+5.2f}R | MAE: {tdf['mae_r'].mean():.2f}R | MFE: {tdf['mfe_r'].mean():.2f}R")
    print(f"  • Attribution: Longs: {len(longs)} ({l_net:+5.2f}R) | Shorts: {len(shorts)} ({s_net:+5.2f}R)")
    print(f"  • Risk Tail (10k MC): Realized DD: {max_dd:.2f}R | 95th% DD: {mc_95:.2f}R | 99th% DD: {mc_99:.2f}R | 95th% Streak: {mc_s95} losses")

if master_trades:
    all_df = pd.concat(master_trades)
    ar = all_df['raw_r']
    awins, alosses = ar[ar > 0], ar[ar <= 0]
    acum = ar.cumsum()
    amax_dd = (acum.cummax() - acum).max()
    apf = abs(awins.sum() / (alosses.sum() + 1e-9))
    
    print("\n" + "=" * 115)
    print(" 🏆 AGGREGATE MULTI-REGIME PORTFOLIO SUMMARY")
    print("=" * 115)
    print(f"Total Trades Across All Regimes : {len(all_df)}")
    print(f"Aggregate Win Rate              : {(len(awins)/len(ar))*100:.1f}%")
    print(f"Aggregate Net Harvest           : {ar.sum():+.2f}R")
    print(f"Aggregate Profit Factor         : {apf:.2f}")
    print(f"Aggregate Expectancy per Trade  : {ar.mean():+.2f}R")
    print(f"Aggregate Max Drawdown          : {amax_dd:.2f}R")
    print(f"Harvest / MaxDD Efficiency Ratio: {ar.sum()/amax_dd:.2f}")
    print("=" * 115)
