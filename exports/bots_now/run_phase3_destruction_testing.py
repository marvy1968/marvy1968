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

XLK_LONG_UNIVERSE = ["MSFT", "NVDA", "AVGO", "AMD", "CRM"]
STAGE4_SHORT_FLEET = [
    "AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "TSLA", "AMZN", "GOOGL", "META",
    "JPM", "BAC", "WFC", "GS", "MS", "AXP",
    "CAT", "GE", "UNP", "HON", "RTX", "ETN"
]
ALL_SYMBOLS = sorted(list(set(XLK_LONG_UNIVERSE + STAGE4_SHORT_FLEET + ["SPY"])))

START_DATE = "2017-01-01"
END_DATE = "2026-08-20"
WARMUP_START = "2016-01-01"
INITIAL_NAV = 100000.0
SLIP, SPREAD = 0.03, 0.02
MAX_POSITIONS = 3

CANDIDATE_CONFIGS = [
    ("Config B (Growth)", 0.0100, 0.0050, 0.0300),
    ("Config D (Defensive)", 0.0075, 0.0025, 0.0225)
]

def get_alpaca_bars(symbol, timeframe="1Day", start_str="", end_str=""):
    headers = {"APCA-API-KEY-ID": APCA_API_KEY_ID, "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY}
    url = f"{DATA_URL}/stocks/{symbol}/bars"
    all_bars = []
    page_token = None
    while True:
        params = {"timeframe": timeframe, "start": f"{start_str}T00:00:00Z", "end": f"{end_str}T23:59:59Z", "limit": 10000, "adjustment": "raw", "feed": "sip"}
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

def compute_adx(df, n=14):
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr_val = tr.rolling(n).mean()
    up_move = h - h.shift(1)
    down_move = l.shift(1) - l
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    pos_di = 100 * (pd.Series(pos_dm, index=df.index).rolling(n).mean() / (atr_val + 1e-9))
    neg_di = 100 * (pd.Series(neg_dm, index=df.index).rolling(n).mean() / (atr_val + 1e-9))
    dx = 100 * (np.abs(pos_di - neg_di) / (pos_di + neg_di + 1e-9))
    return dx.rolling(n).mean()

print("=" * 135)
print(" 🔬 PHASE 3: DESTRUCTION TESTING SUITE (CONFIG B vs CONFIG D)")
print("=" * 135)
print("📥 Fetching master daily and weekly data...")

master_daily = {}
weekly_data = {}

for sym in ALL_SYMBOLS:
    df_d = get_alpaca_bars(sym, "1Day", start_str=WARMUP_START, end_str=END_DATE)
    if df_d is not None and len(df_d) > 60:
        df_d['ema10'] = df_d['close'].ewm(span=10, adjust=False).mean()
        df_d['ema20'] = df_d['close'].ewm(span=20, adjust=False).mean()
        df_d['ema21'] = df_d['close'].ewm(span=21, adjust=False).mean()
        df_d['sma50'] = df_d['close'].rolling(50).mean()
        df_d['donchian_high'] = df_d['high'].rolling(20).max().shift(1)
        tr = pd.concat([df_d['high'] - df_d['low'], (df_d['high'] - df_d['close'].shift(1)).abs(), (df_d['low'] - df_d['close'].shift(1)).abs()], axis=1).max(axis=1)
        df_d['atr'] = tr.rolling(14).mean()
        df_d['adx'] = compute_adx(df_d)
        df_d['vol_sma20'] = df_d['volume'].rolling(20).mean()
        df_d['rvol'] = df_d['volume'] / (df_d['vol_sma20'] + 1e-9)
        df_d['date'] = df_d['time'].dt.date
        df_d.set_index('date', inplace=True)
        master_daily[sym] = df_d

    df_w = get_alpaca_bars(sym, "1Week", start_str=WARMUP_START, end_str=END_DATE)
    if df_w is not None and len(df_w) >= 35:
        df_w['sma30w'] = df_w['close'].rolling(30).mean()
        df_w['sma30w_slope'] = df_w['sma30w'].diff(3)
        df_w['prev_20w_high'] = df_w['high'].rolling(20).max().shift(1)
        df_w['prev_20w_low'] = df_w['low'].rolling(20).min().shift(1)
        df_w['sma30w_pit'] = df_w['sma30w'].shift(1)
        df_w['sma30w_slope_pit'] = df_w['sma30w_slope'].shift(1)
        df_w['prev_20w_high_pit'] = df_w['prev_20w_high'].shift(1)
        df_w['close_pit'] = df_w['close'].shift(1)
        df_w['date'] = df_w['time'].dt.date
        weekly_data[sym] = df_w
    time.sleep(0.01)

spy_df = master_daily["SPY"]
spy_df['macro_bull'] = (spy_df['close'] > spy_df['ema20']) & (spy_df['ema20'] > spy_df['sma50'])
spy_df['macro_bull_pit'] = spy_df['macro_bull'].shift(1).fillna(False)
spy_macro_map = dict(zip(spy_df.index, spy_df['macro_bull_pit']))

def get_pit_stage2(sym, current_date):
    df_w = weekly_data.get(sym)
    if df_w is None: return False
    past_w = df_w[df_w['date'] <= current_date]
    if len(past_w) < 32: return False
    row = past_w.iloc[-1]
    if pd.isna(row['sma30w_pit']) or pd.isna(row['sma30w_slope_pit']): return False
    return (row['close_pit'] > row['sma30w_pit'] and row['sma30w_slope_pit'] > 0 and row['close_pit'] >= (0.95 * row['prev_20w_high_pit']))

def get_pit_stage4(sym, current_date):
    df_w = weekly_data.get(sym)
    if df_w is None: return False
    past_w = df_w[df_w['date'] <= current_date]
    if len(past_w) < 30: return False
    row = past_w.iloc[-1]
    if pd.isna(row['sma30w_pit']) or pd.isna(row['sma30w_slope_pit']): return False
    return (row['close_pit'] < row['sma30w_pit'] and row['sma30w_slope_pit'] < 0)

start_dt = datetime.datetime.strptime(START_DATE, "%Y-%m-%d").date()
end_dt = datetime.datetime.strptime(END_DATE, "%Y-%m-%d").date()
dates = sorted(list(set.union(*[set(master_daily[s].index) for s in ALL_SYMBOLS if s in master_daily])))
dates = [d for d in dates if start_dt <= d <= end_dt]

def run_base_simulation(long_risk, short_risk, max_open_risk, slip_mult=1.0):
    realized_pnl = 0.0
    open_longs = {}
    closed_trades = []
    daily_records = []
    pending_short_orders = []
    effective_slip = SLIP * slip_mult

    for curr_date in dates:
        macro_bull = spy_macro_map.get(curr_date, False)

        if not macro_bull and len(pending_short_orders) > 0 and len(open_longs) == 0:
            for order in pending_short_orders:
                sym = order['sym']
                df_d = master_daily.get(sym)
                if df_d is None or curr_date not in df_d.index: continue
                row = df_d.loc[curr_date]
                limit_px = order['target_entry']
                if row['high'] >= limit_px:
                    entry_px = max(row['open'], limit_px) - effective_slip
                    risk_prs = order['risk_prs']
                    initial_sl = entry_px + risk_prs
                    target_px = entry_px - (1.5 * risk_prs)
                    hit_stop = (row['high'] >= initial_sl)
                    hit_target = (row['low'] <= target_px)
                    if hit_stop:
                        exit_px = initial_sl + effective_slip
                        raw_r = -1.00
                    elif hit_target:
                        exit_px = target_px
                        raw_r = 1.50
                    else:
                        exit_px = row['close']
                        raw_r = (entry_px - exit_px) / risk_prs

                    current_eq = INITIAL_NAV + realized_pnl + sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_longs.items() if curr_date in master_daily[s].index)
                    qty = int((current_eq * short_risk) / risk_prs)
                    if qty > 0:
                        pnl = (entry_px - exit_px) * qty
                        realized_pnl += pnl
                        closed_trades.append({"sleeve": "STAGE4_SHORT", "symbol": sym, "entry_date": curr_date, "raw_r": raw_r, "dollar_pnl": pnl})

        pending_short_orders = []

        for sym in list(open_longs.keys()):
            pos = open_longs[sym]
            df_d = master_daily.get(sym)
            if df_d is None or curr_date not in df_d.index: continue
            row = df_d.loc[curr_date]
            atr_d = row['atr'] if pd.notna(row['atr']) and row['atr'] > 0 else pos['initial_atr']
            if row['low'] <= pos['virtual_sl']:
                exit_px = min(row['open'], pos['virtual_sl']) - effective_slip
                raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
                pnl = (exit_px - pos['entry_px']) * pos['qty']
                realized_pnl += pnl
                closed_trades.append({"sleeve": "XLK_LONG", "symbol": sym, "entry_date": curr_date, "raw_r": raw_r, "dollar_pnl": pnl})
                del open_longs[sym]
                continue
            mfe_r = (row['high'] - pos['entry_px']) / pos['risk_prs']
            if mfe_r >= 1.5 and pos['tier'] < 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                pos['tier'] = 1
            if pos['tier'] >= 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], row['ema10'] - (0.5 * atr_d))

        unrealized_longs = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_longs.items() if curr_date in master_daily[s].index)
        current_open_risk = sum(p['qty'] * max(0.0, p['entry_px'] - p['virtual_sl']) for p in open_longs.values())
        current_eq = INITIAL_NAV + realized_pnl + unrealized_longs

        active_symbols = set(open_longs.keys())
        if macro_bull and len(open_longs) < MAX_POSITIONS:
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
                    entry_px = row['close'] + effective_slip + SPREAD
                    initial_sl = entry_px - (2.0 * atr)
                    rps = max(entry_px - initial_sl, 0.01)
                    dyn_risk = long_risk * min(max(row['rvol']/1.5, 0.8), 1.6) * min(max(row['adx']/25.0, 0.8), 1.5)
                    qty = int(min(current_eq * dyn_risk, rem_risk_dollars) / rps)
                    if qty > 0:
                        open_longs[sym] = {"entry_date": curr_date, "entry_px": entry_px, "qty": qty, "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "initial_atr": atr}
                        active_symbols.add(sym)
                        rem_risk_dollars -= (qty * rps)

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
                    pending_short_orders.append({"sym": sym, "target_entry": row['ema20'], "risk_prs": 0.75 * atr})

        daily_records.append({"date": curr_date, "equity": current_eq})

    for sym, pos in list(open_longs.items()):
        df_d = master_daily[sym]
        window_df = df_d.loc[(df_d.index >= start_dt) & (df_d.index <= end_dt)]
        if window_df.empty: continue
        last_row = window_df.iloc[-1]
        exit_px = last_row['close'] - effective_slip
        raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
        pnl = (exit_px - pos['entry_px']) * pos['qty']
        realized_pnl += pnl
        closed_trades.append({"sleeve": "XLK_LONG", "symbol": sym, "entry_date": pos['entry_date'], "raw_r": raw_r, "dollar_pnl": pnl})
        del open_longs[sym]

    final_equity = INITIAL_NAV + realized_pnl
    daily_records.append({"date": end_dt, "equity": final_equity})
    eq_df = pd.DataFrame(daily_records).drop_duplicates('date', keep='last').set_index('date')
    return eq_df, pd.DataFrame(closed_trades)

print("\n" + "=" * 135)
print(" 🛡️ EXECUTING PHASE 3 DESTRUCTION BATTERY")
print("=" * 135)

for label, l_risk, s_risk, max_r in CANDIDATE_CONFIGS:
    print(f"\n--- Testing {label} ---")
    eq_base, tdf_base = run_base_simulation(l_risk, s_risk, max_r, slip_mult=1.0)
    
    # 1. Winner Removal (Top 1, 3, 5)
    tdf_sort = tdf_base.sort_values('dollar_pnl', ascending=False).reset_index(drop=True)
    print("• Winner Removal Test (Net PnL Impact):")
    for drop_n in [1, 3, 5]:
        sub_pnl = tdf_sort.iloc[drop_n:]['dollar_pnl'].sum()
        print(f"  - Without Top {drop_n} trades -> Ending PnL: ${sub_pnl:,.2f} (Ret: {(sub_pnl/INITIAL_NAV)*100:+.2f}%)")

    # 2. Slippage Stress (2x and 3x)
    eq_2x, _ = run_base_simulation(l_risk, s_risk, max_r, slip_mult=2.0)
    eq_3x, _ = run_base_simulation(l_risk, s_risk, max_r, slip_mult=3.0)
    print(f"• Slippage Stress Test:")
    print(f"  - 2x Slippage Ending NAV: ${eq_2x['equity'].iloc[-1]:,.2f} | Max DD: {(((eq_2x['equity'].cummax() - eq_2x['equity']) / eq_2x['equity'].cummax()).max()*100):.2f}%")
    print(f"  - 3x Slippage Ending NAV: ${eq_3x['equity'].iloc[-1]:,.2f} | Max DD: {(((eq_3x['equity'].cummax() - eq_3x['equity']) / eq_3x['equity'].cummax()).max()*100):.2f}%")

    # 3. Block Bootstrap Resampling (1,000 Iterations)
    np.random.seed(42)
    pnl_arr = tdf_base['dollar_pnl'].values
    block_size = 10
    n_blocks = len(pnl_arr) // block_size
    mc_cagr, mc_dd, mc_calmar, positive_terminals = [], [], [], 0
    
    for _ in range(1000):
        # Generate block indices
        block_starts = np.random.randint(0, len(pnl_arr) - block_size, size=n_blocks)
        boot_pnl = np.concatenate([pnl_arr[bs:bs+block_size] for bs in block_starts])
        boot_eq = INITIAL_NAV + np.cumsum(boot_pnl)
        boot_ret = (boot_eq[-1] - INITIAL_NAV) / INITIAL_NAV
        if boot_eq[-1] > INITIAL_NAV: positive_terminals += 1
        years = (datetime.date(2026, 8, 20) - datetime.date(2017, 1, 1)).days / 365.25
        boot_cagr = (((boot_eq[-1] / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
        boot_peak = np.maximum.accumulate(boot_eq)
        boot_dd = np.max((boot_peak - boot_eq) / boot_peak) * 100.0
        boot_calmar = boot_cagr / boot_dd if boot_dd > 0 else 0.0
        mc_cagr.append(boot_cagr)
        mc_dd.append(boot_dd)
        mc_calmar.append(boot_calmar)

    print(f"• Monte Carlo Block Bootstrap (1,000 Iterations):")
    print(f"  - Median Calmar Ratio        : {np.median(mc_calmar):.2f} (5th-95% CI: [{np.percentile(mc_calmar, 5):.2f}, {np.percentile(mc_calmar, 95):.2f}])")
    print(f"  - 95th-Percentile Max DD     : {np.percentile(mc_dd, 95):.2f}%")
    print(f"  - Positive Terminal Prob     : {(positive_terminals/1000)*100:.1f}%")

print("=" * 135)
