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
print(" 🏛️ G1 MULTI-STAGE GOVERNOR HISTORICAL AUDIT & RECONCILIATION")
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
        
        plus_dm = df_d['high'].diff()
        minus_dm = -df_d['low'].diff()
        plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
        minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)
        tr14 = tr.rolling(14).sum()
        plus_di = 100 * pd.Series(plus_dm).rolling(14).sum() / (tr14 + 1e-9)
        minus_di = 100 * pd.Series(minus_dm).rolling(14).sum() / (tr14 + 1e-9)
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
        df_d['adx'] = pd.Series(dx).rolling(14).mean()

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

def run_multi_stage_simulation(donchian_risk_pct=0.0025, enable_multistage=True):
    realized_pnl = 0.0
    open_longs = {}
    open_donch_longs = {}
    closed_trades = []
    daily_records = []
    pending_short_orders = []

    high_water = INITIAL_NAV
    governor_state = "B"

    for curr_date in dates:
        macro_bull = spy_macro_map.get(curr_date, False)
        spy_vol = spy_vol_map.get(curr_date, 0.15)

        unrealized_longs_val = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_longs.items() if curr_date in master_daily[s].index)
        donch_unrealized_val = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_donch_longs.items() if curr_date in master_daily[s].index)
        cur_eq_est = INITIAL_NAV + realized_pnl + unrealized_longs_val + donch_unrealized_val
        high_water = max(high_water, cur_eq_est)
        dd_pct = (high_water - cur_eq_est) / high_water

        if enable_multistage:
            if governor_state == "B":
                if dd_pct >= 0.10 or spy_vol > 0.30: governor_state = "D3"
                elif dd_pct >= 0.075 or spy_vol > 0.25: governor_state = "D2"
                elif dd_pct >= 0.05: governor_state = "D1"
            elif governor_state == "D1":
                if dd_pct >= 0.10 or spy_vol > 0.30: governor_state = "D3"
                elif dd_pct >= 0.075 or spy_vol > 0.25: governor_state = "D2"
                elif cur_eq_est >= high_water * 0.995: governor_state = "B"
            elif governor_state == "D2":
                if dd_pct >= 0.10 or spy_vol > 0.30: governor_state = "D3"
                elif dd_pct <= 0.05: governor_state = "D1"
            elif governor_state == "D3":
                if dd_pct <= 0.075: governor_state = "D2"
        else:
            if governor_state == "B" and dd_pct >= 0.05:
                governor_state = "D1"
            elif governor_state == "D1" and cur_eq_est >= high_water * 0.995:
                governor_state = "B"

        if not enable_multistage:
            if governor_state == "B":
                long_risk, short_risk, max_open_risk = 0.0100, 0.0050, 0.0300
                donch_mult = 1.0
                allow_long, allow_donch = True, True
            else:
                long_risk, short_risk, max_open_risk = 0.0075, 0.0025, 0.0225
                donch_mult = 0.50
                allow_long, allow_donch = True, True
        else:
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
    return eq_df, pd.DataFrame(closed_trades)

print("\n--- 1. CONTROL REGRESSION (Classic Static D25) ---")
eq_static, _ = run_multi_stage_simulation(0.0025, enable_multistage=False)
end_stat = eq_static['equity'].iloc[-1]
cagr_stat = (((end_stat / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
dd_stat = ((eq_static['equity'].cummax() - eq_static['equity']) / eq_static['equity'].cummax()).max() * 100.0
print(f"Static D25 Control -> NAV: ${end_stat:,.2f} | CAGR: {cagr_stat:.2f}% | Max DD: {dd_stat:.2f}%")

print("\n--- 2. G1 MULTI-STAGE DYNAMIC GOVERNOR ---")
eq_multi, _ = run_multi_stage_simulation(0.0025, enable_multistage=True)
end_multi = eq_multi['equity'].iloc[-1]
cagr_multi = (((end_multi / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
dd_multi = ((eq_multi['equity'].cummax() - eq_multi['equity']) / eq_multi['equity'].cummax()).max() * 100.0

print(f"G1 Multi-Stage     -> NAV: ${end_multi:,.2f} | CAGR: {cagr_multi:.2f}% | Max DD: {dd_multi:.2f}%")

print("\nG1 Governor State Occupancy:")
print(eq_multi["governor"].value_counts())
print()

print("G1 Governor State Percentages:")
print(
    eq_multi["governor"]
    .value_counts(normalize=True)
    .mul(100)
    .round(2)
)
print()

states = eq_multi["governor"]
transitions = (states != states.shift(1))
print("Total governor transitions:", int(transitions.sum() - 1))
print("=" * 135)
