import os, time, datetime, json
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import requests

pd.set_option('future.no_silent_downcasting', True)

load_dotenv("/home/moonrider1968/.env")
APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID", os.getenv("ALPACA_API_KEY"))
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY", os.getenv("ALPACA_SECRET_KEY"))
DATA_URL = "https://data.alpaca.markets/v2"
NY_TZ = ZoneInfo("America/New_York")

XLK_LONG_UNIVERSE = ["MSFT", "NVDA", "AVGO", "AMD", "CRM"]
STAGE4_SHORT_FLEET = [
    "AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "TSLA", "AMZN", "GOOGL", "META",
    "JPM", "BAC", "WFC", "GS", "MS", "AXP",
    "CAT", "GE", "UNP", "HON", "RTX", "ETN"
]
DONCHIAN_SATELLITE_UNIVERSE = [
    "MSFT", "NVDA", "AVGO", "AMD", "CRM", "TSLA", "AMZN", "GOOGL", 
    "META", "AAPL", "NFLX", "SNOW", "PLTR", "DDOG", "CRWD", "NET", "PANW"
]

ALL_SYMBOLS = sorted(list(set(XLK_LONG_UNIVERSE + STAGE4_SHORT_FLEET + DONCHIAN_SATELLITE_UNIVERSE + ["SPY"])))

START_DATE = "2017-01-01"
END_DATE = "2026-08-20"
WARMUP_START = "2016-01-01"
INITIAL_NAV = 100000.0
SLIP, SPREAD = 0.03, 0.02
MAX_POSITIONS = 3

def get_alpaca_bars(symbol, timeframe="1Day", start_str="", end_str=""):
    headers = {"APCA-API-KEY-ID": APCA_API_KEY_ID, "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY}
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    all_bars = []
    page_token = None
    while True:
        params = {"timeframe": timeframe, "start": f"{start_str}T00:00:00Z", "end": f"{end_str}T23:59:59Z", "limit": 10000, "adjustment": "split", "feed": "sip"}
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

print("=" * 135)
print(" 🎲 GOVERNOR-AWARE PAIRED 5,000-PATH MONTE CARLO CERTIFICATION SUITE")
print("=" * 135)
print("📥 Fetching master daily and weekly data...")

master_daily = {}
for sym in ALL_SYMBOLS:
    df_d = get_alpaca_bars(sym, "1Day", start_str=WARMUP_START, end_str=END_DATE)
    if df_d is not None and len(df_d) > 60:
        df_d['ema20'] = df_d['close'].ewm(span=20, adjust=False).mean()
        df_d['sma50'] = df_d['close'].rolling(50).mean()
        df_d['ema10'] = df_d['close'].ewm(span=10, adjust=False).mean()
        df_d['ema21'] = df_d['close'].ewm(span=21, adjust=False).mean()
        df_d['donchian_high'] = df_d['high'].rolling(20).max().shift(1)
        df_d['donchian_55_d3'] = df_d['high'].rolling(55).max().shift(1)
        
        vol_sma = df_d['volume'].rolling(20).mean()
        df_d['rvol'] = df_d['volume'] / (vol_sma + 1e-9)
        
        tr = pd.concat([df_d['high'] - df_d['low'], (df_d['high'] - df_d['close'].shift(1)).abs(), (df_d['low'] - df_d['close'].shift(1)).abs()], axis=1).max(axis=1)
        df_d['atr'] = tr.rolling(14).mean()
        
        up_move = df_d["high"].diff()
        down_move = -df_d["low"].diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr14 = tr.rolling(14).sum()
        plus_di = 100 * pd.Series(plus_dm, index=df_d.index).rolling(14).sum() / (tr14 + 1e-9)
        minus_di = 100 * pd.Series(minus_dm, index=df_d.index).rolling(14).sum() / (tr14 + 1e-9)
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
        df_d["adx"] = dx.rolling(14).mean()

        df_d['ret_20d'] = df_d['close'].pct_change()
        df_d['vol_20d'] = df_d['ret_20d'].rolling(20).std() * np.sqrt(252)

        df_d['date'] = df_d['time'].dt.date
        df_d.set_index('date', inplace=True)
        master_daily[sym] = df_d
    time.sleep(0.01)

weekly_data = {}
for sym in sorted(list(set(XLK_LONG_UNIVERSE + STAGE4_SHORT_FLEET))):
    df_w = get_alpaca_bars(sym, "1Week", start_str=WARMUP_START, end_str=END_DATE)
    if df_w is not None and len(df_w) > 35:
        df_w['sma30w'] = df_w['close'].rolling(30).mean()
        df_w['sma30w_slope'] = df_w['sma30w'].diff(3)
        df_w['prev_20w_high'] = df_w['high'].rolling(20).max().shift(1)
        df_w['close_pit'] = df_w['close'].shift(1)
        df_w['sma30w_pit'] = df_w['sma30w'].shift(1)
        df_w['sma30w_slope_pit'] = df_w['sma30w_slope'].shift(1)
        df_w['prev_20w_high_pit'] = df_w['prev_20w_high'].shift(1)
        df_w['date'] = df_w['time'].dt.date
        df_w.set_index('date', inplace=True)
        weekly_data[sym] = df_w

for _sym in DONCHIAN_SATELLITE_UNIVERSE:
    _df = master_daily.get(_sym)
    if _df is not None:
        _df["donchian_55_d3"] = _df["high"].rolling(55).max().shift(1)

def get_pit_stage2(sym, current_date):
    df_w = weekly_data.get(sym)
    if df_w is None: return False
    past_w = df_w[df_w.index <= current_date]
    if len(past_w) < 32: return False
    row = past_w.iloc[-1]
    if pd.isna(row['sma30w_pit']) or pd.isna(row['sma30w_slope_pit']): return False
    return (row['close_pit'] > row['sma30w_pit'] and row['sma30w_slope_pit'] > 0 and row['close_pit'] >= (0.95 * row['prev_20w_high_pit']))

def get_pit_stage4(sym, current_date):
    df_w = weekly_data.get(sym)
    if df_w is None: return False
    past_w = df_w[df_w.index <= current_date]
    if len(past_w) < 30: return False
    row = past_w.iloc[-1]
    if pd.isna(row['sma30w_pit']) or pd.isna(row['sma30w_slope_pit']): return False
    return (row['close_pit'] < row['sma30w_pit'] and row['sma30w_slope_pit'] < 0)

spy_df = master_daily["SPY"]
spy_df['macro_bull'] = (spy_df['close'] > spy_df['ema20']) & (spy_df['ema20'] > spy_df['sma50'])
spy_df['macro_bull_pit'] = spy_df['macro_bull'].shift(1).fillna(False)
spy_macro_map = dict(zip(spy_df.index, spy_df['macro_bull_pit']))
spy_vol_map = dict(zip(spy_df.index, spy_df['vol_20d'].shift(1).fillna(0.15)))

start_dt = datetime.datetime.strptime(START_DATE, "%Y-%m-%d").date()
end_dt = datetime.datetime.strptime(END_DATE, "%Y-%m-%d").date()
dates = sorted(list(set.union(*[set(master_daily[s].index) for s in ALL_SYMBOLS if s in master_daily])))
dates = [d for d in dates if start_dt <= d <= end_dt]

start_dt_obj = datetime.datetime.strptime(START_DATE, "%Y-%m-%d")
end_dt_obj = datetime.datetime.strptime(END_DATE, "%Y-%m-%d")
years = (end_dt_obj - start_dt_obj).days / 365.25

def get_rolling_corr(sym1, sym2, curr_date, window=60):
    df1 = master_daily.get(sym1)
    df2 = master_daily.get(sym2)
    if df1 is None or df2 is None: return 0.0
    s1 = df1.loc[:curr_date, 'close'].pct_change().dropna().tail(window)
    s2 = df2.loc[:curr_date, 'close'].pct_change().dropna().tail(window)
    common = pd.concat([s1, s2], axis=1).dropna()
    if len(common) < 20: return 0.0
    return float(common.corr().iloc[0, 1])

def run_simulation(donchian_risk_pct=0.0025, mode="G2"):
    realized_pnl = 0.0
    open_longs = {}
    open_donch_longs = {}
    closed_trades = []
    daily_records = []
    pending_short_orders = []

    high_water = INITIAL_NAV
    governor_state = "B"
    state_dwell_counter = 0

    for curr_date in dates:
        macro_bull = spy_macro_map.get(curr_date, False)
        spy_vol = spy_vol_map.get(curr_date, 0.15)

        unrealized_longs_val = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_longs.items() if curr_date in master_daily[s].index)
        donch_unrealized_val = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_donch_longs.items() if curr_date in master_daily[s].index)
        cur_eq_est = INITIAL_NAV + realized_pnl + unrealized_longs_val + donch_unrealized_val
        high_water = max(high_water, cur_eq_est)
        dd_pct = (high_water - cur_eq_est) / high_water

        stress_confirm = (spy_vol > 0.25) and (not macro_bull)
        old_state = governor_state
        state_dwell_counter += 1

        if mode == "G2":
            if state_dwell_counter >= 3:
                if governor_state == "B":
                    if dd_pct >= 0.10: governor_state = "D3"
                    elif dd_pct >= 0.075 and stress_confirm: governor_state = "D2"
                    elif dd_pct >= 0.05: governor_state = "D1"
                elif governor_state == "D1":
                    if dd_pct >= 0.10: governor_state = "D3"
                    elif dd_pct >= 0.075 and stress_confirm: governor_state = "D2"
                    elif cur_eq_est >= high_water * 0.995: governor_state = "B"
                elif governor_state == "D2":
                    if dd_pct >= 0.10: governor_state = "D3"
                    elif dd_pct < 0.045: governor_state = "D1"
                elif governor_state == "D3":
                    if dd_pct < 0.070: governor_state = "D2"
            if governor_state != old_state:
                state_dwell_counter = 0

            if governor_state == "B":
                long_risk, short_risk, max_open_risk = 0.0100, 0.0050, 0.0300
                donch_mult = 1.0
                allow_long, allow_donch = True, True
            elif governor_state == "D1":
                long_risk, short_risk, max_open_risk = 0.0075, 0.0025, 0.0225
                donch_mult = 0.50
                allow_long, allow_donch = True, True
            elif governor_state == "D2":
                long_risk, short_risk, max_open_risk = 0.00375, 0.0025, 0.0150
                donch_mult = 0.25
                allow_long, allow_donch = True, True
            else:
                long_risk, short_risk, max_open_risk = 0.0, 0.0025, 0.0050
                donch_mult = 0.0
                allow_long, allow_donch = False, False
        else: # Static D25 (Config B base with standard D toggle)
            if governor_state == "B" and dd_pct >= 0.05:
                governor_state = "D1"
            elif governor_state == "D1" and cur_eq_est >= high_water * 0.995:
                governor_state = "B"
            
            if governor_state == "B":
                long_risk, short_risk, max_open_risk = 0.0100, 0.0050, 0.0300
                donch_mult = 1.0
                allow_long, allow_donch = True, True
            else:
                long_risk, short_risk, max_open_risk = 0.0075, 0.0025, 0.0225
                donch_mult = 0.50
                allow_long, allow_donch = True, True

        if not macro_bull and len(pending_short_orders) > 0 and len(open_longs) == 0:
            for order in pending_short_orders:
                sym = order['sym']
                df_d = master_daily.get(sym)
                if df_d is None or curr_date not in df_d.index: continue
                row = df_d.loc[curr_date]
                limit_px = order['target_entry']
                if row['high'] >= limit_px:
                    entry_px = max(row['open'], limit_px) - SLIP
                    risk_prs = order['risk_prs']
                    initial_sl = entry_px + risk_prs
                    target_px = entry_px - (1.5 * risk_prs)
                    hit_stop = (row['high'] >= initial_sl)
                    hit_target = (row['low'] <= target_px)
                    if hit_stop:
                        exit_px = initial_sl + SLIP
                        raw_r = -1.00
                    elif hit_target:
                        exit_px = target_px
                        raw_r = 1.50
                    else:
                        exit_px = row['close']
                        raw_r = (entry_px - exit_px) / risk_prs

                    qty = int((cur_eq_est * short_risk) / risk_prs)
                    if qty > 0:
                        pnl = (entry_px - exit_px) * qty
                        realized_pnl += pnl
                        closed_trades.append({"sleeve": "STAGE4_SHORT", "symbol": sym, "entry_date": order.get('entry_date', curr_date), "exit_date": curr_date, "raw_r": raw_r, "dollar_pnl": pnl, "days_held": 1})
        pending_short_orders = []

        for sym in list(open_longs.keys()):
            pos = open_longs[sym]
            df_d = master_daily.get(sym)
            if df_d is None or curr_date not in df_d.index: continue
            row = df_d.loc[curr_date]
            atr_d = row['atr'] if pd.notna(row['atr']) and row['atr'] > 0 else pos['initial_atr']
            if row['low'] <= pos['virtual_sl']:
                exit_px = min(row['open'], pos['virtual_sl']) - SLIP
                raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
                pnl = (exit_px - pos['entry_px']) * pos['qty']
                realized_pnl += pnl
                closed_trades.append({"sleeve": "XLK_LONG", "symbol": sym, "entry_date": pos['entry_date'], "exit_date": curr_date, "raw_r": raw_r, "dollar_pnl": pnl, "days_held": (curr_date - pos['entry_date']).days})
                del open_longs[sym]
                continue
            mfe_r = (row['high'] - pos['entry_px']) / pos['risk_prs']
            if mfe_r >= 1.5 and pos['tier'] < 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                pos['tier'] = 1
            if pos['tier'] >= 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], row['ema10'] - (0.5 * atr_d))

        if donchian_risk_pct > 0.0:
            for sym in list(open_donch_longs.keys()):
                pos = open_donch_longs[sym]
                df_d = master_daily.get(sym)
                if df_d is None or curr_date not in df_d.index: continue
                row = df_d.loc[curr_date]
                atr_d = row['atr'] if pd.notna(row['atr']) and row['atr'] > 0 else pos['initial_atr']
                if row['low'] <= pos['virtual_sl']:
                    exit_px = min(row['open'], pos['virtual_sl']) - SLIP
                    raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
                    pnl = (exit_px - pos['entry_px']) * pos['qty']
                    realized_pnl += pnl
                    closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": pos['entry_date'], "exit_date": curr_date, "raw_r": raw_r, "dollar_pnl": pnl, "days_held": (curr_date - pos['entry_date']).days})
                    del open_donch_longs[sym]
                    continue
                mfe_r = (row['high'] - pos['entry_px']) / pos['risk_prs']
                if mfe_r >= 1.5 and pos['tier'] < 1:
                    pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                    pos['tier'] = 1
                if pos['tier'] >= 1:
                    pos['virtual_sl'] = max(pos['virtual_sl'], row['sma50'] - (0.5 * atr_d))

        unrealized_longs = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_longs.items() if curr_date in master_daily[s].index)
        donch_unrealized = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_donch_longs.items() if curr_date in master_daily[s].index)
        current_eq = INITIAL_NAV + realized_pnl + unrealized_longs + donch_unrealized
        high_water = max(high_water, current_eq)

        active_symbols = set(open_longs.keys())
        if macro_bull and allow_long and len(open_longs) < MAX_POSITIONS:
            current_open_risk = sum(p['qty'] * max(0.0, p['entry_px'] - p['virtual_sl']) for p in open_longs.values())
            rem_risk_dollars = (current_eq * max_open_risk) - current_open_risk
            if rem_risk_dollars > 0:
                cands = []
                for sym in XLK_LONG_UNIVERSE:
                    if sym in active_symbols: continue
                    df_d = master_daily.get(sym)
                    if df_d is None or curr_date not in df_d.index: continue
                    idx = df_d.index.get_loc(curr_date)
                    if idx < 30: continue
                    row = df_d.iloc[idx]
                    prev = df_d.iloc[idx - 1]
                    atr = row['atr']
                    if pd.isna(atr) or atr <= 0: continue
                    if not get_pit_stage2(sym, curr_date): continue
                    breakout = (row['close'] > row['donchian_high']) and (prev['close'] <= prev['donchian_high'])
                    if breakout and row['rvol'] > 1.25 and row['close'] > row['ema21']:
                        cands.append((sym, row, atr, (row['rvol'] * 25.0) + (row['adx'] * 1.5)))
                cands.sort(key=lambda x: x[3], reverse=True)
                for sym, row, atr, score in cands:
                    if len(open_longs) >= MAX_POSITIONS: break
                    entry_px = row['close'] + SLIP + SPREAD
                    initial_sl = entry_px - (2.0 * atr)
                    rps = max(entry_px - initial_sl, 0.01)
                    dyn_risk = long_risk * min(max(row['rvol']/1.5, 0.8), 1.6) * min(max(row['adx']/25.0, 0.8), 1.5)
                    qty = int(min(current_eq * dyn_risk, rem_risk_dollars) / rps)
                    if qty > 0:
                        open_longs[sym] = {"entry_date": curr_date, "entry_px": entry_px, "qty": qty, "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "initial_atr": atr}
                        active_symbols.add(sym)
                        rem_risk_dollars -= (qty * rps)

        active_donch_risk = donchian_risk_pct * donch_mult
        if donchian_risk_pct > 0.0 and allow_donch and active_donch_risk > 0 and len(open_donch_longs) < MAX_POSITIONS and macro_bull:
            for sym in DONCHIAN_SATELLITE_UNIVERSE:
                if sym in open_donch_longs: continue
                df_d = master_daily.get(sym)
                if df_d is None or curr_date not in df_d.index: continue
                idx = df_d.index.get_loc(curr_date)
                if idx < 200: continue
                row = df_d.iloc[idx]
                atr = row['atr']
                if pd.isna(atr) or atr <= 0: continue
                if row['close'] > row['donchian_55_d3']:
                    max_corr = 0.0
                    for held_sym in open_donch_longs:
                        c = get_rolling_corr(sym, held_sym, curr_date, window=60)
                        max_corr = max(max_corr, c)
                    if max_corr >= 0.80: continue
                    entry_px = row['close'] + SLIP + SPREAD
                    initial_sl = entry_px - (2.00 * atr)
                    rps = max(entry_px - initial_sl, 0.01)
                    qty = int((current_eq * active_donch_risk) / rps)
                    if qty > 0:
                        open_donch_longs[sym] = {
                            "entry_date": curr_date, "entry_px": entry_px, "qty": qty,
                            "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "initial_atr": atr
                        }
                        break

        if not macro_bull and len(open_longs) == 0:
            for sym in STAGE4_SHORT_FLEET:
                df_d = master_daily.get(sym)
                if df_d is None or curr_date not in df_d.index: continue
                idx = df_d.index.get_loc(curr_date)
                if idx < 30: continue
                row = df_d.iloc[idx]
                atr = row['atr']
                if pd.isna(atr) or atr <= 0: continue
                if not get_pit_stage4(sym, curr_date): continue
                if (row['close'] < row['ema20']) and (row['ema20'] < row['sma50']):
                    pending_short_orders.append({"sym": sym, "target_entry": row['ema20'], "risk_prs": 0.75 * atr, "entry_date": curr_date})

        daily_records.append({"date": curr_date, "equity": current_eq, "governor": governor_state})

    if donchian_risk_pct > 0.0:
        for sym, pos in list(open_donch_longs.items()):
            df_d = master_daily[sym]
            window_df = df_d.loc[(df_d.index >= start_dt) & (df_d.index <= end_dt)]
            if window_df.empty: continue
            last_row = window_df.iloc[-1]
            exit_px = last_row['close'] - SLIP
            raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
            pnl = (exit_px - pos['entry_px']) * pos['qty']
            realized_pnl += pnl
            closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": pos.get('entry_date', start_dt), "exit_date": end_dt, "days_held": (end_dt - pos.get('entry_date', start_dt)).days, "raw_r": raw_r, "dollar_pnl": pnl})
            del open_donch_longs[sym]

    eq_df = pd.DataFrame(daily_records).drop_duplicates('date', keep='last').set_index('date')
    return eq_df

print("\n--- Generating Base Equity Curves for Resampling ---")
eq_stat = run_simulation(0.0025, mode="Static")
eq_g2 = run_simulation(0.0025, mode="G2")

ret_stat = eq_stat['equity'].pct_change().dropna().values
ret_g2 = eq_g2['equity'].pct_change().dropna().values
n_days = len(ret_stat)
block_size = 20
n_blocks = int(np.ceil(n_days / block_size))
n_sims = 5000

np.random.seed(42)
shared_starts = [np.random.randint(0, n_days - block_size + 1, size=n_blocks) for _ in range(n_sims)]

print(f"\n--- Running 5,000 Paired Bootstrap Simulations ({n_sims} paths) ---")

stat_cagrs, stat_dds, stat_calmars, stat_terms = [], [], [], []
g2_cagrs, g2_dds, g2_calmars, g2_terms = [], [], [], []

stat_dd_gt_10, stat_dd_gt_125, stat_dd_gt_15, stat_dd_gt_20 = 0, 0, 0, 0
g2_dd_gt_10, g2_dd_gt_125, g2_dd_gt_15, g2_dd_gt_20 = 0, 0, 0, 0

for starts in shared_starts:
    # Static
    b_stat = np.concatenate([ret_stat[s:s + block_size] for s in starts])[:n_days]
    eq_s = INITIAL_NAV * np.cumprod(1.0 + b_stat)
    term_s = eq_s[-1]
    cagr_s = (((term_s / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
    peak_s = np.maximum.accumulate(eq_s)
    dd_s = np.max((peak_s - eq_s) / peak_s) * 100.0
    calmar_s = cagr_s / dd_s if dd_s > 0 else 0.0

    stat_cagrs.append(cagr_s)
    stat_dds.append(dd_s)
    stat_calmars.append(calmar_s)
    stat_terms.append(term_s)

    if dd_s > 10.0: stat_dd_gt_10 += 1
    if dd_s > 12.5: stat_dd_gt_125 += 1
    if dd_s > 15.0: stat_dd_gt_15 += 1
    if dd_s > 20.0: stat_dd_gt_20 += 1

    # G2
    b_g2 = np.concatenate([ret_g2[s:s + block_size] for s in starts])[:n_days]
    eq_g = INITIAL_NAV * np.cumprod(1.0 + b_g2)
    term_g = eq_g[-1]
    cagr_g = (((term_g / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
    peak_g = np.maximum.accumulate(eq_g)
    dd_g = np.max((peak_g - eq_g) / peak_g) * 100.0
    calmar_g = cagr_g / dd_g if dd_g > 0 else 0.0

    g2_cagrs.append(cagr_g)
    g2_dds.append(dd_g)
    g2_calmars.append(calmar_g)
    g2_terms.append(term_g)

    if dd_g > 10.0: g2_dd_gt_10 += 1
    if dd_g > 12.5: g2_dd_gt_125 += 1
    if dd_g > 15.0: g2_dd_gt_15 += 1
    if dd_g > 20.0: g2_dd_gt_20 += 1

print("\n" + "=" * 135)
print(" 📊 PAIRED MONTE CARLO CERTIFICATION RESULTS (5,000 SIMS)")
print("=" * 135)
print(f"{'Metric':<30} | {'Static D25 (Baseline)':<28} | {'G2 Governor':<28} | {'Delta / Impact':<20}")
print("-" * 135)
print(f"{'Median CAGR':<30} | {np.median(stat_cagrs):>26.2f}% | {np.median(g2_cagrs):>26.2f}% | {np.median(g2_cagrs)-np.median(stat_cagrs):>+18.2f}%")
print(f"{'5th Percentile CAGR':<30} | {np.percentile(stat_cagrs, 5):>26.2f}% | {np.percentile(g2_cagrs, 5):>26.2f}% | {np.percentile(g2_cagrs, 5)-np.percentile(stat_cagrs, 5):>+18.2f}%")
print(f"{'1st Percentile CAGR':<30} | {np.percentile(stat_cagrs, 1):>26.2f}% | {np.percentile(g2_cagrs, 1):>26.2f}% | {np.percentile(g2_cagrs, 1)-np.percentile(stat_cagrs, 1):>+18.2f}%")
print(f"{'Median Max Drawdown':<30} | {np.median(stat_dds):>26.2f}% | {np.median(g2_dds):>26.2f}% | {np.median(g2_dds)-np.median(stat_dds):>+18.2f}%")
print(f"{'90th Percentile Max DD':<30} | {np.percentile(stat_dds, 90):>26.2f}% | {np.percentile(g2_dds, 90):>26.2f}% | {np.percentile(g2_dds, 90)-np.percentile(stat_dds, 90):>+18.2f}%")
print(f"{'95th Percentile Max DD':<30} | {np.percentile(stat_dds, 95):>26.2f}% | {np.percentile(g2_dds, 95):>26.2f}% | {np.percentile(g2_dds, 95)-np.percentile(stat_dds, 95):>+18.2f}%")
print(f"{'99th Percentile Max DD':<30} | {np.percentile(stat_dds, 99):>26.2f}% | {np.percentile(g2_dds, 99):>26.2f}% | {np.percentile(g2_dds, 99)-np.percentile(stat_dds, 99):>+18.2f}%")
print(f"{'Median Calmar Ratio':<30} | {np.median(stat_calmars):>26.2f}  | {np.median(g2_calmars):>26.2f}  | {np.median(g2_calmars)-np.median(stat_calmars):>+18.2f}")
print(f"{'Positive Terminal Prob':<30} | {(sum(np.array(stat_terms)>INITIAL_NAV)/n_sims)*100:>25.1f}% | {(sum(np.array(g2_terms)>INITIAL_NAV)/n_sims)*100:>25.1f}% | {((sum(np.array(g2_terms)>INITIAL_NAV)-sum(np.array(stat_terms)>INITIAL_NAV))/n_sims)*100:>+17.1f}%")
print(f"{'Prob(Max DD > 10.0%)':<30} | {(stat_dd_gt_10/n_sims)*100:>25.1f}% | {(g2_dd_gt_10/n_sims)*100:>25.1f}% | {((g2_dd_gt_10-stat_dd_gt_10)/n_sims)*100:>+17.1f}%")
print(f"{'Prob(Max DD > 12.5%)':<30} | {(stat_dd_gt_125/n_sims)*100:>25.1f}% | {(g2_dd_gt_125/n_sims)*100:>25.1f}% | {((g2_dd_gt_125-stat_dd_gt_125)/n_sims)*100:>+17.1f}%")
print(f"{'Prob(Max DD > 15.0%)':<30} | {(stat_dd_gt_15/n_sims)*100:>25.1f}% | {(g2_dd_gt_15/n_sims)*100:>25.1f}% | {((g2_dd_gt_15-stat_dd_gt_15)/n_sims)*100:>+17.1f}%")
print(f"{'Prob(Max DD > 20.0%)':<30} | {(stat_dd_gt_20/n_sims)*100:>25.1f}% | {(g2_dd_gt_20/n_sims)*100:>25.1f}% | {((g2_dd_gt_20-stat_dd_gt_20)/n_sims)*100:>+17.1f}%")
print(f"{'Worst Path Ending NAV':<30} | ${min(stat_terms):>24,.0f} | ${min(g2_terms):>24,.0f} | ${min(g2_terms)-min(stat_terms):>+16,.0f}")
print("=" * 135)
