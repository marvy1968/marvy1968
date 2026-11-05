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
print(" 🏛️ CONFIG B + D3 DONCHIAN SATELLITE INTEGRATION BACKTEST")
print("=" * 145)

master_daily = {}
all_syms = list(set(EXPERT_UNIVERSE + ["SPY", "XLK"]))

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

def run_integration_simulation(donchian_risk_pct):
    # NAV tracking per sleeve and total
    total_nav = INITIAL_NAV
    xlk_nav = INITIAL_NAV * 0.50
    donch_nav = INITIAL_NAV * 0.30
    short_nav = INITIAL_NAV * 0.20
    cash_nav = INITIAL_NAV * 0.00

    # For attribution
    xlk_pnl = 0.0
    donch_pnl = 0.0
    short_pnl = 0.0

    open_donch_longs = {}
    donch_closed_trades = []
    
    # XLK Position state (Simple Core Long: buy when SPY macro bull, sell when bear)
    xlk_position = 0 # shares
    xlk_entry_px = 0.0

    # Short Sleeve state (Stage 4: short when SPY close < SMA50)
    short_positions = {}

    daily_records = []
    gov_mode = "B"
    days_b = 0
    days_d = 0
    b_to_d_count = 0
    d_to_b_count = 0

    high_water = INITIAL_NAV

    for curr_date in dates:
        macro_bull = spy_macro_map.get(curr_date, False)
        
        # Portfolio Governor state transition
        prev_mode = gov_mode
        if macro_bull:
            gov_mode = "B"
            if prev_mode == "D": d_to_b_count += 1
            days_b += 1
        else:
            gov_mode = "D"
            if prev_mode == "B": b_to_d_count += 1
            days_d += 1

        active_donch_risk = donchian_risk_pct if gov_mode == "B" else (donchian_risk_pct * 0.5)

        # 1. Donchian Satellite Management
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
                donch_pnl += pnl
                donch_closed_trades.append({"symbol": sym, "raw_r": raw_r, "dollar_pnl": pnl})
                del open_donch_longs[sym]
                continue

            pos['days_held'] += 1
            pos['peak_price'] = max(pos['peak_price'], row['high'])
            mfe_r = (pos['peak_price'] - pos['entry_px']) / pos['risk_prs']

            if mfe_r >= 1.50 and pos['tier'] < 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                pos['tier'] = 1
            if pos['tier'] >= 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], row['sma50'] - (0.5 * atr_d))

        # Donchian Entries (if risk > 0)
        if active_donch_risk > 0 and len(open_donch_longs) < 3:
            for sym in EXPERT_UNIVERSE:
                if sym in open_donch_longs: continue
                df_d = master_daily.get(sym)
                if df_d is None or curr_date not in df_d.index: continue
                idx = df_d.index.get_loc(curr_date)
                if idx < 200: continue
                row = df_d.iloc[idx]
                atr = row['atr']
                if pd.isna(atr) or atr <= 0: continue

                if row['close'] > row['donchian_55']:
                    max_corr = 0.0
                    for held_sym in open_donch_longs:
                        c = get_rolling_corr(sym, held_sym, curr_date, window=60)
                        max_corr = max(max_corr, c)
                    if max_corr >= 0.80: continue

                    entry_px = row['close'] + SLIP + SPREAD
                    initial_sl = entry_px - (2.00 * atr)
                    rps = max(entry_px - initial_sl, 0.01)
                    qty = int((total_nav * active_donch_risk) / rps)
                    if qty > 0:
                        open_donch_longs[sym] = {
                            "entry_date": curr_date, "entry_px": entry_px, "qty": qty,
                            "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "initial_atr": atr,
                            "days_held": 0, "peak_price": entry_px
                        }
                        break

        # 2. Core XLK Long Sleeve Management
        xlk_df = master_daily["XLK"]
        if curr_date in xlk_df.index:
            xlk_row = xlk_df.loc[curr_date]
            target_xlk_alloc = 1.00 if gov_mode == "B" else 0.40 # Config B vs D allocation
            
            # Simple daily return compounding for XLK
            xlk_ret = xlk_row['returns'] if pd.notna(xlk_row['returns']) else 0.0
            xlk_sleeve_delta = (xlk_nav * target_xlk_alloc) * xlk_ret
            xlk_pnl += xlk_sleeve_delta

        # 3. Stage-4 Short Sleeve Management
        spy_row = spy_df.loc[curr_date] if curr_date in spy_df.index else None
        if spy_row is not None:
            is_stage4 = spy_row['close'] < spy_row['sma50']
            short_alloc = 0.50 if gov_mode == "B" else 0.25
            if is_stage4:
                # Inverse SPY performance when in stage 4 bear
                spy_ret = spy_row['returns'] if pd.notna(spy_row['returns']) else 0.0
                short_sleeve_delta = (short_nav * short_alloc) * (-spy_ret * 1.0)
                short_pnl += short_sleeve_delta

        # Unrealized Donchian PnL
        donch_unrealized = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_donch_longs.items() if curr_date in master_daily[s].index)
        
        total_eq = INITIAL_NAV + xlk_pnl + donch_pnl + short_pnl + donch_unrealized
        high_water = max(high_water, total_eq)
        current_dd = ((high_water - total_eq) / high_water) * 100.0

        daily_records.append({
            "date": curr_date, "equity": total_eq, "dd": current_dd,
            "xlk_pnl": xlk_pnl, "donch_pnl": donch_pnl + donch_unrealized, "short_pnl": short_pnl
        })

    # Final Liquidation for Donchian
    for sym, pos in list(open_donch_longs.items()):
        df_d = master_daily[sym]
        window_df = df_d.loc[(df_d.index >= start_dt) & (df_d.index <= end_dt)]
        if window_df.empty: continue
        last_row = window_df.iloc[-1]
        exit_px = last_row['close'] - SLIP
        raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
        pnl = (exit_px - pos['entry_px']) * pos['qty']
        donch_pnl += pnl
        donch_closed_trades.append({"symbol": sym, "raw_r": raw_r, "dollar_pnl": pnl})
        del open_donch_longs[sym]

    eq_df = pd.DataFrame(daily_records).drop_duplicates('date', keep='last').set_index('date')
    tdf = pd.DataFrame(donch_closed_trades)
    
    final_eq = eq_df['equity'].iloc[-1]
    total_ret = ((final_eq - INITIAL_NAV) / INITIAL_NAV) * 100.0
    max_dd = eq_df['dd'].max()
    years = (end_dt - start_dt).days / 365.25
    cagr = (((final_eq / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
    calmar = (cagr / max_dd) if max_dd > 0 else 0.0

    # 2020 / 2022 specific slices
    eq_2020 = eq_df.loc[datetime.date(2020, 1, 1):datetime.date(2020, 12, 31)]
    ret_2020 = ((eq_2020['equity'].iloc[-1] / eq_2020['equity'].iloc[0]) - 1.0) * 100.0 if not eq_2020.empty else 0.0
    dd_2020 = eq_2020['dd'].max() if not eq_2020.empty else 0.0

    eq_2022 = eq_df.loc[datetime.date(2022, 1, 1):datetime.date(2022, 12, 31)]
    ret_2022 = ((eq_2022['equity'].iloc[-1] / eq_2022['equity'].iloc[0]) - 1.0) * 100.0 if not eq_2022.empty else 0.0
    dd_2022 = eq_2022['dd'].max() if not eq_2022.empty else 0.0

    r_arr = tdf['raw_r'].values if not tdf.empty else np.array([0])
    wins = r_arr[r_arr > 0]
    losses = r_arr[r_arr <= 0]
    pf = float(np.sum(wins) / np.abs(np.sum(losses))) if len(losses) > 0 else float("inf")
    exp_r = float(np.mean(r_arr)) if len(r_arr) > 0 else 0.0

    return {
        "risk": donchian_risk_pct, "cagr": cagr, "tot_ret": total_ret, "max_dd": max_dd,
        "calmar": calmar, "pf": pf, "exp_r": exp_r, "ret_2020": ret_2020, "dd_2020": dd_2020,
        "ret_2022": ret_2022, "dd_2022": dd_2022, "days_b": days_b, "days_d": days_d,
        "b_d_trans": b_to_d_count, "d_b_trans": d_to_b_count, "final_nav": final_eq,
        "xlk_contrib": xlk_pnl, "donch_contrib": donch_pnl, "short_contrib": short_pnl
    }

CONFIGS = [
    (0.00, "B0 Control (Off)"),
    (0.0025, "B + D25 (0.25%)"),
    (0.0050, "B + D50 (0.50%)"),
    (0.0075, "B + D75 (0.75%)"),
    (0.0100, "B + D100 (1.00%)"),
]

print(f"{'Configuration':<18} | {'CAGR':<7} | {'Max DD':<7} | {'Calmar':<7} | {'PF':<4} | {'2020 Ret/DD':<12} | {'2022 Ret/DD':<12} | {'Terminal NAV'}")
print("-" * 115)

for risk_val, label in CONFIGS:
    res = run_integration_simulation(risk_val)
    r20 = f"{res['ret_2020']:+.1f}%/{res['dd_2020']:.1f}%"
    r22 = f"{res['ret_2022']:+.1f}%/{res['dd_2022']:.1f}%"
    print(f"{label:<18} | {res['cagr']:>5.2f}% | {res['max_dd']:>5.2f}% | {res['calmar']:>6.2f} | {res['pf']:>4.2f} | {r20:>12} | {r22:>12} | ${res['final_nav']:>12,.2f}")

print("=" * 145)
