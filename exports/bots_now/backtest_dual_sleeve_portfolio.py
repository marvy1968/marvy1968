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

XLK_UNIVERSE = ["MSFT", "NVDA", "AVGO", "AMD", "CRM"] # No AAPL
SHORT_UNIVERSE = ["SPY", "QQQ"] # Stage-4 short proxies / index shorts or tech weakness

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
print(" 🚀 PHASE 1: DUAL-SLEEVE PORTFOLIO REPLAY (SHARED $100K NAV)")
print("=" * 135)
print("📥 Fetching master data for Portfolio Simulation...")

master_data = {}
weekly_data = {}
all_syms = list(set(XLK_UNIVERSE + SHORT_UNIVERSE + ["SPY"]))
for sym in all_syms:
    df_d = get_alpaca_bars(sym, "1Day", start_str="2016-10-01", end_str=END_DATE)
    if df_d is not None and len(df_d) > 100:
        df_d['ema10'] = df_d['close'].ewm(span=10, adjust=False).mean()
        df_d['ema21'] = df_d['close'].ewm(span=21, adjust=False).mean()
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
spy_df['sma50'] = spy_df['close'].rolling(50).mean()
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

nav = INITIAL_NAV
realized_pnl = 0.0
open_positions = {} # {id: {sleeve, sym, entry_px, qty, risk_prs, virtual_sl, ...}}
trades_log = []
equity_curve = []

print("⚙️ Executing Unified Chronological Portfolio Simulation...")

for curr_date in dates:
    macro_bull = spy_macro_map.get(curr_date, False)

    # 1. Manage Active Positions (Both Sleeves)
    for pos_id in list(open_positions.keys()):
        pos = open_positions[pos_id]
        sym = pos['sym']
        df_d = master_data.get(sym)
        if df_d is None or curr_date not in df_d.index: continue
        row = df_d.loc[curr_date]
        pos['max_fav'] = max(pos['max_fav'], row['high'] if pos['sleeve']=='LONG' else pos['entry_px'])
        pos['max_adv'] = min(pos['max_adv'], row['low'] if pos['sleeve']=='LONG' else pos['entry_px'])
        atr_d = row['atr'] if pd.notna(row['atr']) and row['atr'] > 0 else pos['initial_atr']

        if pos['sleeve'] == 'LONG':
            # Long Exit: Stop hit or trailing stop
            if row['low'] <= pos['virtual_sl']:
                exit_px = min(row['open'], pos['virtual_sl']) - SLIP
                raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
                pnl = (exit_px - pos['entry_px']) * pos['qty']
                realized_pnl += pnl
                trades_log.append({"sleeve": "XLK_LONG", "symbol": sym, "entry_date": pos['entry_date'], "exit_date": curr_date, "raw_r": raw_r, "pnl": pnl})
                del open_positions[pos_id]
                continue
            
            mfe_r = (row['high'] - pos['entry_px']) / pos['risk_prs']
            if mfe_r >= 1.5 and pos['tier'] < 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                pos['tier'] = 1
            if pos['tier'] >= 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], row['ema10'] - (0.5 * atr_d))

        elif pos['sleeve'] == 'SHORT':
            # Short Exit: Stop hit or break-even ratchet
            if row['high'] >= pos['virtual_sl']:
                exit_px = max(row['open'], pos['virtual_sl']) + SLIP
                raw_r = (pos['entry_px'] - exit_px) / pos['risk_prs']
                pnl = (pos['entry_px'] - exit_px) * pos['qty']
                realized_pnl += pnl
                trades_log.append({"sleeve": "STAGE4_SHORT", "symbol": sym, "entry_date": pos['entry_date'], "exit_date": curr_date, "raw_r": raw_r, "pnl": pnl})
                del open_positions[pos_id]
                continue

    # 2. Portfolio Accounting
    unrealized = 0.0
    for pos_id, pos in open_positions.items():
        df_d = master_data.get(pos['sym'])
        if df_d is not None and curr_date in df_d.index:
            curr_px = df_d.loc[curr_date]['close']
            if pos['sleeve'] == 'LONG':
                unrealized += (curr_px - pos['entry_px']) * pos['qty']
            else:
                unrealized += (pos['entry_px'] - curr_px) * pos['qty']

    current_eq = INITIAL_NAV + realized_pnl + unrealized
    equity_curve.append({"date": curr_date, "equity": current_eq})

    # 3. Sleeve #3 Entries (No-AAPL XLK Long Growth @ 0.75% Risk)
    long_count = sum(1 for p in open_positions.values() if p['sleeve'] == 'LONG')
    if macro_bull and long_count < 3:
        for sym in XLK_UNIVERSE:
            if any(p['sym'] == sym for p in open_positions.values()): continue
            df_d = master_data.get(sym)
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
                entry_px = row['close'] + SLIP + SPREAD
                initial_sl = entry_px - (2.0 * atr)
                rps = max(entry_px - initial_sl, 0.01)
                dyn_risk = 0.0075 * min(max(row['rvol']/1.5, 0.8), 1.6) * min(max(row['adx']/25.0, 0.8), 1.5)
                qty = int((current_eq * dyn_risk) / rps)
                if qty > 0:
                    pos_id = f"LONG_{sym}_{curr_date}"
                    open_positions[pos_id] = {
                        "sleeve": "LONG", "sym": sym, "entry_date": curr_date, "entry_px": entry_px, "qty": qty,
                        "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "max_fav": entry_px, "max_adv": entry_px, "initial_atr": atr
                    }
                    break # max 1 per day

    # 4. Sleeve #1 Entries (Stage-4 Short Engine)
    short_count = sum(1 for p in open_positions.values() if p['sleeve'] == 'SHORT')
    if not macro_bull and short_count < 3:
        for sym in SHORT_UNIVERSE:
            if any(p['sym'] == sym for p in open_positions.values()): continue
            df_d = master_data.get(sym)
            if df_d is None or curr_date not in df_d.index: continue
            idx = df_d.index.get_loc(curr_date)
            if idx < 30: continue
            row = df_d.iloc[idx]
            prev = df_d.iloc[idx - 1]
            atr = row['atr']
            if pd.isna(atr) or atr <= 0: continue

            breakout_short = (row['close'] < row['donchian_low']) and (prev['close'] >= prev['donchian_low'])
            if breakout_short and row['close'] < row['ema21']:
                entry_px = row['close'] - SLIP - SPREAD
                initial_sl = entry_px + (1.5 * atr)
                rps = max(initial_sl - entry_px, 0.01)
                qty = int((current_eq * 0.01) / rps)
                if qty > 0:
                    pos_id = f"SHORT_{sym}_{curr_date}"
                    open_positions[pos_id] = {
                        "sleeve": "SHORT", "sym": sym, "entry_date": curr_date, "entry_px": entry_px, "qty": qty,
                        "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "max_fav": entry_px, "max_adv": entry_px, "initial_atr": atr
                    }
                    break

# Final window close
for pos_id, pos in list(open_positions.items()):
    df_d = master_data.get(pos['sym'])
    window_df = df_d.loc[(df_d.index >= start_dt) & (df_d.index <= end_dt)]
    if window_df.empty: continue
    last_row = window_df.iloc[-1]
    exit_px = last_row['close']
    if pos['sleeve'] == 'LONG':
        raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
        pnl = (exit_px - pos['entry_px']) * pos['qty']
        trades_log.append({"sleeve": "XLK_LONG", "symbol": pos['sym'], "entry_date": pos['entry_date'], "exit_date": window_df.index[-1], "raw_r": raw_r, "pnl": pnl})
    else:
        raw_r = (pos['entry_px'] - exit_px) / pos['risk_prs']
        pnl = (pos['entry_px'] - exit_px) * pos['qty']
        trades_log.append({"sleeve": "STAGE4_SHORT", "symbol": pos['sym'], "entry_date": pos['entry_date'], "exit_date": window_df.index[-1], "raw_r": raw_r, "pnl": pnl})
    realized_pnl += pnl

eq_df = pd.DataFrame(equity_curve).set_index('date')
final_eq = INITIAL_NAV + realized_pnl
eq_df.loc[end_dt] = final_eq

tdf = pd.DataFrame(trades_log)

print("\n" + "=" * 135)
print(" 🏆 DUAL-SLEEVE PORTFOLIO PERFORMANCE SUMMARY (2017 - 2026)")
print("=" * 135)
total_ret = ((eq_df['equity'].iloc[-1] - INITIAL_NAV) / INITIAL_NAV) * 100.0
eq_peak = eq_df['equity'].cummax()
max_dd = ((eq_peak - eq_df['equity']) / eq_peak).max() * 100.0
days = (end_dt - start_dt).days
calmar = (total_ret * (365.25 / days)) / max_dd if max_dd > 0 else 0.0

print(f"• Combined Starting NAV       : ${INITIAL_NAV:,.2f}")
print(f"• Combined Ending NAV         : ${eq_df['equity'].iloc[-1]:,.2f} ({total_ret:+.2f}%)")
print(f"• Portfolio Max Drawdown      : {max_dd:.2f}%")
print(f"• Annualized Calmar Ratio     : {calmar:.2f}")
print(f"• Total Trades Executed (N)   : {len(tdf)}")

print("\n📊 Sleeve Attribution Breakdown:")
sleeve_grp = tdf.groupby('sleeve').agg(
    Trades=('raw_r', 'count'),
    Net_R=('raw_r', 'sum'),
    Total_PnL=('pnl', 'sum'),
    WinRate=('raw_r', lambda x: f"{(x > 0).mean()*100:.1f}%")
).reset_index()
print(sleeve_grp.to_string(index=False))
print("=" * 135)
