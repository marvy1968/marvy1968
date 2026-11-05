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

print("=" * 145)
print(" 🔬 PHASE D3: RECONCILED DETERMINISTIC CORRELATION MATRIX")
print("=" * 145)

master_daily = {}
# Enforce deterministic preservation of EXPERT_UNIVERSE order
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

def run_d3_simulation(max_positions=3, corr_cap=1.0, use_d2_filter=False):
    realized_pnl = 0.0
    open_longs = {}
    closed_trades = []
    daily_records = []

    for curr_date in dates:
        macro_bull = spy_macro_map.get(curr_date, False)

        for sym in list(open_longs.keys()):
            pos = open_longs[sym]
            df_d = master_daily.get(sym)
            if df_d is None or curr_date not in df_d.index: continue
            row = df_d.loc[curr_date]
            atr_d = row['atr'] if pd.notna(row['atr']) and row['atr'] > 0 else pos['initial_atr']

            # 1. Pending next-open D2 time exits (7-day / 0.25R failed breakout)
            if use_d2_filter and pos.get("pending_time_exit", False) and curr_date > pos["time_exit_signal_date"]:
                exit_px = row["open"] - SLIP
                raw_r = (exit_px - pos["entry_px"]) / pos["risk_prs"]
                pnl = (exit_px - pos["entry_px"]) * pos["qty"]
                realized_pnl += pnl
                closed_trades.append({"symbol": sym, "raw_r": raw_r, "dollar_pnl": pnl, "exit_reason": "TIME_STOP"})
                del open_longs[sym]
                continue

            # 2. Regular stop loss
            if row['low'] <= pos['virtual_sl']:
                exit_px = min(row['open'], pos['virtual_sl']) - SLIP
                raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
                pnl = (exit_px - pos['entry_px']) * pos['qty']
                realized_pnl += pnl
                closed_trades.append({"symbol": sym, "raw_r": raw_r, "dollar_pnl": pnl, "exit_reason": "STOP_LOSS"})
                del open_longs[sym]
                continue

            pos['days_held'] += 1
            pos['peak_price'] = max(pos['peak_price'], row['high'])
            mfe_r = (pos['peak_price'] - pos['entry_px']) / pos['risk_prs']

            # Breakeven Ratchet
            if mfe_r >= 1.50 and pos['tier'] < 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                pos['tier'] = 1
            if pos['tier'] >= 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], row['sma50'] - (0.5 * atr_d))

            # D2 7-day / 0.25R trigger evaluation at close
            if use_d2_filter and not pos.get("pending_time_exit", False):
                if pos['days_held'] >= 7 and mfe_r < 0.25 and row['close'] < pos['entry_px']:
                    pos["pending_time_exit"] = True
                    pos["time_exit_signal_date"] = curr_date

        unrealized = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_longs.items() if curr_date in master_daily[s].index)
        current_eq = INITIAL_NAV + realized_pnl + unrealized
        daily_records.append({"date": curr_date, "equity": current_eq})

        if macro_bull and len(open_longs) < max_positions:
            for sym in EXPERT_UNIVERSE: # Deterministic iteration order
                if sym in open_longs: continue
                df_d = master_daily.get(sym)
                if df_d is None or curr_date not in df_d.index: continue
                idx = df_d.index.get_loc(curr_date)
                if idx < 200: continue
                row = df_d.iloc[idx]
                atr = row['atr']
                if pd.isna(atr) or atr <= 0: continue

                breakout_lvl = row['donchian_55']
                if row['close'] > breakout_lvl:
                    # Correlation Check against currently held positions
                    max_corr = 0.0
                    for held_sym in open_longs:
                        c = get_rolling_corr(sym, held_sym, curr_date, window=60)
                        max_corr = max(max_corr, c)

                    if max_corr >= corr_cap and corr_cap < 1.0:
                        continue # Reject candidate due to high correlation

                    entry_px = row['close'] + SLIP + SPREAD
                    initial_sl = entry_px - (2.00 * atr)
                    rps = max(entry_px - initial_sl, 0.01)
                    qty = int((current_eq * 0.01) / rps)
                    if qty > 0:
                        open_longs[sym] = {
                            "entry_date": curr_date, "entry_px": entry_px, "qty": qty,
                            "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "initial_atr": atr,
                            "days_held": 0, "peak_price": entry_px, "breakout_level": breakout_lvl,
                            "pending_time_exit": False, "time_exit_signal_date": None
                        }
                        if len(open_longs) >= max_positions: break

    # Final Liquidation
    for sym, pos in list(open_longs.items()):
        df_d = master_daily[sym]
        window_df = df_d.loc[(df_d.index >= start_dt) & (df_d.index <= end_dt)]
        if window_df.empty: continue
        last_row = window_df.iloc[-1]
        exit_px = last_row['close'] - SLIP
        raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
        pnl = (exit_px - pos['entry_px']) * pos['qty']
        realized_pnl += pnl
        closed_trades.append({"symbol": sym, "raw_r": raw_r, "dollar_pnl": pnl, "exit_reason": "END_OF_TEST"})
        del open_longs[sym]

    eq_df = pd.DataFrame(daily_records).drop_duplicates('date', keep='last').set_index('date')
    tdf = pd.DataFrame(closed_trades)
    
    total_ret = ((eq_df['equity'].iloc[-1] - INITIAL_NAV) / INITIAL_NAV) * 100.0
    eq_peak = eq_df['equity'].cummax()
    max_dd = ((eq_peak - eq_df['equity']) / eq_peak).max() * 100.0
    years = (end_dt - start_dt).days / 365.25
    cagr = (((eq_df['equity'].iloc[-1] / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
    calmar = (cagr / max_dd) if max_dd > 0 else 0.0

    r_arr = tdf['raw_r'].values if not tdf.empty else np.array([0])
    wins = r_arr[r_arr > 0]
    losses = r_arr[r_arr <= 0]
    pf = float(np.sum(wins) / np.abs(np.sum(losses))) if len(losses) > 0 else float("inf")

    return {
        "max_pos": max_positions, "corr_cap": corr_cap, "d2": use_d2_filter,
        "cagr": cagr, "max_dd": max_dd, "calmar": calmar, "pf": pf, "trades": len(tdf), "ending_nav": eq_df['equity'].iloc[-1]
    }

MATRIX_RECONCILED = [
    (3, 1.0, False),  # Reconciled Control (Should match 15.53% CAGR / 17.86% DD)
    (3, 1.0, True),   # Control + D2 (7d / 0.25R filter)
    (3, 0.95, False),
    (3, 0.90, False),
    (3, 0.85, False), # Previous D3 Winner
    (3, 0.80, False),
    (2, 1.0, False),
    (1, 1.0, False),
]

print(f"{'MaxPos'} | {'CorrCap'} | {'D2Filter'} | {'CAGR':<7} | {'Max DD':<7} | {'Calmar'} | {'PF'} | {'Trds'} | {'Terminal NAV'}")
print("-" * 100)

for mpos, ccap, d2f in MATRIX_RECONCILED:
    res = run_d3_simulation(mpos, ccap, d2f)
    print(f"{res['max_pos']:6d} | {res['corr_cap']:7.2f} | {str(res['d2']):<8} | {res['cagr']:>5.2f}% | {res['max_dd']:>5.2f}% | {res['calmar']:>6.2f} | {res['pf']:>4.2f} | {res['trades']:>4} | ${res['ending_nav']:>12,.2f}")

print("=" * 145)
