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

BULL_WINDOW = {
    "BULL_EXPANSION_2023_2024": ("2023-11-01", "2024-06-30", "2022-11-01")
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

def run_bull_momentum_backtest():
    SLIP, SPREAD = 0.03, 0.02
    start_date, end_date, warm_date = BULL_WINDOW["BULL_EXPANSION_2023_2024"]
    
    print(f"📥 Loading SPY Daily, Weekly, and 15m bars ({start_date} to {end_date})...")
    
    # 1. Macro SPY Daily Engine
    spy_df = get_alpaca_bars("SPY", "1Day", start_str=warm_date, end_str=end_date)
    spy_df['ema20'] = spy_df['close'].ewm(span=20, adjust=False).mean()
    spy_df['sma50'] = spy_df['close'].rolling(50).mean()
    spy_df['date'] = spy_df['time'].dt.date
    spy_regime_map = {row['date']: (row['close'] > row['ema20'] and row['ema20'] > row['sma50']) for _, row in spy_df.iterrows()}

    weekly_data = {}
    intraday_data = {}
    all_symbols = [s for tickers in SECTOR_UNIVERSE.values() for s in tickers]

    for sym in all_symbols:
        df_w = get_alpaca_bars(sym, "1Week", start_str=warm_date, end_str=end_date)
        if df_w is not None and len(df_w) >= 35:
            df_w['sma30w'] = df_w['close'].rolling(30).mean()
            df_w['sma30w_slope'] = df_w['sma30w'].diff(3)
            df_w['prev_20w_high'] = df_w['high'].rolling(20).max().shift(1)
            weekly_data[sym] = df_w

        df_15 = get_alpaca_bars(sym, "15Min", start_str=start_date, end_str=end_date)
        if df_15 is not None and len(df_15) >= 50:
            df_15['ema9'] = df_15['close'].ewm(span=9, adjust=False).mean()
            df_15['ema21'] = df_15['close'].ewm(span=21, adjust=False).mean()
            df_15['bb_mid'] = df_15['close'].rolling(20).mean()
            df_15['bb_std'] = df_15['close'].rolling(20).std()
            df_15['bb_upper'] = df_15['bb_mid'] + (2.0 * df_15['bb_std'])
            tr = pd.concat([df_15['high'] - df_15['low'], (df_15['high'] - df_15['close'].shift(1)).abs(), (df_15['low'] - df_15['close'].shift(1)).abs()], axis=1).max(axis=1)
            df_15['atr'] = tr.rolling(14).mean()
            df_15['vol_sma'] = df_15['volume'].rolling(20).mean()
            df_15['adx'], _, _ = compute_adx(df_15)
            df_15['donchian_high'] = df_15['high'].rolling(20).max().shift(1)
            df_15['swing_low'] = df_15['low'].rolling(20).min().shift(1)
            intraday_data[sym] = df_15
        time.sleep(0.02)

    def get_pit_stage(sym, current_time):
        df_w = weekly_data.get(sym)
        if df_w is None: return False
        past_w = df_w[df_w['time'] < current_time]
        if len(past_w) < 32: return False
        row = past_w.iloc[-1]
        return row['close'] > row['sma30w'] and row['sma30w_slope'] > 0 and row['close'] >= (0.95 * row['prev_20w_high'])

    def get_pit_sector_breadth(sec, current_time):
        tickers = SECTOR_UNIVERSE.get(sec, [])
        s2 = sum(1 for s in tickers if get_pit_stage(s, current_time))
        return s2 >= 4

    trades = []
    print("⚙️ Simulating Sleeve #2 Bull Momentum Breakouts across fleet...")

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
            curr_date = curr_time.date()
            bar_time = curr_time.time()
            in_session = (bar_time >= datetime.time(10, 0)) and (bar_time <= datetime.time(15, 50))

            if in_pos:
                max_fav_px = max(max_fav_px, row['high'])
                max_adv_px = min(max_adv_px, row['low'])

                # Stop Hit
                if row['low'] <= (virtual_sl + SPREAD):
                    exit_px = virtual_sl - SLIP
                    trades.append({
                        "symbol": sym, "sector": sec, "side": "LONG",
                        "raw_r": (exit_px - entry_px) / initial_risk,
                        "mae_r": max(0, (entry_px - max_adv_px) / initial_risk),
                        "mfe_r": max(0, (max_fav_px - entry_px) / initial_risk)
                    })
                    in_pos = False
                    continue

                # Trailing Ratchets
                if row['high'] >= target_tp1 and tier < 1:
                    virtual_sl = max(virtual_sl, entry_px + (0.05 * atr))
                    tier = 1
                if row['high'] >= target_tp2 and tier < 2:
                    virtual_sl = max(virtual_sl, row['ema21'] - (1.2 * atr))
                    tier = 2
                elif tier == 2:
                    virtual_sl = max(virtual_sl, row['ema21'] - (1.2 * atr))

            else:
                if not in_session: continue
                if not spy_regime_map.get(curr_date, False): continue

                if curr_date != last_check_date:
                    cached_breadth = get_pit_sector_breadth(sec, curr_time)
                    last_check_date = curr_date

                if not cached_breadth or not get_pit_stage(sym, curr_time):
                    continue

                # MOMENTUM CONTINUATION TRIGGER
                # 1. Breakout above 20-bar Donchian high
                # 2. Volume expansion > 1.25x SMA20
                # 3. Trend alignment: EMA9 > EMA21 > BB Mid and ADX >= 20
                if (row['close'] > prev['donchian_high'] and 
                    row['volume'] > (1.25 * row['vol_sma']) and 
                    row['ema9'] > row['ema21'] and 
                    row['ema21'] > row['bb_mid'] and 
                    row['adx'] >= 20.0):

                    in_pos = True
                    entry_px = row['close'] + SLIP + SPREAD
                    initial_sl = max(prev['swing_low'] - (0.3 * atr), row['ema21'] - (1.2 * atr))
                    initial_risk = max(entry_px - initial_sl, 1.2 * atr)
                    virtual_sl = entry_px - initial_risk
                    target_tp1 = entry_px + (1.8 * initial_risk)
                    target_tp2 = entry_px + (2.8 * initial_risk)
                    tier = 0
                    max_fav_px, max_adv_px = entry_px, entry_px

    return pd.DataFrame(trades)

print("=" * 115)
print(" 🚀 SLEEVE #2: BULL MOMENTUM CONTINUATION BACKTEST (2023-11-01 to 2024-06-30)")
print("=" * 115)

tdf = run_bull_momentum_backtest()
if len(tdf) == 0:
    print("❌ No trades triggered.")
    exit()

r = tdf['raw_r'].values
wins, losses = r[r > 0], r[r <= 0]
cum = np.cumsum(r)
max_dd = np.max(np.maximum.accumulate(cum) - cum)
wr = (len(wins) / len(r)) * 100.0
pf = abs(wins.sum() / (losses.sum() + 1e-9))
exp = r.mean()
avg_w = wins.mean() if len(wins) > 0 else 0.0
avg_l = losses.mean() if len(losses) > 0 else 0.0

print(f"• Total Trades Evaluated (N) : {len(tdf)}")
print(f"• Win Rate (%)               : {wr:.1f}%")
print(f"• Total Harvest (R)          : {r.sum():+.2f}R")
print(f"• Profit Factor (PF)         : {pf:.2f}")
print(f"• Expectancy per Trade       : {exp:+.2f}R/trade")
print(f"• Average Winner / Loser     : {avg_w:+.2f}R / {avg_l:+.2f}R")
print(f"• Payoff Ratio (Avg W / L)   : {abs(avg_w/(avg_l+1e-9)):.2f}")
print(f"• Realized Max Drawdown      : {max_dd:.2f}R")
print(f"• Harvest / MaxDD Ratio      : {r.sum()/max_dd:.2f}")
print(f"• Mean Adverse Excursion     : {tdf['mae_r'].mean():.2f}R")
print(f"• Mean Favorable Excursion   : {tdf['mfe_r'].mean():.2f}R")
print("-" * 115)

print("\n📊 Sector Attribution Breakdown:")
sec_grp = tdf.groupby('sector').agg(
    Trades=('raw_r', 'count'),
    WinRate=('raw_r', lambda x: f"{(x > 0).mean()*100:.1f}%"),
    Net_R=('raw_r', 'sum'),
    PF=('raw_r', lambda x: abs(x[x > 0].sum() / (x[x <= 0].sum() + 1e-9)))
).reset_index()
print(sec_grp.to_string(index=False))

print("\n📈 Ticker Attribution Breakdown:")
tick_grp = tdf.groupby('symbol').agg(
    Trades=('raw_r', 'count'),
    WinRate=('raw_r', lambda x: f"{(x > 0).mean()*100:.1f}%"),
    Net_R=('raw_r', 'sum'),
    PF=('raw_r', lambda x: abs(x[x > 0].sum() / (x[x <= 0].sum() + 1e-9)))
).reset_index()
print(tick_grp.to_string(index=False))
print("=" * 115)
