import os, time, datetime, json
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

STATE_FILE = "/home/moonrider1968/bots/governor_state.json"

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


def get_rolling_corr(sym1, sym2, curr_date, window=60):
    df1 = master_daily.get(sym1)
    df2 = master_daily.get(sym2)
    if df1 is None or df2 is None: return 0.0
    s1 = df1.loc[:curr_date, 'close'].pct_change().dropna().tail(window)
    s2 = df2.loc[:curr_date, 'close'].pct_change().dropna().tail(window)
    common = pd.concat([s1, s2], axis=1).dropna()
    if len(common) < 20: return 0.0
    return float(common.corr().iloc[0, 1])

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
print(" 🚀 ADAPTIVE B → D RISK GOVERNOR BACKTEST & DAEMON PERSISTENCE ENGINE")
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

for _sym in DONCHIAN_SATELLITE_UNIVERSE:
    _df = master_daily.get(_sym)
    if _df is not None:
        _df["donchian_55_d3"] = (
            _df["high"].rolling(55).max().shift(1)
        )

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

def run_adaptive_simulation(donchian_risk_pct=0.0):
    realized_pnl = 0.0
    open_donch_longs = {}
    donch_pnl_total = 0.0
    peak_concurrent_donch = 0
    open_longs = {}
    closed_trades = []
    daily_records = []
    pending_short_orders = []
    
    # Governor State Tracking
    high_water = INITIAL_NAV
    active_config = "B"
    transition_log = []

    for curr_date in dates:
        macro_bull = spy_macro_map.get(curr_date, False)

        # Active Risk Parameters based on Governor State
        if active_config == "B":
            long_risk, short_risk, max_open_risk = 0.0100, 0.0050, 0.0300
        else: # Config D
            long_risk, short_risk, max_open_risk = 0.0075, 0.0025, 0.0225

        # 1. Execute Pending Next-Day Stage-4 Short Orders
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

                    current_eq = INITIAL_NAV + realized_pnl + sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_longs.items() if curr_date in master_daily[s].index)
                    qty = int((current_eq * short_risk) / risk_prs)
                    if qty > 0:
                        pnl = (entry_px - exit_px) * qty
                        realized_pnl += pnl
                        closed_trades.append({"sleeve": "STAGE4_SHORT", "symbol": sym, "entry_date": curr_date, "raw_r": raw_r, "dollar_pnl": pnl})

        pending_short_orders = []

        # 2. Manage Active Multi-Day Long Positions
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
                closed_trades.append({"sleeve": "XLK_LONG", "symbol": sym, "entry_date": curr_date, "raw_r": raw_r, "dollar_pnl": pnl})
                del open_longs[sym]
                continue
            mfe_r = (row['high'] - pos['entry_px']) / pos['risk_prs']
            if mfe_r >= 1.5 and pos['tier'] < 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                pos['tier'] = 1
            if pos['tier'] >= 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], row['ema10'] - (0.5 * atr_d))

        # 2.5 Manage Active Donchian Satellite Positions
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
                    donch_pnl_total += pnl
                    closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": pos.get("entry_date", curr_date), "exit_date": curr_date, "days_held": (curr_date - pos.get("entry_date", curr_date)).days, "raw_r": raw_r, "dollar_pnl": pnl, "entry_px": pos["entry_px"], "risk_prs": pos["risk_prs"], "exit_px": exit_px})
                    del open_donch_longs[sym]
                    continue
                mfe_r = (row['high'] - pos['entry_px']) / pos['risk_prs']
                if mfe_r >= 1.5 and pos['tier'] < 1:
                    pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                    pos['tier'] = 1
                if pos['tier'] >= 1:
                    pos['virtual_sl'] = max(pos['virtual_sl'], row['sma50'] - (0.5 * atr_d))
            peak_concurrent_donch = max(peak_concurrent_donch, len(open_donch_longs))

        # 3. Daily Portfolio Accounting & Governor Transition Logic
        unrealized_longs = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_longs.items() if curr_date in master_daily[s].index)
        donch_unrealized = 0.0
        if donchian_risk_pct > 0.0:
            donch_unrealized = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_donch_longs.items() if curr_date in master_daily[s].index)
        current_eq = INITIAL_NAV + realized_pnl + unrealized_longs + donch_unrealized
        
        # Update High-Water Mark (never reset downward)
        prev_hwm = high_water
        high_water = max(high_water, current_eq)
        drawdown = (high_water - current_eq) / high_water

        # Governor State Machine with Hysteresis
        if active_config == "B" and drawdown >= 0.05:
            active_config = "D"
            transition_log.append({
                "date": str(curr_date), "from": "B", "to": "D",
                "equity": current_eq, "hwm": high_water, "drawdown": drawdown,
                "reason": f"Drawdown reached {drawdown*100:.2f}% (>= 5.0%)"
            })
        elif active_config == "D" and current_eq >= high_water * 0.995:
            active_config = "B"
            transition_log.append({
                "date": str(curr_date), "from": "D", "to": "B",
                "equity": current_eq, "hwm": high_water, "drawdown": drawdown,
                "reason": f"Equity recovered to {current_eq/high_water*100:.2f}% of HWM (>= 99.5%)"
            })

        # Persist State to Disk (Simulation Checkpoint)
        state_payload = {
            "timestamp": str(datetime.datetime.now(NY_TZ)),
            "date": str(curr_date),
            "active_config": active_config,
            "current_equity": current_eq,
            "high_water_mark": high_water,
            "current_drawdown": drawdown,
            "transitions_count": len(transition_log)
        }
        with open(STATE_FILE, "w") as f:
            json.dump(state_payload, f, indent=2)

        # 4. Long Entries: XLK Long Growth Swings (Macro Bull Only)
        active_symbols = set(open_longs.keys())
        if macro_bull and len(open_longs) < MAX_POSITIONS:
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

        # 4.5 Donchian Satellite Entries (Macro Bull Only)
        active_donch_risk = donchian_risk_pct if active_config == "B" else (donchian_risk_pct * 0.50)
        if donchian_risk_pct > 0.0 and active_donch_risk > 0 and len(open_donch_longs) < MAX_POSITIONS and macro_bull:
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
                            "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "initial_atr": atr,
                            "days_held": 0, "peak_price": entry_px
                        }
                        break

        # 5. Queue Stage-4 Short Signals for Next-Day Limit Entry (Macro Bear Only)
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

        daily_records.append({"date": curr_date, "equity": current_eq, "config": active_config})

    # Final Liquidation of open Donchian positions
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
            donch_pnl_total += pnl
            closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": pos.get("entry_date", end_dt), "exit_date": end_dt, "days_held": (end_dt - pos.get("entry_date", end_dt)).days, "raw_r": raw_r, "dollar_pnl": pnl, "entry_px": pos["entry_px"], "risk_prs": pos["risk_prs"], "exit_px": exit_px})
            del open_donch_longs[sym]

    eq_df = pd.DataFrame(daily_records).drop_duplicates('date', keep='last').set_index('date')
    return eq_df, pd.DataFrame(closed_trades), transition_log




print("\n" + "=" * 135)
print(" 🔬 SPLIT-ADJUSTED RECONCILIATION & FRONTIER SWEEP")
print("=" * 135)

# 1. NVDA Sanity Check Around July 20, 2021 Split
print("--- NVDA SPLIT SANITY CHECK (July 16 - 21, 2021) ---")
if "NVDA" in master_daily:
    nvda_df = master_daily["NVDA"]
    sub_nvda = nvda_df.loc[datetime.date(2021, 7, 16):datetime.date(2021, 7, 21), ['open', 'high', 'low', 'close', 'volume']]
    print(sub_nvda)
print("=" * 135)

# 2. Run SPLIT B0 and D25
print("\n--- RUNNING SPLIT B0 & D25 ---")
eq_b0_s, tdf_b0_s, _ = run_adaptive_simulation(0.00)
eq_d25_s, tdf_d25_s, _ = run_adaptive_simulation(0.0025)

years = (end_dt - start_dt).days / 365.25

b0_end = eq_b0_s['equity'].iloc[-1]
b0_cagr = (((b0_end / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
b0_dd = ((eq_b0_s['equity'].cummax() - eq_b0_s['equity']) / eq_b0_s['equity'].cummax()).max() * 100.0

d25_end = eq_d25_s['equity'].iloc[-1]
d25_cagr = (((d25_end / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
d25_dd = ((eq_d25_s['equity'].cummax() - eq_d25_s['equity']) / eq_d25_s['equity'].cummax()).max() * 100.0

print(f"SPLIT B0  -> Ending Equity: ${b0_end:,.2f} | CAGR: {b0_cagr:.2f}% | Max DD: {b0_dd:.2f}%")
print(f"SPLIT D25 -> Ending Equity: ${d25_end:,.2f} | CAGR: {d25_cagr:.2f}% | Max DD: {d25_dd:.2f}%")

# 3. Check Extreme R Outliers for D25
print("\n--- EXTREME R AUDIT (Top 10 Smallest R Multiples in SPLIT D25) ---")
donch_s = tdf_d25_s[tdf_d25_s['sleeve'] == 'DONCHIAN_SATELLITE'].copy()
if not donch_s.empty:
    print(donch_s.nsmallest(10, 'raw_r')[['symbol', 'entry_date', 'exit_date', 'raw_r', 'dollar_pnl']])
print("=" * 135)

# 4. Paired-Path Efficient Frontier Sweep (D10 - D35, 5,000 sims)
print("\n" + "=" * 135)
print(" 🚀 SPLIT-ADJUSTED PAIRED-PATH EFFICIENT FRONTIER SWEEP (D10 - D35, 5,000 SIMS)")
print("=" * 135)

RISK_LEVELS = [
    (0.0010, "SPLIT + D10 (0.10%)"),
    (0.0015, "SPLIT + D15 (0.15%)"),
    (0.0020, "SPLIT + D20 (0.20%)"),
    (0.0025, "SPLIT + D25 (0.25%)"),
    (0.0030, "SPLIT + D30 (0.30%)"),
    (0.0035, "SPLIT + D35 (0.35%)"),
]

n_sims = 5000
block_size = 20

print(f"{'Config':<20} | {'CAGR':<6} | {'HistDD':<6} | {'Calmar':<6} | {'PF':<4} | {'MedCAGR':<7} | {'5thCAGR':<7} | {'MedDD':<6} | {'95thDD':<6} | {'99thDD':<6} | {'P(DD>10%)':<9}")
print("-" * 145)

base_ret = eq_d25_s['equity'].pct_change().dropna().values
n_days = len(base_ret)
n_blocks = int(np.ceil(n_days / block_size))

np.random.seed(42)
shared_starts = [np.random.randint(0, n_days - block_size + 1, size=n_blocks) for _ in range(n_sims)]

for risk_val, label in RISK_LEVELS:
    eq_df_c, tdf_c, _ = run_adaptive_simulation(risk_val)
    ending_equity = eq_df_c['equity'].iloc[-1]
    eq_peak = eq_df_c['equity'].cummax()
    max_dd_hist = ((eq_peak - eq_df_c['equity']) / eq_peak).max() * 100.0
    cagr_hist = (((ending_equity / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
    calmar_hist = (cagr_hist / max_dd_hist) if max_dd_hist > 0 else 0.0

    r_arr_c = tdf_c['raw_r'].values if not tdf_c.empty else np.array([0])
    wins_c = r_arr_c[r_arr_c > 0]
    losses_c = r_arr_c[r_arr_c <= 0]
    pf_c = float(np.sum(wins_c) / np.abs(np.sum(losses_c))) if len(losses_c) > 0 else float("inf")

    daily_ret = eq_df_c['equity'].pct_change().dropna().values
    
    sim_cagrs = []
    sim_max_dds = []
    dd_exceed_10 = 0

    for starts in shared_starts:
        boot_ret = np.concatenate([daily_ret[s:s + block_size] for s in starts])[:n_days]
        equity = INITIAL_NAV * np.cumprod(1.0 + boot_ret)
        term_val = equity[-1]
        
        peak = np.maximum.accumulate(equity)
        dd = np.max((peak - equity) / peak)
        max_dd_pct = dd * 100.0

        if max_dd_pct > 10.0: dd_exceed_10 += 1
        cagr_sim = (((term_val / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0

        sim_cagrs.append(cagr_sim)
        sim_max_dds.append(max_dd_pct)

    med_cagr = np.median(sim_cagrs)
    p5_cagr = np.percentile(sim_cagrs, 5)
    med_dd = np.median(sim_max_dds)
    p95_dd = np.percentile(sim_max_dds, 95)
    p99_dd = np.percentile(sim_max_dds, 99)
    prob_dd10 = (dd_exceed_10 / n_sims) * 100.0

    print(f"{label:<20} | {cagr_hist:>5.2f}% | {max_dd_hist:>5.2f}% | {calmar_hist:>6.2f} | {pf_c:>4.2f} | {med_cagr:>6.2f}% | {p5_cagr:>6.2f}% | {med_dd:>5.2f}% | {p95_dd:>5.2f}% | {p99_dd:>5.2f}% | {prob_dd10:>8.1f}%")

print("=" * 145)
