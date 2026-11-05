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

SECTOR_UNIVERSE = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "QCOM"],
    "XLF": ["JPM", "BAC", "WFC", "GS", "MS", "BLK"],
    "XLE": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC"],
    "XLY": ["AMZN", "TSLA", "HD", "NKE", "TJX", "BKNG"],
    "XLI": ["GE", "CAT", "UNP", "HON", "BA", "DE"],
    "XLV": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "AMGN"]
}
TICKER_SECTOR = {sym: sec for sec, tickers in SECTOR_UNIVERSE.items() for sym in tickers}

STRESS_WINDOWS = {
    "2018_Q4_QT_SELLOFF": ("2018-10-01", "2018-12-31", "2017-10-01"),
    "2020_Q1_COVID_CRASH": ("2020-02-15", "2020-04-30", "2019-02-15")
}

def get_alpaca_bars(symbol, timeframe="15Min", start_str="", end_str=""):
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

def run_stress_window(name, start_date, end_date, warmup_date):
    SLIP, SPREAD = 0.03, 0.02
    trades = []
    
    print(f"📥 Fetching Weekly & 15m Bars for {name} ({start_date} to {end_date})...")
    weekly_data = {}
    intraday_data = {}
    
    all_symbols = [s for tickers in SECTOR_UNIVERSE.values() for s in tickers]
    
    for sym in all_symbols:
        df_w = get_alpaca_bars(sym, "1Week", start_str=warmup_date, end_str=end_date)
        if df_w is not None and len(df_w) >= 35:
            df_w['sma30w'] = df_w['close'].rolling(30).mean()
            df_w['sma30w_slope'] = df_w['sma30w'].diff(3)
            df_w['prev_20w_low'] = df_w['low'].rolling(20).min().shift(1)
            weekly_data[sym] = df_w
            
        df_15 = get_alpaca_bars(sym, "15Min", start_str=start_date, end_str=end_date)
        if df_15 is not None and len(df_15) >= 50:
            df_15['ema9'] = df_15['close'].ewm(span=9, adjust=False).mean()
            df_15['ema21'] = df_15['close'].ewm(span=21, adjust=False).mean()
            df_15['bb_mid'] = df_15['close'].rolling(20).mean()
            df_15['bb_std'] = df_15['close'].rolling(20).std()
            df_15['bb_lower'] = df_15['bb_mid'] - (2.0 * df_15['bb_std'])
            tr = pd.concat([df_15['high'] - df_15['low'], (df_15['high'] - df_15['close'].shift(1)).abs(), (df_15['low'] - df_15['close'].shift(1)).abs()], axis=1).max(axis=1)
            df_15['atr'] = tr.rolling(14).mean()
            df_15['swing_high'] = df_15['high'].rolling(40).max()
            df_15['swing_low'] = df_15['low'].rolling(40).min()
            intraday_data[sym] = df_15
        time.sleep(0.02)
        
    def get_pit_stage(sym, current_time):
        df_w = weekly_data.get(sym)
        if df_w is None: return "STAGE_UNKNOWN"
        past_w = df_w[df_w['time'] < current_time]
        if len(past_w) < 32: return "STAGE_UNKNOWN"
        row = past_w.iloc[-1]
        if row['close'] < row['sma30w'] and row['sma30w_slope'] < 0 and row['close'] <= (1.05 * row['prev_20w_low']):
            return "STAGE_4_DECLINING"
        return "STAGE_OTHER"

    def get_pit_breadth(sec, current_time):
        tickers = SECTOR_UNIVERSE.get(sec, [])
        s4 = sum(1 for s in tickers if get_pit_stage(s, current_time) == "STAGE_4_DECLINING")
        return {"bearish": s4 >= 3, "s4": s4}

    for sym, df in intraday_data.items():
        sec = TICKER_SECTOR.get(sym)
        in_pos = False
        entry_px, initial_risk, virtual_sl, target_tp1, target_tp2 = 0.0, 0.0, 0.0, 0.0, 0.0
        tier = 0
        max_fav_px, max_adv_px = 0.0, 0.0
        last_check_date, cached_breadth = None, None

        for i in range(50, len(df)):
            row, prev = df.iloc[i], df.iloc[i-1]
            atr = row['atr']
            if pd.isna(atr) or atr <= 0: continue
            
            curr_time = row['time']
            bar_time = curr_time.time()
            in_session = (bar_time >= datetime.time(10, 0)) and (bar_time <= datetime.time(15, 50))

            if in_pos:
                max_fav_px = min(max_fav_px, row['low'])
                max_adv_px = max(max_adv_px, row['high'])

                if row['high'] >= (virtual_sl - SPREAD):
                    exit_px = virtual_sl + SLIP
                    trades.append({
                        "window": name, "symbol": sym, "sector": sec,
                        "raw_r": (entry_px - exit_px) / initial_risk,
                        "mae_r": max(0, (max_adv_px - entry_px) / initial_risk),
                        "mfe_r": max(0, (entry_px - max_fav_px) / initial_risk)
                    })
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
                if curr_time.date() != last_check_date:
                    cached_breadth = get_pit_breadth(sec, curr_time)
                    last_check_date = curr_time.date()

                if get_pit_stage(sym, curr_time) != "STAGE_4_DECLINING" or not cached_breadth['bearish']:
                    continue

                if row['ema9'] < row['ema21'] and row['close'] < row['bb_mid']:
                    s_high, s_low = prev['swing_high'], prev['swing_low']
                    fib_range = s_high - s_low
                    if fib_range <= (1.0 * atr): continue
                    
                    fib_382 = s_low + (0.382 * fib_range)
                    fib_500 = s_low + (0.500 * fib_range)
                    fib_618 = s_low + (0.618 * fib_range)
                    candidates = [fib_382, fib_500, row['ema21'], row['bb_mid']]
                    cluster = max([[lvl for lvl in candidates if abs(lvl - target) <= 0.5 * atr] for target in candidates], key=len)
                    
                    if (len(cluster) / len(candidates)) >= 0.5:
                        sell_min, sell_max = min(cluster) - (0.15 * atr), max(cluster) + (0.15 * atr)
                        if (row['high'] >= sell_min) and (row['close'] <= sell_max):
                            in_pos = True
                            entry_px = row['close'] - SLIP - SPREAD
                            initial_sl = fib_618 + (0.5 * atr)
                            initial_risk = max(initial_sl - entry_px, 1.2 * atr)
                            virtual_sl = entry_px + initial_risk
                            target_tp1 = max(row['bb_lower'], s_low)
                            target_tp2 = s_low - (0.272 * fib_range)
                            tier = 0
                            max_fav_px, max_adv_px = entry_px, entry_px

    return pd.DataFrame(trades)

print("=" * 115)
print(" 🔬 STAGE-4 SHORT SLEEVE: OUT-OF-SAMPLE WALK-FORWARD VALIDATION")
print("=" * 115)

master_wf = []
for name, (s_dt, e_dt, warm_dt) in STRESS_WINDOWS.items():
    tdf = run_stress_window(name, s_dt, e_dt, warm_dt)
    if len(tdf) == 0:
        print(f"\n▶ {name:<22} | 0 trades triggered (Breadth stayed dormant).")
        continue
    master_wf.append(tdf)
    r = tdf['raw_r'].values
    wins, losses = r[r > 0], r[r <= 0]
    cum = np.cumsum(r)
    max_dd = np.max(np.maximum.accumulate(cum) - cum)
    wr = (len(wins) / len(r)) * 100.0
    pf = abs(wins.sum() / (losses.sum() + 1e-9))
    exp = r.mean()
    avg_w = wins.mean() if len(wins) > 0 else 0.0
    avg_l = losses.mean() if len(losses) > 0 else 0.0
    
    print(f"\n▶ {name:<22} | Trades: {len(tdf):<3} | WR: {wr:4.1f}% | Net R: {r.sum():+6.2f}R | PF: {pf:4.2f} | Exp: {exp:+5.2f}R/tr")
    print(f"  • Payoff: Avg Win: {avg_w:+5.2f}R | Avg Loss: {avg_l:+5.2f}R | Realized DD: {max_dd:4.2f}R | Harvest/DD: {r.sum()/max_dd:4.2f}")
    print(f"  • Excursions: Mean MAE: {tdf['mae_r'].mean():.2f}R | Mean MFE: {tdf['mfe_r'].mean():.2f}R")

if master_wf:
    all_wf = pd.concat(master_wf)
    ar = all_wf['raw_r'].values
    awins, alosses = ar[ar > 0], ar[ar <= 0]
    acum = np.cumsum(ar)
    amax_dd = np.max(np.maximum.accumulate(acum) - acum)
    apf = abs(awins.sum() / (alosses.sum() + 1e-9))
    
    print("\n" + "=" * 115)
    print(" 🏆 AGGREGATE OUT-OF-SAMPLE STRESS TEST SUMMARY (2018 + 2020)")
    print("=" * 115)
    print(f"Total Out-of-Sample Trades      : {len(all_wf)}")
    print(f"Aggregate Win Rate              : {(len(awins)/len(ar))*100:.1f}%")
    print(f"Aggregate Net Harvest           : {ar.sum():+.2f}R")
    print(f"Aggregate Profit Factor         : {apf:.2f}")
    print(f"Aggregate Expectancy per Trade  : {ar.mean():+.2f}R")
    print(f"Aggregate Max Drawdown          : {amax_dd:.2f}R")
    print(f"Harvest / MaxDD Ratio           : {ar.sum()/amax_dd:.2f}")
    print("=" * 115)
