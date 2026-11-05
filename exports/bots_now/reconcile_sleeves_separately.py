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

XLK_UNIVERSE = ["MSFT", "NVDA", "AVGO", "AMD", "CRM"]
SHORT_UNIVERSE = ["SPY", "QQQ"]

START_DATE = "2017-01-01"
END_DATE = "2026-08-20"
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
print(" 🔬 ISOLATED RECONCILIATION BATTERY (A: STAGE-4 SHORT, B: XLK NO-AAPL LONG)")
print("=" * 135)

master_data = {}
weekly_data = {}
all_syms = list(set(XLK_UNIVERSE + SHORT_UNIVERSE + ["SPY"]))
for sym in all_syms:
    df_d = get_alpaca_bars(sym, "1Day", start_str="2016-10-01", end_str=END_DATE)
    if df_d is not None and len(df_d) > 100:
        df_d['ema10'] = df_d['close'].ewm(span=10, adjust=False).mean()
        df_d['ema21'] = df_d['close'].ewm(span=21, adjust=False).mean()
        df_d['sma50'] = df_d['close'].rolling(50).mean()
        df_d['donchian_high'] = df_d['high'].rolling(20).max().shift(1)
        df_d['donchian_low'] = df_d['low'].rolling(20).min().shift(1)
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

    df_w = get_alpaca_bars(sym, "1Week", start_str="2016-10-01", end_str=END_DATE)
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

spy_df = master_data["SPY"]
spy_df['ema20'] = spy_df['close'].ewm(span=20, adjust=False).mean()
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
    return (row['close_pit'] > row['sma30w_pit'] and 
            row['sma30w_slope_pit'] > 0 and 
            row['close_pit'] >= (0.95 * row['prev_20w_high_pit']))

start_dt = datetime.datetime.strptime(START_DATE, "%Y-%m-%d").date()
end_dt = datetime.datetime.strptime(END_DATE, "%Y-%m-%d").date()
dates = sorted(list(set.union(*[set(master_data[s].index) for s in all_syms if s in master_data])))
dates = [d for d in dates if start_dt <= d <= end_dt]

# ==========================================
# TEST A: STAGE-4 SHORT SLEEVE ISOLATED
# ==========================================
print("\n🔎 Running Test A: Stage-4 Short Sleeve Isolated...")
nav_a = INITIAL_NAV
pnl_a = 0.0
trades_a = []
open_a = {}

for curr_date in dates:
    macro_bull = spy_macro_map.get(curr_date, False)

    for pos_id in list(open_a.keys()):
        pos = open_a[pos_id]
        df_d = master_data[pos['sym']]
        if curr_date not in df_d.index: continue
        row = df_d.loc[curr_date]
        pos['max_adv'] = max(pos['max_adv'], row['high'])
        pos['max_fav'] = min(pos['max_fav'], row['low'])

        # Corrected Short Stop Logic
        stop_px = pos['virtual_sl']
        exit_px = None
        reason = None
        if row['open'] >= stop_px:
            exit_px = row['open'] + SLIP
            reason = "OPEN_GAP_STOP"
        elif row['high'] >= stop_px:
            exit_px = stop_px + SLIP
            reason = "STOP_HIT"

        if exit_px is not None:
            raw_r = (pos['entry_px'] - exit_px) / pos['risk_prs']
            pnl = (pos['entry_px'] - exit_px) * pos['qty']
            pnl_a += pnl
            trades_a.append({
                "symbol": pos['sym'], "entry": pos['entry_px'], "exit": exit_px, "stop": stop_px,
                "raw_r": raw_r, "dollar_pnl": pnl, "exit_reason": reason
            })
            del open_a[pos_id]
            continue

        # Break-even ratchet at +1.0R
        current_mfe_r = (pos['entry_px'] - row['low']) / pos['risk_prs']
        if current_mfe_r >= 1.0 and pos['tier'] < 1:
            pos['virtual_sl'] = min(pos['virtual_sl'], pos['entry_px'])
            pos['tier'] = 1

    eq_a = INITIAL_NAV + pnl_a

    if not macro_bull and len(open_a) < 3:
        for sym in SHORT_UNIVERSE:
            if any(p['sym'] == sym for p in open_a.values()): continue
            df_d = master_data[sym]
            if curr_date not in df_d.index: continue
            idx = df_d.index.get_loc(curr_date)
            if idx < 30: continue
            row = df_d.iloc[idx]
            prev = df_d.iloc[idx - 1]
            atr = row['atr']
            if pd.isna(atr) or atr <= 0: continue

            # Stage-4 Short entry criteria (Close < Donchian Low, Close < EMA21, EMA21 < SMA50)
            breakout_short = (row['close'] < row['donchian_low']) and (prev['close'] >= prev['donchian_low'])
            trend_short = (row['close'] < row['ema21']) and (row['ema21'] < row['sma50'])
            if breakout_short and trend_short:
                entry_px = row['close'] - SLIP - SPREAD
                initial_sl = entry_px + (1.5 * atr)
                rps = max(initial_sl - entry_px, 0.01)
                qty = int((eq_a * 0.01) / rps)
                if qty > 0:
                    pos_id = f"SHORT_{sym}_{curr_date}"
                    open_a[pos_id] = {
                        "sym": sym, "entry_date": curr_date, "entry_px": entry_px, "qty": qty,
                        "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "max_fav": entry_px, "max_adv": entry_px
                    }
                    break

# Final window close for Test A
for pos_id, pos in list(open_a.items()):
    df_d = master_data[pos['sym']]
    window_df = df_d.loc[(df_d.index >= start_dt) & (df_d.index <= end_dt)]
    if window_df.empty: continue
    last_row = window_df.iloc[-1]
    exit_px = last_row['close']
    raw_r = (pos['entry_px'] - exit_px) / pos['risk_prs']
    pnl = (pos['entry_px'] - exit_px) * pos['qty']
    pnl_a += pnl
    trades_a.append({
        "symbol": pos['sym'], "entry": pos['entry_px'], "exit": exit_px, "stop": pos['virtual_sl'],
        "raw_r": raw_r, "dollar_pnl": pnl, "exit_reason": "MARK_TO_MARKET"
    })

tdf_a = pd.DataFrame(trades_a)
print(f"• Test A (Stage-4 Shorts) Total Trades: {len(tdf_a)}")
if len(tdf_a) > 0:
    wins_a = tdf_a['raw_r'] > 0
    print(f"• Win Rate: {wins_a.mean()*100:.1f}% | Net R: {tdf_a['raw_r'].sum():+.2f}R | Net PnL: ${tdf_a['dollar_pnl'].sum():,.2f}")
    print("\nFirst 5 Closed Short Trades:")
    print(tdf_a.head(5).to_string(index=False))

# ==========================================
# TEST B: XLK NO-AAPL LONG SLEEVE ISOLATED
# ==========================================
print("\n🔎 Running Test B: XLK No-AAPL Long Sleeve Isolated...")
pnl_b = 0.0
trades_b = []
open_b = {}

for curr_date in dates:
    macro_bull = spy_macro_map.get(curr_date, False)

    for pos_id in list(open_b.keys()):
        pos = open_b[pos_id]
        df_d = master_data[pos['sym']]
        if curr_date not in df_d.index: continue
        row = df_d.loc[curr_date]
        pos['max_fav'] = max(pos['max_fav'], row['high'])
        pos['max_adv'] = min(pos['max_adv'], row['low'])
        atr_d = row['atr'] if pd.notna(row['atr']) and row['atr'] > 0 else pos['initial_atr']

        if row['low'] <= pos['virtual_sl']:
            exit_px = min(row['open'], pos['virtual_sl']) - SLIP
            raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
            pnl = (exit_px - pos['entry_px']) * pos['qty']
            pnl_b += pnl
            trades_b.append({"symbol": pos['sym'], "raw_r": raw_r, "dollar_pnl": pnl})
            del open_b[pos_id]
            continue

        mfe_r = (row['high'] - pos['entry_px']) / pos['risk_prs']
        if mfe_r >= 1.5 and pos['tier'] < 1:
            pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
            pos['tier'] = 1
        if pos['tier'] >= 1:
            pos['virtual_sl'] = max(pos['virtual_sl'], row['ema10'] - (0.5 * atr_d))

    eq_b = INITIAL_NAV + pnl_b

    if macro_bull and len(open_b) < 3:
        for sym in XLK_UNIVERSE:
            if any(p['sym'] == sym for p in open_b.values()): continue
            df_d = master_data[sym]
            if curr_date not in df_d.index: continue
            idx = df_d.index.get_loc(curr_date)
            if idx < 30: continue
            row = df_d.iloc[idx]
            prev = df_d.iloc[idx - 1]
            atr = row['atr']
            if pd.isna(atr) or atr <= 0: continue

            if not get_pit_stage2(sym, curr_date): continue

            breakout = (row['close'] > row['donchian_high']) and (prev['close'] <= prev['donchian_high'])
            if breakout and row['rvol'] > 1.25 and row['close'] > row['ema21']:
                entry_px = row['close'] + SLIP + SPREAD
                initial_sl = entry_px - (2.0 * atr)
                rps = max(entry_px - initial_sl, 0.01)
                dyn_risk = 0.0075 * min(max(row['rvol']/1.5, 0.8), 1.6) * min(max(row['adx']/25.0, 0.8), 1.5)
                qty = int((eq_b * dyn_risk) / rps)
                if qty > 0:
                    pos_id = f"LONG_{sym}_{curr_date}"
                    open_b[pos_id] = {
                        "sym": sym, "entry_date": curr_date, "entry_px": entry_px, "qty": qty,
                        "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "max_fav": entry_px, "max_adv": entry_px, "initial_atr": atr
                    }
                    break

for pos_id, pos in list(open_b.items()):
    df_d = master_data[pos['sym']]
    window_df = df_d.loc[(df_d.index >= start_dt) & (df_d.index <= end_dt)]
    if window_df.empty: continue
    last_row = window_df.iloc[-1]
    exit_px = last_row['close']
    raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
    pnl = (exit_px - pos['entry_px']) * pos['qty']
    pnl_b += pnl
    trades_b.append({"symbol": pos['sym'], "raw_r": raw_r, "dollar_pnl": pnl})

tdf_b = pd.DataFrame(trades_b)
print(f"• Test B (XLK No-AAPL Longs) Total Trades: {len(tdf_b)}")
if len(tdf_b) > 0:
    wins_b = tdf_b['raw_r'] > 0
    print(f"• Win Rate: {wins_b.mean()*100:.1f}% | Net R: {tdf_b['raw_r'].sum():+.2f}R | Net PnL: ${tdf_b['dollar_pnl'].sum():,.2f}")

print("=" * 135)
