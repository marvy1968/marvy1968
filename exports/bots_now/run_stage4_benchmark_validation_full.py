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

FULL_STAGE4_FLEET = [
    # XLK Tech
    "AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "TSLA", "AMZN", "GOOGL", "META",
    # XLF Financials
    "JPM", "BAC", "WFC", "GS", "MS", "AXP",
    # XLI Industrials
    "CAT", "GE", "UNP", "HON", "RTX", "ETN"
]

BENCHMARK_START = "2022-01-03"
BENCHMARK_END = "2022-06-30"
WARMUP_START = "2021-01-01"

INITIAL_NAV = 100000.0
SLIP, SPREAD = 0.03, 0.02

def get_alpaca_bars(symbol, timeframe="1Day", start_str="", end_str=""):
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
        if page_token: params["page_token"] = page_token
        try:
            r = requests.get(url, headers=headers, params=params, timeout=25)
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

print("=" * 80)
print("RUNNING 22-TICKER STAGE-4 SHORT RECONCILIATION TEST")
print(f"Window: {BENCHMARK_START} -> {BENCHMARK_END}")
print("=" * 80)
print("📥 Fetching historical data for 22-ticker fleet...")

master_daily = {}
weekly_data = {}
all_syms = list(set(FULL_STAGE4_FLEET + ["SPY"]))

for sym in all_syms:
    df_d = get_alpaca_bars(sym, "1Day", start_str=WARMUP_START, end_str=BENCHMARK_END)
    if df_d is not None and len(df_d) > 60:
        df_d['ema10'] = df_d['close'].ewm(span=10, adjust=False).mean()
        df_d['ema20'] = df_d['close'].ewm(span=20, adjust=False).mean()
        df_d['ema21'] = df_d['close'].ewm(span=21, adjust=False).mean()
        df_d['sma50'] = df_d['close'].rolling(50).mean()
        df_d['donchian_low'] = df_d['low'].rolling(20).min().shift(1)
        tr = pd.concat([df_d['high'] - df_d['low'], 
                        (df_d['high'] - df_d['close'].shift(1)).abs(), 
                        (df_d['low'] - df_d['close'].shift(1)).abs()], axis=1).max(axis=1)
        df_d['atr'] = tr.rolling(14).mean()
        df_d['vol_sma20'] = df_d['volume'].rolling(20).mean()
        df_d['rvol'] = df_d['volume'] / (df_d['vol_sma20'] + 1e-9)
        df_d['date'] = df_d['time'].dt.date
        df_d.set_index('date', inplace=True)
        master_daily[sym] = df_d

    df_w = get_alpaca_bars(sym, "1Week", start_str=WARMUP_START, end_str=BENCHMARK_END)
    if df_w is not None and len(df_w) >= 30:
        df_w['sma30w'] = df_w['close'].rolling(30).mean()
        df_w['sma30w_slope'] = df_w['sma30w'].diff(3)
        df_w['prev_20w_low'] = df_w['low'].rolling(20).min().shift(1)
        df_w['sma30w_pit'] = df_w['sma30w'].shift(1)
        df_w['sma30w_slope_pit'] = df_w['sma30w_slope'].shift(1)
        df_w['prev_20w_low_pit'] = df_w['prev_20w_low'].shift(1)
        df_w['close_pit'] = df_w['close'].shift(1)
        df_w['date'] = df_w['time'].dt.date
        weekly_data[sym] = df_w
    time.sleep(0.01)

spy_df = master_daily["SPY"]
spy_df['macro_bear'] = (spy_df['close'] < spy_df['ema20']) & (spy_df['ema20'] < spy_df['sma50'])
spy_df['macro_bear_pit'] = spy_df['macro_bear'].shift(1).fillna(False)
spy_macro_map = dict(zip(spy_df.index, spy_df['macro_bear_pit']))

def get_pit_stage4(sym, current_date):
    df_w = weekly_data.get(sym)
    if df_w is None: return False
    past_w = df_w[df_w['date'] <= current_date]
    if len(past_w) < 30: return False
    row = past_w.iloc[-1]
    if pd.isna(row['sma30w_pit']) or pd.isna(row['sma30w_slope_pit']): return False
    return (row['close_pit'] < row['sma30w_pit'] and 
            row['sma30w_slope_pit'] < 0)

start_dt = datetime.datetime.strptime(BENCHMARK_START, "%Y-%m-%d").date()
end_dt = datetime.datetime.strptime(BENCHMARK_END, "%Y-%m-%d").date()
dates = sorted(list(set.union(*[set(master_daily[s].index) for s in FULL_STAGE4_FLEET if s in master_daily])))
dates = [d for d in dates if start_dt <= d <= end_dt]

trades = []

for curr_date in dates:
    macro_bear = spy_macro_map.get(curr_date, False)
    if not macro_bear:
        continue

    for sym in FULL_STAGE4_FLEET:
        df_d = master_daily.get(sym)
        if df_d is None or curr_date not in df_d.index: continue
        idx = df_d.index.get_loc(curr_date)
        if idx < 30: continue
        row = df_d.iloc[idx]
        atr = row['atr']
        if pd.isna(atr) or atr <= 0: continue

        if not get_pit_stage4(sym, curr_date): continue

        stage4_context = (row['close'] < row['ema20']) and (row['ema20'] < row['sma50'])
        rally_test = (row['high'] >= row['ema20']) and (row['close'] < row['ema20'])

        if stage4_context and rally_test:
            entry_px = row['ema20'] - SLIP
            risk_prs = 0.75 * atr
            initial_sl = entry_px + risk_prs

            target_hit = row['low'] <= (entry_px - (1.5 * risk_prs))
            stopped_out = row['high'] >= initial_sl

            if target_hit and not stopped_out:
                exit_px = entry_px - (1.5 * risk_prs)
                raw_r = 1.50
                reason = "TARGET_HIT"
            elif stopped_out:
                exit_px = initial_sl + SLIP
                raw_r = -1.00
                reason = "STOP_HIT"
            else:
                exit_px = row['close']
                raw_r = (entry_px - exit_px) / risk_prs
                reason = "EOD_FLAT"

            qty = int((INITIAL_NAV * 0.0075) / risk_prs)
            pnl = (entry_px - exit_px) * qty

            trades.append({
                "symbol": sym, "entry_date": curr_date, "exit_date": curr_date,
                "entry": entry_px, "exit": exit_px, "stop": initial_sl,
                "r_multiple": raw_r, "dollar_pnl": pnl, "exit_reason": reason
            })

tdf = pd.DataFrame(trades)

total_trades = len(tdf)
r_arr = tdf['r_multiple'].values
wins = r_arr[r_arr > 0]
losses = r_arr[r_arr <= 0]
win_rate = len(wins) / total_trades if total_trades > 0 else 0
net_r = float(np.sum(r_arr)) if total_trades > 0 else 0
exp_r = float(np.mean(r_arr)) if total_trades > 0 else 0
pf = float(np.sum(wins) / np.abs(np.sum(losses))) if len(losses) > 0 else float("inf")

cum_r = np.cumsum(r_arr) if total_trades > 0 else np.array([0])
peak_r = np.maximum.accumulate(cum_r)
max_dd_r = float(np.max(peak_r - cum_r)) if len(cum_r) > 0 else 0.0

print("-" * 80)
print(f"{'Metric':<20} | {'22-Ticker Fleet Actual':<25}")
print("-" * 80)
print(f"{'Trades':<20} | {total_trades:<25}")
print(f"{'Win Rate':<20} | {win_rate*100:.1f}%{'':<20}")
print(f"{'Net R':<20} | +{net_r:.2f}R{'':<19}")
print(f"{'Profit Factor':<20} | {pf:<25.2f}")
print(f"{'Expectancy':<20} | +{exp_r:.2f}R/tr{'':<16}")
print(f"{'Max Drawdown':<20} | {max_dd_r:.2f}R{'':<20}")
print("-" * 80)
