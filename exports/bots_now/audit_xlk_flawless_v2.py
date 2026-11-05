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

XLK_UNIVERSE = ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM"]

REGIMES = [
    ("2017-01-01", "2017-12-31", "2017 Bull Expansion"),
    ("2019-01-01", "2019-12-31", "2019 Recovery Rally"),
    ("2020-04-01", "2020-12-31", "2020 Post-Crash Rebound"),
    ("2021-01-01", "2021-12-31", "2021 Bull Cycle"),
    ("2023-11-01", "2024-06-30", "2023-2024 Validation"),
    ("2024-07-01", "2026-08-20", "2024-2025 Continuation")
]

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
print(" 🔬 FLAWLESS RECONCILED XLK SWING AUDIT V2 (STRICT WINDOW BOUNDS)")
print("=" * 135)
print("📥 Fetching master historical daily & weekly data (2016-2026)...")

master_data = {}
weekly_data = {}
for sym in XLK_UNIVERSE:
    df_d = get_alpaca_bars(sym, "1Day", start_str="2016-10-01", end_str="2026-08-20")
    if df_d is not None and len(df_d) > 100:
        df_d['ema10'] = df_d['close'].ewm(span=10, adjust=False).mean()
        df_d['ema21'] = df_d['close'].ewm(span=21, adjust=False).mean()
        df_d['donchian_high'] = df_d['high'].rolling(20).max().shift(1)
        tr = pd.concat([df_d['high'] - df_d['low'], 
                        (df_d['high'] - df_d['close'].shift(1)).abs(), 
                        (df_d['low'] - df_d['close'].shift(1)).abs()], axis=1).max(axis=1)
        df_d['atr'] = tr.rolling(14).mean()
        df_d['adx'] = compute_adx(df_d)
        df_d['vol_sma20'] = df_d['volume'].rolling(20).mean()
        df_d['rvol'] = df_d['volume'] / (df_d['vol_sma20'] + 1e-9)
        df_d['date'] = df_d['time'].dt.date
        df_d.set_index('date', inplace=True)
        master_data[sym] = df_d

    df_w = get_alpaca_bars(sym, "1Week", start_str="2016-10-01", end_str="2026-08-20")
    if df_w is not None and len(df_w) >= 35:
        df_w['sma30w'] = df_w['close'].rolling(30).mean()
        df_w['sma30w_slope'] = df_w['sma30w'].diff(3)
        df_w['prev_20w_high'] = df_w['high'].rolling(20).max().shift(1)
        df_w['sma30w_pit'] = df_w['sma30w'].shift(1)
        df_w['sma30w_slope_pit'] = df_w['sma30w_slope'].shift(1)
        df_w['prev_20w_high_pit'] = df_w['prev_20w_high'].shift(1)
        df_w['close_pit'] = df_w['close'].shift(1)
        df_w['date'] = df_w['time'].dt.date
        weekly_data[sym] = df_w
    time.sleep(0.01)

spy_master = get_alpaca_bars("SPY", "1Day", start_str="2016-10-01", end_str="2026-08-20")
spy_master['ema20'] = spy_master['close'].ewm(span=20, adjust=False).mean()
spy_master['sma50'] = spy_master['close'].rolling(50).mean()
spy_master['macro_bull'] = (spy_master['close'] > spy_master['ema20']) & (spy_master['ema20'] > spy_master['sma50'])
spy_master['macro_bull_pit'] = spy_master['macro_bull'].shift(1).fillna(False)
spy_master['date'] = spy_master['time'].dt.date
spy_macro_map = dict(zip(spy_master['date'], spy_master['macro_bull_pit']))

def get_pit_stage2(sym, current_date):
    df_w = weekly_data.get(sym)
    if df_w is None: return False
    past_w = df_w[df_w['date'] <= current_date]
    if len(past_w) < 32: return False
    row = past_w.iloc[-1]
    if pd.isna(row['sma30w_pit']) or pd.isna(row['sma30w_slope_pit']): return False
    return (row['close_pit'] > row['sma30w_pit'] and 
            row['sma30w_slope_pit'] > 0 and 
            row['close_pit'] >= (0.95 * row['prev_20w_high_pit']))

def run_backtest(start_date, end_date, base_risk=0.012, exclude_symbols=None, exclude_top_trades=0):
    if exclude_symbols is None: exclude_symbols = []
    universe = [s for s in XLK_UNIVERSE if s not in exclude_symbols]
    
    start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()

    dates = sorted(list(set.union(*[set(master_data[s].index) for s in universe if s in master_data])))
    dates = [d for d in dates if start_dt <= d <= end_dt]
    
    if not dates: return None

    nav = INITIAL_NAV
    realized_pnl = 0.0
    open_pos = {}
    trades = []
    eq_curve = []

    for curr_date in dates:
        macro_bull = spy_macro_map.get(curr_date, False)

        for sym in list(open_pos.keys()):
            pos = open_pos[sym]
            df_d = master_data.get(sym)
            if df_d is None or curr_date not in df_d.index: continue
            row = df_d.loc[curr_date]
            pos['max_fav'] = max(pos['max_fav'], row['high'])
            pos['max_adv'] = min(pos['max_adv'], row['low'])
            atr_d = row['atr'] if pd.notna(row['atr']) and row['atr'] > 0 else pos['initial_atr']

            if row['low'] <= pos['virtual_sl']:
                exit_px = min(row['open'], pos['virtual_sl']) - SLIP
                raw_r = (exit_px - pos['entry_px']) / pos['risk_per_share']
                pnl = (exit_px - pos['entry_px']) * pos['qty']
                realized_pnl += pnl
                trades.append({
                    "symbol": sym, "entry_date": pos['entry_date'], "exit_date": curr_date,
                    "raw_r": raw_r, "dollar_pnl": pnl,
                    "mae": max(0, (pos['entry_px'] - pos['max_adv']) / pos['risk_per_share']),
                    "mfe": max(0, (pos['max_fav'] - pos['entry_px']) / pos['risk_per_share'])
                })
                del open_pos[sym]
                continue

            current_mfe_r = (row['high'] - pos['entry_px']) / pos['risk_per_share']
            if current_mfe_r >= 1.5 and pos['tier'] < 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                pos['tier'] = 1
            if pos['tier'] >= 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], row['ema10'] - (0.5 * atr_d))

        unrealized = sum((master_data[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_pos.items() if curr_date in master_data[s].index)
        open_risk = sum(p['qty'] * max(0.0, p['entry_px'] - p['virtual_sl']) for p in open_pos.values())
        eq = INITIAL_NAV + realized_pnl + unrealized
        eq_curve.append({"date": curr_date, "equity": eq})

        if macro_bull and len(open_pos) < 3:
            rem_risk = (eq * 0.06) - open_risk
            if rem_risk <= 0: continue

            cands = []
            for sym in universe:
                if sym in open_pos: continue
                df_d = master_data[sym]
                if curr_date not in df_d.index: continue
                idx = df_d.index.get_loc(curr_date)
                if idx < 30: continue
                row = df_d.iloc[idx]
                prev = df_d.iloc[idx - 1]
                atr = row['atr']
                if pd.isna(atr) or atr <= 0: continue

                # Restored Weinstein Stage-2 PIT Gate
                if not get_pit_stage2(sym, curr_date): continue

                breakout = (row['close'] > row['donchian_high']) and (prev['close'] <= prev['donchian_high'])
                if breakout and row['rvol'] > 1.25 and row['close'] > row['ema21']:
                    cands.append((sym, row, atr, (row['rvol'] * 25.0) + (row['adx'] * 1.5)))

            cands.sort(key=lambda x: x[3], reverse=True)
            for sym, row, atr, score in cands:
                if len(open_pos) >= 3: break
                entry_px = row['close'] + SLIP + SPREAD
                initial_sl = entry_px - (2.0 * atr)
                rps = max(entry_px - initial_sl, 0.01)
                
                dyn_risk = base_risk * min(max(row['rvol']/1.5, 0.8), 1.6) * min(max(row['adx']/25.0, 0.8), 1.5)
                qty = int(min(eq * dyn_risk, rem_risk) / rps)
                if qty <= 0: continue

                open_pos[sym] = {
                    "entry_date": curr_date, "entry_px": entry_px, "qty": qty,
                    "initial_sl": initial_sl, "virtual_sl": initial_sl, "risk_per_share": rps,
                    "tier": 0, "max_fav": entry_px, "max_adv": entry_px, "initial_atr": atr
                }
                rem_risk -= (qty * rps)

    # EXACT USER-SPECIFIED WINDOW-BOUNDED FINAL LIQUIDATION
    for sym, pos in list(open_pos.items()):
        df_d = master_data[sym]
        window_df = df_d.loc[(df_d.index >= start_dt) & (df_d.index <= end_dt)]
        if window_df.empty: continue
        last_date = window_df.index[-1]
        last_row = window_df.iloc[-1]
        exit_px = last_row['close'] - SLIP
        raw_r = (exit_px - pos['entry_px']) / pos['risk_per_share']
        pnl = (exit_px - pos['entry_px']) * pos['qty']
        realized_pnl += pnl
        trades.append({
            "symbol": sym, "entry_date": pos['entry_date'], "exit_date": last_date,
            "raw_r": raw_r, "dollar_pnl": pnl,
            "mae": max(0, (pos['entry_px'] - pos['max_adv']) / pos['risk_per_share']),
            "mfe": max(0, (pos['max_fav'] - pos['entry_px']) / pos['risk_per_share'])
        })
        del open_pos[sym]

    # EXACT USER-SPECIFIED FINAL EQUITY RECONCILIATION APPEND
    final_equity = INITIAL_NAV + realized_pnl
    eq_curve.append({"date": end_dt, "equity": final_equity})

    tdf = pd.DataFrame(trades)
    eq_df = pd.DataFrame(eq_curve).set_index('date')
    if len(tdf) == 0: return {"n": 0, "net_r": 0, "pf": 0, "exp": 0, "dd": 0, "calmar": 0, "ret": 0, "recon_error": 0.0, "df": tdf}

    if exclude_top_trades > 0:
        tdf = tdf.sort_values('raw_r', ascending=False).iloc[exclude_top_trades:].reset_index(drop=True)

    r = tdf['raw_r'].values
    wins, losses = r[r > 0], r[r <= 0]
    pf = abs(wins.sum() / (losses.sum() + 1e-9))
    eq = eq_df['equity']
    dd = ((eq.cummax() - eq) / eq.cummax()).max() * 100.0
    ret_pct = ((eq.iloc[-1] - INITIAL_NAV) / INITIAL_NAV) * 100.0
    days = max((end_dt - start_dt).days, 1)
    calmar = (ret_pct * (365.25 / days)) / dd if dd > 0 else 0.0

    trade_dollar_total = tdf['dollar_pnl'].sum()
    equity_change = eq.iloc[-1] - INITIAL_NAV
    recon_error = trade_dollar_total - equity_change

    return {
        "n": len(tdf), "net_r": r.sum(), "pf": pf, "exp": r.mean(),
        "wr": (len(wins)/len(r))*100 if len(r)>0 else 0, "dd": dd, "calmar": calmar,
        "ret": ret_pct, "mae": tdf['mae'].mean(), "mfe": tdf['mfe'].mean(),
        "recon_error": recon_error, "df": tdf
    }

print("\n" + "=" * 135)
print(" 📊 FLAWLESS V2: MULTI-CYCLE REGIME BATTERY (FULLY RECONCILED)")
print("=" * 135)
print(f"{'Regime Window':<30} | {'Trades':<7} | {'Win Rate':<9} | {'Net R':<9} | {'PF':<6} | {'Exp/Tr':<10} | {'Return':<9} | {'Max DD':<9} | {'Calmar':<7} | {'Recon Err'}")
print("-" * 135)

for start, end, label in REGIMES:
    res = run_backtest(start, end)
    if res and res['n'] > 0:
        print(f"{label:<30} | {res['n']:^7} | {res['wr']:^9.1f}% | {res['net_r']:^+9.2f}R | {res['pf']:^6.2f} | {res['exp']:^+10.2f}R/tr | {res['ret']:^+8.1f}% | {res['dd']:^8.2f}% | {res['calmar']:^7.2f} | ${res['recon_error']:>9.2f}")

print("=" * 135)

print("\n" + "=" * 135)
print(" 🛡️ FLAWLESS V2: LEAVE-ONE-TICKER-OUT STRESS TEST (2017 - 2026 Full Horizon)")
print("=" * 135)

full_res = run_backtest("2017-01-01", "2026-08-20")
print(f"• Baseline Full Horizon (N={full_res['n']}) -> Net R: {full_res['net_r']:+.2f}R | PF: {full_res['pf']:.2f} | Return: {full_res['ret']:+.1f}% | MaxDD: {full_res['dd']:.2f}% | Recon Err: ${full_res['recon_error']:.2f}")

for sym in XLK_UNIVERSE:
    loto = run_backtest("2017-01-01", "2026-08-20", exclude_symbols=[sym])
    print(f"• Without {sym:<5} -> Net R: {loto['net_r']:+.2f}R | PF: {loto['pf']:.2f} | Return: {loto['ret']:+.1f}% | MaxDD: {loto['dd']:.2f}% | Recon Err: ${loto['recon_error']:.2f}")

print("=" * 135)
