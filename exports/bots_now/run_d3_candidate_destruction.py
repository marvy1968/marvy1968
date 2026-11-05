import os, time, datetime
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

EXPERT_UNIVERSE = [
    "MSFT", "NVDA", "AVGO", "AMD", "CRM", "TSLA", "AMZN", "GOOGL", 
    "META", "AAPL", "NFLX", "SNOW", "PLTR", "DDOG", "CRWD", "NET", "PANW"
]

START_DATE = "2017-01-01"
END_DATE = "2026-08-20"
WARMUP_START = "2016-01-01"
INITIAL_NAV = 100000.0
SLIP, SPREAD = 0.03, 0.02

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

print("=" * 135)
print(" 🛡️ D3-CANDIDATE-A (0.80 CORRELATION CAP): DESTRUCTION TESTING BATTERY")
print("=" * 135)

master_daily = {}
all_syms = EXPERT_UNIVERSE + ["SPY"]

for sym in all_syms:
    df_d = get_alpaca_bars(sym, "1Day", start_str=WARMUP_START, end_str=END_DATE)
    if df_d is not None and len(df_d) > 60:
        df_d['ema20'] = df_d['close'].ewm(span=20, adjust=False).mean()
        df_d['sma50'] = df_d['close'].rolling(50).mean()
        df_d['donchian_55'] = df_d['high'].rolling(55).max().shift(1)
        tr = pd.concat([df_d['high'] - df_d['low'], (df_d['high'] - df_d['close'].shift(1)).abs(), (df_d['low'] - df_d['close'].shift(1)).abs()], axis=1).max(axis=1)
        df_d['atr'] = tr.rolling(14).mean()
        df_d['returns'] = df_d['close'].pct_change()
        df_d['date'] = df_d['time'].dt.date
        df_d.set_index('date', inplace=True)
        master_daily[sym] = df_d
    time.sleep(0.01)

spy_df = master_daily["SPY"]
spy_df['macro_bull'] = (spy_df['close'] > spy_df['ema20']) & (spy_df['ema20'] > spy_df['sma50'])
spy_df['macro_bull_pit'] = spy_df['macro_bull'].shift(1).infer_objects(copy=False).fillna(False)
spy_macro_map = dict(zip(spy_df.index, spy_df['macro_bull_pit']))

start_dt = datetime.datetime.strptime(START_DATE, "%Y-%m-%d").date()
end_dt = datetime.datetime.strptime(END_DATE, "%Y-%m-%d").date()
dates = sorted(list(set.union(*[set(master_daily[s].index) for s in EXPERT_UNIVERSE if s in master_daily])))
dates = [d for d in dates if start_dt <= d <= end_dt]

def get_rolling_corr(sym1, sym2, curr_date, window=60):
    df1 = master_daily.get(sym1)
    df2 = master_daily.get(sym2)
    if df1 is None or df2 is None: return 0.0
    s1 = df1.loc[:curr_date, 'returns'].dropna().tail(window)
    s2 = df2.loc[:curr_date, 'returns'].dropna().tail(window)
    common = pd.concat([s1, s2], axis=1).dropna()
    if len(common) < 20: return 0.0
    return float(common.corr().iloc[0, 1])

def run_simulation(corr_cap=0.80, slip_mult=1.0, shock_window=None):
    realized_pnl = 0.0
    open_longs = {}
    closed_trades = []
    daily_records = []
    effective_slip = SLIP * slip_mult

    filtered_dates = dates
    if shock_window == "2020":
        filtered_dates = [d for d in dates if datetime.date(2020, 1, 1) <= d <= datetime.date(2020, 12, 31)]
    elif shock_window == "2022":
        filtered_dates = [d for d in dates if datetime.date(2022, 1, 1) <= d <= datetime.date(2022, 12, 31)]

    window_start_nav = INITIAL_NAV

    for curr_date in filtered_dates:
        macro_bull = spy_macro_map.get(curr_date, False)

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
                closed_trades.append({"symbol": sym, "raw_r": raw_r, "dollar_pnl": pnl})
                del open_longs[sym]
                continue

            pos['days_held'] += 1
            pos['peak_price'] = max(pos['peak_price'], row['high'])
            mfe_r = (pos['peak_price'] - pos['entry_px']) / pos['risk_prs']

            if mfe_r >= 1.50 and pos['tier'] < 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                pos['tier'] = 1
            if pos['tier'] >= 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], row['sma50'] - (0.5 * atr_d))

        unrealized = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_longs.items() if curr_date in master_daily[s].index)
        current_eq = window_start_nav + realized_pnl + unrealized
        daily_records.append({"date": curr_date, "equity": current_eq})

        if macro_bull and len(open_longs) < 3:
            for sym in EXPERT_UNIVERSE:
                if sym in open_longs: continue
                df_d = master_daily.get(sym)
                if df_d is None or curr_date not in df_d.index: continue
                idx = df_d.index.get_loc(curr_date)
                if idx < 200: continue
                row = df_d.iloc[idx]
                atr = row['atr']
                if pd.isna(atr) or atr <= 0: continue

                if row['close'] > row['donchian_55']:
                    max_corr = 0.0
                    for held_sym in open_longs:
                        c = get_rolling_corr(sym, held_sym, curr_date, window=60)
                        max_corr = max(max_corr, c)

                    if max_corr >= corr_cap:
                        continue

                    entry_px = row['close'] + effective_slip + SPREAD
                    initial_sl = entry_px - (2.00 * atr)
                    rps = max(entry_px - initial_sl, 0.01)
                    qty = int((current_eq * 0.01) / rps)
                    if qty > 0:
                        open_longs[sym] = {
                            "entry_date": curr_date, "entry_px": entry_px, "qty": qty,
                            "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "initial_atr": atr,
                            "days_held": 0, "peak_price": entry_px
                        }
                        break

    if shock_window is None:
        for sym, pos in list(open_longs.items()):
            df_d = master_daily[sym]
            window_df = df_d.loc[(df_d.index >= start_dt) & (df_d.index <= end_dt)]
            if window_df.empty: continue
            last_row = window_df.iloc[-1]
            exit_px = last_row['close'] - effective_slip
            raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
            pnl = (exit_px - pos['entry_px']) * pos['qty']
            realized_pnl += pnl
            closed_trades.append({"symbol": sym, "raw_r": raw_r, "dollar_pnl": pnl})
            del open_longs[sym]

    eq_df = pd.DataFrame(daily_records).drop_duplicates('date', keep='last').set_index('date')
    tdf = pd.DataFrame(closed_trades)
    
    eq_peak = eq_df['equity'].cummax()
    max_dd = ((eq_peak - eq_df['equity']) / eq_peak).max() * 100.0
    s_start = eq_df.index[0]
    s_end = eq_df.index[-1]
    years = max((s_end - s_start).days / 365.25, 0.5)
    cagr = (((eq_df['equity'].iloc[-1] / eq_df['equity'].iloc[0]) ** (1.0 / years)) - 1.0) * 100.0
    calmar = (cagr / max_dd) if max_dd > 0 else 0.0

    return eq_df, tdf, cagr, max_dd, calmar

print("\n--- 1. Friction Stress Tests ---")
eq_b, tdf_b, c_b, dd_b, cal_b = run_simulation(corr_cap=0.80, slip_mult=1.0)
_, _, c_2x, dd_2x, _ = run_simulation(corr_cap=0.80, slip_mult=2.0)
_, _, c_3x, dd_3x, _ = run_simulation(corr_cap=0.80, slip_mult=3.0)
_, _, c_5x, dd_5x, _ = run_simulation(corr_cap=0.80, slip_mult=5.0)

print(f"• Baseline (1x Slip) : CAGR: {c_b:+.2f}% | Max DD: {dd_b:.2f}% | Calmar: {cal_b:.2f}")
print(f"• 2x Slippage        : CAGR: {c_2x:+.2f}% | Max DD: {dd_2x:.2f}%")
print(f"• 3x Slippage        : CAGR: {c_3x:+.2f}% | Max DD: {dd_3x:.2f}%")
print(f"• 5x Slippage        : CAGR: {c_5x:+.2f}% | Max DD: {dd_5x:.2f}%")

print("\n--- 2. Winner Removal Test ---")
tdf_sort = tdf_b.sort_values('dollar_pnl', ascending=False).reset_index(drop=True)
for drop_n in [1, 3, 5, 10]:
    sub_pnl = tdf_sort.iloc[drop_n:]['dollar_pnl'].sum()
    sub_nav = INITIAL_NAV + sub_pnl
    print(f"• Without Top {drop_n:2d} Trades -> Terminal NAV: ${sub_nav:,.2f} (Ret: {(sub_pnl/INITIAL_NAV)*100:+.2f}%)")

print("\n--- 3. Macro Shock Window Replay ---")
_, _, c_2020, dd_2020, _ = run_simulation(corr_cap=0.80, shock_window="2020")
_, _, c_2022, dd_2022, _ = run_simulation(corr_cap=0.80, shock_window="2022")
print(f"• 2020 COVID Shock Replay : Return: {c_2020:+.2f}% | Max DD: {dd_2020:.2f}%")
print(f"• 2022 Bear Market Replay : Return: {c_2022:+.2f}% | Max DD: {dd_2022:.2f}%")

print("\n--- 4. Monte Carlo Block Bootstrap (1,000 Iterations) ---")
np.random.seed(42)
pnl_arr = tdf_b['dollar_pnl'].values
block_size = 10
n_blocks = len(pnl_arr) // block_size
mc_calmar, positive_terminals = [], 0

for _ in range(1000):
    block_starts = np.random.randint(0, len(pnl_arr) - block_size, size=n_blocks)
    boot_pnl = np.concatenate([pnl_arr[bs:bs+block_size] for bs in block_starts])
    boot_eq = INITIAL_NAV + np.cumsum(boot_pnl)
    if boot_eq[-1] > INITIAL_NAV: positive_terminals += 1
    years = (end_dt - start_dt).days / 365.25
    boot_cagr = (((boot_eq[-1] / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
    boot_peak = np.maximum.accumulate(boot_eq)
    boot_dd = np.max((boot_peak - boot_eq) / boot_peak) * 100.0
    boot_calmar = boot_cagr / boot_dd if boot_dd > 0 else 0.0
    mc_calmar.append(boot_calmar)

print(f"• Median Calmar Ratio        : {np.median(mc_calmar):.2f} (5th-95% CI: [{np.percentile(mc_calmar, 5):.2f}, {np.percentile(mc_calmar, 95):.2f}])")
print(f"• Positive Terminal Prob     : {(positive_terminals/1000)*100:.1f}%")
print("=" * 135)
