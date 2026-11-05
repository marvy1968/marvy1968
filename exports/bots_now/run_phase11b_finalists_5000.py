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
print(" 🏛️ PHASE 11B: PAIRED CONTROL & FINALIST 5,000-PATH CERTIFICATION")
print("=" * 135)

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
years = (end_dt - start_dt).days / 365.25

def get_rolling_corr(sym1, sym2, curr_date, window=60):
    df1 = master_daily.get(sym1)
    df2 = master_daily.get(sym2)
    if df1 is None or df2 is None: return 0.0
    s1 = df1.loc[:curr_date, 'close'].pct_change().dropna().tail(window)
    s2 = df2.loc[:curr_date, 'close'].pct_change().dropna().tail(window)
    common = pd.concat([s1, s2], axis=1).dropna()
    if len(common) < 20: return 0.0
    return float(common.corr().iloc[0, 1])

tape = []
for curr_date in dates:
    macro_bull = spy_macro_map.get(curr_date, False)
    spy_vol = spy_vol_map.get(curr_date, 0.15)
    
    asset_tape = {}
    for sym in ALL_SYMBOLS:
        df_d = master_daily.get(sym)
        if df_d is not None and curr_date in df_d.index:
            idx = df_d.index.get_loc(curr_date)
            row = df_d.iloc[idx]
            prev_c = df_d.iloc[idx-1]['close'] if idx > 0 else row['open']
            asset_tape[sym] = {
                "open_rel": row['open'] / prev_c,
                "high_rel": row['high'] / prev_c,
                "low_rel": row['low'] / prev_c,
                "close_rel": row['close'] / prev_c,
                "atr_pct": row['atr'] / prev_c if pd.notna(row['atr']) else 0.03,
                "ema10_rel": row['ema10'] / prev_c if pd.notna(row['ema10']) else 1.0,
                "sma50_rel": row['sma50'] / prev_c if pd.notna(row['sma50']) else 1.0,
                "rvol": row['rvol'] if pd.notna(row['rvol']) else 1.0,
                "adx": row['adx'] if pd.notna(row['adx']) else 25.0
            }
        else:
            asset_tape[sym] = {"open_rel": 1.0, "high_rel": 1.0, "low_rel": 1.0, "close_rel": 1.0, "atr_pct": 0.03, "ema10_rel": 1.0, "sma50_rel": 1.0, "rvol": 1.0, "adx": 25.0}

    long_signals = []
    if macro_bull:
        for sym in XLK_LONG_UNIVERSE:
            df_d = master_daily.get(sym)
            if df_d is None or curr_date not in df_d.index: continue
            idx = df_d.index.get_loc(curr_date)
            if idx < 30: continue
            row = df_d.iloc[idx]
            prev = df_d.iloc[idx - 1]
            atr = row['atr']
            prev_c = prev['close']
            if pd.isna(atr) or atr <= 0: continue
            if not get_pit_stage2(sym, curr_date): continue
            breakout = (row['close'] > row['donchian_high']) and (prev['close'] <= prev['donchian_high'])
            if breakout and row['rvol'] > 1.25 and row['close'] > row['ema21']:
                score = (row['rvol'] * 25.0) + (row['adx'] * 1.5)
                long_signals.append({
                    "sym": sym,
                    "score": score,
                    "entry_rel": (row['close'] + SLIP + SPREAD) / prev_c,
                    "atr_pct": atr / prev_c
                })
        long_signals.sort(key=lambda x: x['score'], reverse=True)

    donch_signals = []
    donch_corrs = {}
    if macro_bull:
        for i, sym1 in enumerate(DONCHIAN_SATELLITE_UNIVERSE):
            df_d = master_daily.get(sym1)
            if df_d is None or curr_date not in df_d.index: continue
            idx = df_d.index.get_loc(curr_date)
            if idx < 200: continue
            row = df_d.iloc[idx]
            prev = df_d.iloc[idx - 1]
            atr = row['atr']
            prev_c = prev['close']
            if pd.isna(atr) or atr <= 0: continue
            if row['close'] > row['donchian_55_d3']:
                donch_signals.append({
                    "sym": sym1,
                    "entry_rel": (row['close'] + SLIP + SPREAD) / prev_c,
                    "atr_pct": atr / prev_c
                })
                for sym2 in DONCHIAN_SATELLITE_UNIVERSE:
                    if sym1 != sym2:
                        c = get_rolling_corr(sym1, sym2, curr_date, window=60)
                        donch_corrs[f"{sym1}|{sym2}"] = c

    short_signals = []
    if not macro_bull:
        for sym in STAGE4_SHORT_FLEET:
            df_d = master_daily.get(sym)
            if df_d is None or curr_date not in df_d.index: continue
            idx = df_d.index.get_loc(curr_date)
            if idx < 30: continue
            row = df_d.iloc[idx]
            prev = df_d.iloc[idx - 1]
            atr = row['atr']
            if pd.isna(atr) or atr <= 0: continue
            if not get_pit_stage4(sym, curr_date): continue
            if (row['close'] < row['ema20']) and (row['ema20'] < row['sma50']):
                short_signals.append({
                    "sym": sym,
                    "target_rel": row['ema20'] / row['close'],
                    "atr_pct": atr / row['close']
                })

    tape.append({
        "date": curr_date,
        "macro_bull": macro_bull,
        "spy_vol": spy_vol,
        "asset_tape": asset_tape,
        "long_signals": long_signals,
        "donch_signals": donch_signals,
        "donch_corrs": donch_corrs,
        "short_signals": short_signals
    })

print(f"✅ Normalized tape exported successfully: {len(tape)} trading days.")
n_days = len(tape)

def simulate_governor_path(resampled_tape, mode="Static", params=None):
    realized_pnl = 0.0
    open_longs = {}
    open_donch = {}
    pending_short = []
    
    synthetic_marks = {sym: 100.0 for sym in ALL_SYMBOLS}
    high_water = INITIAL_NAV
    governor_state = "B"
    state_dwell_counter = 0

    equity_curve = []

    if mode == "G3":
        d1_trig = params["d1_trig"]
        d2_trig = params["d2_trig"]
        d3_trig = params["d3_trig"]
        d1_mult = params["d1_mult"]
        d2_mult = params["d2_mult"]
        d3_mult = params["d3_mult"]
        dwell = params["dwell"]
        recovery_hysteresis = params["recovery_hysteresis"]
    elif mode == "G2":
        dwell = 3
        recovery_hysteresis = 0.005

    for day in resampled_tape:
        macro_bull = day["macro_bull"]
        spy_vol = day["spy_vol"]
        asset_tape = day["asset_tape"]

        previous_marks = synthetic_marks.copy()
        for sym in ALL_SYMBOLS:
            rec = asset_tape.get(sym, {"close_rel": 1.0})
            synthetic_marks[sym] = previous_marks[sym] * rec["close_rel"]

        # Short Orders
        if not macro_bull and len(pending_short) > 0 and len(open_longs) == 0:
            for order in pending_short:
                sym = order['sym']
                rec = asset_tape.get(sym)
                if not rec: continue
                prev_mark = previous_marks[sym]
                syn_high = prev_mark * rec['high_rel']
                syn_low = prev_mark * rec['low_rel']
                syn_open = prev_mark * rec['open_rel']
                syn_close = prev_mark * rec['close_rel']
                
                limit_px = prev_mark * order['target_rel']
                if syn_high >= limit_px:
                    entry_px = max(syn_open, limit_px) - SLIP
                    risk_prs = 0.75 * prev_mark * order['atr_pct']
                    initial_sl = entry_px + risk_prs
                    target_px = entry_px - (1.5 * risk_prs)
                    if syn_high >= initial_sl:
                        exit_px = initial_sl + SLIP
                    elif syn_low <= target_px:
                        exit_px = target_px
                    else:
                        exit_px = syn_close
                    
                    cur_eq = INITIAL_NAV + realized_pnl
                    qty = int((cur_eq * order['short_risk']) / risk_prs)
                    if qty > 0:
                        pnl = (entry_px - exit_px) * qty
                        realized_pnl += pnl
        pending_short = []

        # XLK Longs
        for sym in list(open_longs.keys()):
            pos = open_longs[sym]
            rec = asset_tape.get(sym)
            if not rec: continue
            prev_mark = previous_marks[sym]
            syn_open = prev_mark * rec['open_rel']
            syn_low = prev_mark * rec['low_rel']
            syn_high = prev_mark * rec['high_rel']
            syn_close = prev_mark * rec['close_rel']
            syn_atr = prev_mark * rec['atr_pct']
            syn_ema10 = prev_mark * rec['ema10_rel']
            
            pos['mark_px'] = syn_close
            if syn_low <= pos['virtual_sl']:
                exit_px = min(syn_open, pos['virtual_sl']) - SLIP
                pnl = (exit_px - pos['entry_px']) * pos['qty']
                realized_pnl += pnl
                del open_longs[sym]
                continue

            mfe_r = (syn_high - pos['entry_px']) / pos['risk_prs']
            if mfe_r >= 1.5 and pos['tier'] < 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                pos['tier'] = 1
            if pos['tier'] >= 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], syn_ema10 - (0.5 * syn_atr))

        # Donchian Longs
        for sym in list(open_donch.keys()):
            pos = open_donch[sym]
            rec = asset_tape.get(sym)
            if not rec: continue
            prev_mark = previous_marks[sym]
            syn_open = prev_mark * rec['open_rel']
            syn_low = prev_mark * rec['low_rel']
            syn_high = prev_mark * rec['high_rel']
            syn_close = prev_mark * rec['close_rel']
            syn_atr = prev_mark * rec['atr_pct']
            syn_sma50 = prev_mark * rec['sma50_rel']
            
            pos['mark_px'] = syn_close
            if syn_low <= pos['virtual_sl']:
                exit_px = min(syn_open, pos['virtual_sl']) - SLIP
                pnl = (exit_px - pos['entry_px']) * pos['qty']
                realized_pnl += pnl
                del open_donch[sym]
                continue

            mfe_r = (syn_high - pos['entry_px']) / pos['risk_prs']
            if mfe_r >= 1.5 and pos['tier'] < 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                pos['tier'] = 1
            if pos['tier'] >= 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], syn_sma50 - (0.5 * syn_atr))

        # Equity & Governor State Machine
        unrealized_longs = sum((p['mark_px'] - p['entry_px']) * p['qty'] for p in open_longs.values())
        unrealized_donch = sum((p['mark_px'] - p['entry_px']) * p['qty'] for p in open_donch.values())
        current_eq = INITIAL_NAV + realized_pnl + unrealized_longs + unrealized_donch
        high_water = max(high_water, current_eq)
        dd_pct = (high_water - current_eq) / high_water

        stress_confirm = (spy_vol > 0.25) and (not macro_bull)
        old_state = governor_state
        state_dwell_counter += 1

        if mode == "G3":
            if state_dwell_counter >= dwell:
                if governor_state == "B":
                    if dd_pct >= d3_trig: governor_state = "D3"
                    elif dd_pct >= d2_trig and stress_confirm: governor_state = "D2"
                    elif dd_pct >= d1_trig: governor_state = "D1"
                elif governor_state == "D1":
                    if dd_pct >= d3_trig: governor_state = "D3"
                    elif dd_pct >= d2_trig and stress_confirm: governor_state = "D2"
                    elif dd_pct <= (d1_trig - recovery_hysteresis): governor_state = "B"
                elif governor_state == "D2":
                    if dd_pct >= d3_trig: governor_state = "D3"
                    elif dd_pct <= (d2_trig - recovery_hysteresis): governor_state = "D1"
                elif governor_state == "D3":
                    if dd_pct <= (d3_trig - recovery_hysteresis): governor_state = "D2"
            if governor_state != old_state:
                state_dwell_counter = 0

            if governor_state == "B":
                long_risk, short_risk, max_open_risk = 0.0100, 0.0050, 0.0300
                donch_mult, allow_long, allow_donch = 1.0, True, True
            elif governor_state == "D1":
                long_risk, short_risk, max_open_risk = 0.0100 * d1_mult, 0.0025, 0.0300 * d1_mult
                donch_mult, allow_long, allow_donch = d1_mult, True, True
            elif governor_state == "D2":
                long_risk, short_risk, max_open_risk = 0.0100 * d2_mult, 0.0025, 0.0300 * d2_mult
                donch_mult, allow_long, allow_donch = d2_mult, True, True
            else:
                long_risk, short_risk, max_open_risk = 0.0100 * d3_mult, 0.0025, 0.0300 * d3_mult
                donch_mult, allow_long, allow_donch = d3_mult, d3_mult > 0, d3_mult > 0
        elif mode == "G2":
            if state_dwell_counter >= 3:
                if governor_state == "B":
                    if dd_pct >= 0.10: governor_state = "D3"
                    elif dd_pct >= 0.075 and stress_confirm: governor_state = "D2"
                    elif dd_pct >= 0.05: governor_state = "D1"
                elif governor_state == "D1":
                    if dd_pct >= 0.10: governor_state = "D3"
                    elif dd_pct >= 0.075 and stress_confirm: governor_state = "D2"
                    elif current_eq >= high_water * 0.995: governor_state = "B"
                elif governor_state == "D2":
                    if dd_pct >= 0.10: governor_state = "D3"
                    elif dd_pct < 0.045: governor_state = "D1"
                elif governor_state == "D3":
                    if dd_pct < 0.070: governor_state = "D2"
            if governor_state != old_state:
                state_dwell_counter = 0

            if governor_state == "B":
                long_risk, short_risk, max_open_risk = 0.0100, 0.0050, 0.0300
                donch_mult, allow_long, allow_donch = 1.0, True, True
            elif governor_state == "D1":
                long_risk, short_risk, max_open_risk = 0.0075, 0.0025, 0.0225
                donch_mult, allow_long, allow_donch = 0.50, True, True
            elif governor_state == "D2":
                long_risk, short_risk, max_open_risk = 0.00375, 0.0025, 0.0150
                donch_mult, allow_donch = 0.25, True
                allow_long = True
            else:
                long_risk, short_risk, max_open_risk = 0.0, 0.0025, 0.0050
                donch_mult, allow_long, allow_donch = 0.0, False, False
        else: # Classic B/D25 Benchmark
            if governor_state == "B" and dd_pct >= 0.05:
                governor_state = "D1"
            elif governor_state == "D1" and current_eq >= high_water * 0.995:
                governor_state = "B"
            
            if governor_state == "B":
                long_risk, short_risk, max_open_risk = 0.0100, 0.0050, 0.0300
                donch_mult, allow_long, allow_donch = 1.0, True, True
            else:
                long_risk, short_risk, max_open_risk = 0.0075, 0.0025, 0.0225
                donch_mult, allow_long, allow_donch = 0.50, True, True

        # Entries
        active_symbols = set(open_longs.keys())
        if macro_bull and allow_long and len(open_longs) < MAX_POSITIONS:
            current_open_risk = sum(p['qty'] * max(0.0, p['entry_px'] - p['virtual_sl']) for p in open_longs.values())
            rem_risk_dollars = (current_eq * max_open_risk) - current_open_risk
            if rem_risk_dollars > 0:
                for sig in day["long_signals"]:
                    if len(open_longs) >= MAX_POSITIONS: break
                    sym = sig["sym"]
                    if sym in active_symbols: continue
                    prev_mark = previous_marks[sym]
                    entry_px = prev_mark * sig["entry_rel"]
                    atr = prev_mark * sig["atr_pct"]
                    initial_sl = entry_px - (2.0 * atr)
                    rps = max(entry_px - initial_sl, 0.01)
                    
                    rec_sig = asset_tape[sym]
                    dyn_risk = (
                        long_risk
                        * min(max(rec_sig["rvol"] / 1.5, 0.8), 1.6)
                        * min(max(rec_sig["adx"] / 25.0, 0.8), 1.5)
                    )
                    qty = int(min(current_eq * dyn_risk, rem_risk_dollars) / rps)
                    if qty > 0:
                        open_longs[sym] = {
                            "entry_px": entry_px, "mark_px": entry_px,
                            "qty": qty, "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0
                        }
                        active_symbols.add(sym)
                        rem_risk_dollars -= (qty * rps)

        active_donch_risk = 0.0025 * donch_mult
        if allow_donch and active_donch_risk > 0 and len(open_donch) < MAX_POSITIONS and macro_bull:
            for sig in day["donch_signals"]:
                sym1 = sig["sym"]
                if sym1 in open_donch: continue
                max_corr = 0.0
                for held_sym in open_donch:
                    c = day["donch_corrs"].get(f"{sym1}|{held_sym}", 0.0)
                    max_corr = max(max_corr, c)
                if max_corr >= 0.80: continue
                
                prev_mark = previous_marks[sym1]
                entry_px = prev_mark * sig["entry_rel"]
                atr = prev_mark * sig["atr_pct"]
                initial_sl = entry_px - (2.00 * atr)
                rps = max(entry_px - initial_sl, 0.01)
                qty = int((current_eq * active_donch_risk) / rps)
                if qty > 0:
                    open_donch[sym1] = {
                        "entry_px": entry_px, "mark_px": entry_px,
                        "qty": qty, "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0
                    }
                    break

        if not macro_bull and len(open_longs) == 0:
            for sig in day["short_signals"]:
                sym = sig["sym"]
                pending_short.append({
                    "sym": sym, "target_rel": sig["target_rel"],
                    "atr_pct": sig["atr_pct"], "short_risk": short_risk
                })

        equity_curve.append(current_eq)

    return np.array(equity_curve)

n_sims = 5000
block_size = 20
n_blocks = int(np.ceil(n_days / block_size))

np.random.seed(42)
shared_starts = [np.random.randint(0, n_days - block_size + 1, size=n_blocks) for _ in range(n_sims)]

finalists = {
    "Exact G2": {"mode": "G2", "params": None},
    "G3-A3": {"mode": "G3", "params": {"d1_trig": 0.055, "d2_trig": 0.080, "d3_trig": 0.110, "d1_mult": 0.80, "d2_mult": 0.45, "d3_mult": 0.10, "dwell": 1, "recovery_hysteresis": 0.03}},
    "G3-B5": {"mode": "G3", "params": {"d1_trig": 0.050, "d2_trig": 0.085, "d3_trig": 0.125, "d1_mult": 0.85, "d2_mult": 0.50, "d3_mult": 0.15, "dwell": 2, "recovery_hysteresis": 0.025}},
    "G3-B1": {"mode": "G3", "params": {"d1_trig": 0.060, "d2_trig": 0.090, "d3_trig": 0.130, "d1_mult": 0.85, "d2_mult": 0.55, "d3_mult": 0.20, "dwell": 2, "recovery_hysteresis": 0.03}}
}

stat_cagrs, stat_dds, stat_terms = [], [], []
fin_metrics = {k: {"cagrs": [], "dds": [], "terms": []} for k in finalists}

print(f"\n🚀 Running Paired 5,000-Path Certification for Finalists vs Benchmark...")

for i, starts in enumerate(shared_starts):
    resampled_tape = []
    for s in starts:
        resampled_tape.extend(tape[s:s + block_size])
    resampled_tape = resampled_tape[:n_days]

    eq_s = simulate_governor_path(resampled_tape, mode="Static")
    term_s = eq_s[-1]
    stat_cagrs.append((((term_s / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0)
    stat_dds.append(np.max((np.maximum.accumulate(eq_s) - eq_s) / np.maximum.accumulate(eq_s)) * 100.0)
    stat_terms.append(term_s)

    for name, cfg in finalists.items():
        eq_f = simulate_governor_path(resampled_tape, mode=cfg["mode"], params=cfg["params"])
        term_f = eq_f[-1]
        fin_metrics[name]["cagrs"].append((((term_f / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0)
        fin_metrics[name]["dds"].append(np.max((np.maximum.accumulate(eq_f) - eq_f) / np.maximum.accumulate(eq_f)) * 100.0)
        fin_metrics[name]["terms"].append(term_f)

print("\n" + "=" * 135)
print(" 🚀 PHASE 11B PAIRED FINALIST CERTIFICATION RESULTS (5,000 SIMS)")
print("=" * 135)
print(f"{'Metric':<25} | {'Classic B/D25':<16} | {'Exact G2':<16} | {'G3-A3':<16} | {'G3-B5':<16} | {'G3-B1':<16}")
print("-" * 135)
print(f"{'Median CAGR':<25} | {np.median(stat_cagrs):>14.2f}% | {np.median(fin_metrics['Exact G2']['cagrs']):>14.2f}% | {np.median(fin_metrics['G3-A3']['cagrs']):>14.2f}% | {np.median(fin_metrics['G3-B5']['cagrs']):>14.2f}% | {np.median(fin_metrics['G3-B1']['cagrs']):>14.2f}%")
print(f"{'5th Percentile CAGR':<25} | {np.percentile(stat_cagrs, 5):>14.2f}% | {np.percentile(fin_metrics['Exact G2']['cagrs'], 5):>14.2f}% | {np.percentile(fin_metrics['G3-A3']['cagrs'], 5):>14.2f}% | {np.percentile(fin_metrics['G3-B5']['cagrs'], 5):>14.2f}% | {np.percentile(fin_metrics['G3-B1']['cagrs'], 5):>14.2f}%")
print(f"{'Median Max Drawdown':<25} | {np.median(stat_dds):>14.2f}% | {np.median(fin_metrics['Exact G2']['dds']):>14.2f}% | {np.median(fin_metrics['G3-A3']['dds']):>14.2f}% | {np.median(fin_metrics['G3-B5']['dds']):>14.2f}% | {np.median(fin_metrics['G3-B1']['dds']):>14.2f}%")
print(f"{'95th Percentile Max DD':<25} | {np.percentile(stat_dds, 95):>14.2f}% | {np.percentile(fin_metrics['Exact G2']['dds'], 95):>14.2f}% | {np.percentile(fin_metrics['G3-A3']['dds'], 95):>14.2f}% | {np.percentile(fin_metrics['G3-B5']['dds'], 95):>14.2f}% | {np.percentile(fin_metrics['G3-B1']['dds'], 95):>14.2f}%")
print(f"{'99th Percentile Max DD':<25} | {np.percentile(stat_dds, 99):>14.2f}% | {np.percentile(fin_metrics['Exact G2']['dds'], 99):>14.2f}% | {np.percentile(fin_metrics['G3-A3']['dds'], 99):>14.2f}% | {np.percentile(fin_metrics['G3-B5']['dds'], 99):>14.2f}% | {np.percentile(fin_metrics['G3-B1']['dds'], 99):>14.2f}%")
print(f"{'Positive Terminal Prob':<25} | {(sum(np.array(stat_terms)>INITIAL_NAV)/n_sims)*100:>13.1f}% | {(sum(np.array(fin_metrics['Exact G2']['terms'])>INITIAL_NAV)/n_sims)*100:>13.1f}% | {(sum(np.array(fin_metrics['G3-A3']['terms'])>INITIAL_NAV)/n_sims)*100:>13.1f}% | {(sum(np.array(fin_metrics['G3-B5']['terms'])>INITIAL_NAV)/n_sims)*100:>13.1f}% | {(sum(np.array(fin_metrics['G3-B1']['terms'])>INITIAL_NAV)/n_sims)*100:>13.1f}%")
print(f"{'Worst Path Ending NAV':<25} | ${min(stat_terms):>12,.0f} | ${min(fin_metrics['Exact G2']['terms']):>12,.0f} | ${min(fin_metrics['G3-A3']['terms']):>12,.0f} | ${min(fin_metrics['G3-B5']['terms']):>12,.0f} | ${min(fin_metrics['G3-B1']['terms']):>12,.0f}")
print("=" * 135)
