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

SECTOR_UNIVERSE = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM"],
    "XLF": ["JPM", "BAC", "WFC", "GS", "MS", "AXP"],
    "XLE": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC"],
    "XLY": ["AMZN", "TSLA", "HD", "MCD", "BKNG", "TJX"],
    "XLI": ["GE", "CAT", "UNP", "HON", "RTX", "ETN"],
    "XLV": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO"]
}
TICKER_SECTOR = {sym: sec for sec, tickers in SECTOR_UNIVERSE.items() for sym in tickers}

START_DATE = "2023-11-01"
END_DATE = "2024-06-30"
WARMUP_DATE = "2022-11-01"

MAX_POSITIONS = 3
BASE_RISK_PCT = 0.005
MAX_PORTFOLIO_RISK_PCT = 0.022
MAX_SECTOR_RISK_PCT = 0.016
INITIAL_NAV = 100000.0

SLIP, SPREAD = 0.03, 0.02

def get_alpaca_bars(symbol, timeframe="15Min", start_str="", end_str=""):
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

def calculate_rth_vwap(df):
    typical_px = (df['high'] + df['low'] + df['close']) / 3.0
    pv = typical_px * df['volume']
    session_date = df['time'].dt.date
    cum_pv = pv.groupby(session_date).cumsum()
    cum_v = df['volume'].groupby(session_date).cumsum()
    return cum_pv / (cum_v + 1e-9)

print("=" * 115)
print(" 🚀 OPTION A: PURE INTRADAY BULL PULLBACK ENGINE (15:50 HARD FLAT)")
print("=" * 115)
print(f"📥 Loading Data ({START_DATE} to {END_DATE})...")

spy_df = get_alpaca_bars("SPY", "1Day", start_str=WARMUP_DATE, end_str=END_DATE)
spy_df['ema20'] = spy_df['close'].ewm(span=20, adjust=False).mean()
spy_df['sma50'] = spy_df['close'].rolling(50).mean()
spy_df['macro_bull'] = (spy_df['close'] > spy_df['ema20']) & (spy_df['ema20'] > spy_df['sma50'])
spy_df['macro_bull_pit'] = spy_df['macro_bull'].shift(1).fillna(False)
spy_df['date'] = spy_df['time'].dt.date
spy_macro_map = dict(zip(spy_df['date'], spy_df['macro_bull_pit']))

weekly_data = {}
all_symbols = [s for tickers in SECTOR_UNIVERSE.values() for s in tickers]
for sym in all_symbols:
    df_w = get_alpaca_bars(sym, "1Week", start_str=WARMUP_DATE, end_str=END_DATE)
    if df_w is not None and len(df_w) >= 35:
        df_w['sma30w'] = df_w['close'].rolling(30).mean()
        df_w['sma30w_slope'] = df_w['sma30w'].diff(3)
        df_w['prev_20w_high'] = df_w['high'].rolling(20).max().shift(1)
        df_w['sma30w_pit'] = df_w['sma30w'].shift(1)
        df_w['sma30w_slope_pit'] = df_w['sma30w_slope'].shift(1)
        df_w['prev_20w_high_pit'] = df_w['prev_20w_high'].shift(1)
        df_w['close_pit'] = df_w['close'].shift(1)
        weekly_data[sym] = df_w
    time.sleep(0.01)

fleet_intraday = {}
for sym in all_symbols:
    df_raw = get_alpaca_bars(sym, "15Min", start_str=START_DATE, end_str=END_DATE)
    if df_raw is None or len(df_raw) < 50: continue
    df_15 = df_raw[(df_raw['time'].dt.time >= datetime.time(9, 30)) & 
                   (df_raw['time'].dt.time <= datetime.time(16, 0))].copy().reset_index(drop=True)
    if len(df_15) < 50: continue
    df_15['ema9'] = df_15['close'].ewm(span=9, adjust=False).mean()
    df_15['ema21'] = df_15['close'].ewm(span=21, adjust=False).mean()
    df_15['bb_mid'] = df_15['close'].rolling(20).mean()
    df_15['bb_std'] = df_15['close'].rolling(20).std()
    df_15['bb_upper'] = df_15['bb_mid'] + (2.0 * df_15['bb_std'])
    df_15['bb_lower'] = df_15['bb_mid'] - (2.0 * df_15['bb_std'])
    tr = pd.concat([df_15['high'] - df_15['low'], 
                    (df_15['high'] - df_15['close'].shift(1)).abs(), 
                    (df_15['low'] - df_15['close'].shift(1)).abs()], axis=1).max(axis=1)
    df_15['atr'] = tr.rolling(14).mean()
    df_15['vol_sma20'] = df_15['volume'].rolling(20).mean()
    df_15['vwap'] = calculate_rth_vwap(df_15)
    df_15['swing_high'] = df_15['high'].rolling(30).max().shift(1)
    df_15['swing_low'] = df_15['low'].rolling(30).min().shift(1)
    df_15.set_index('time', inplace=True)
    fleet_intraday[sym] = df_15
    time.sleep(0.01)

def get_pit_stage2(sym, current_time):
    df_w = weekly_data.get(sym)
    if df_w is None: return False
    past_w = df_w[df_w['time'] <= current_time]
    if len(past_w) < 32: return False
    row = past_w.iloc[-1]
    if pd.isna(row['sma30w_pit']) or pd.isna(row['sma30w_slope_pit']): return False
    return (row['close_pit'] > row['sma30w_pit'] and 
            row['sma30w_slope_pit'] > 0 and 
            row['close_pit'] >= (0.95 * row['prev_20w_high_pit']))

def get_pit_breadth(sec, current_time):
    tickers = SECTOR_UNIVERSE.get(sec, [])
    s2 = sum(1 for s in tickers if get_pit_stage2(s, current_time))
    return s2 >= 4

all_timestamps = sorted(list(set.union(*[set(df.index) for df in fleet_intraday.values()])))

portfolio_nav = INITIAL_NAV
realized_pnl_dollars = 0.0
open_positions = {}
closed_trades = []
nav_history = []

print(f"⚙️ Simulating Chronological Event Stream Across Fleet ({len(all_timestamps)} Bars)...")

for ts in all_timestamps:
    curr_date = ts.date()
    curr_time = ts.time()
    in_entry_session = (curr_time >= datetime.time(10, 0)) and (curr_time <= datetime.time(14, 30))
    is_hard_flat_bar = (curr_time >= datetime.time(15, 50))
    macro_bull = spy_macro_map.get(curr_date, False)

    # 1. Manage Active Positions
    for sym in list(open_positions.keys()):
        pos = open_positions[sym]
        df_sym = fleet_intraday.get(sym)
        if df_sym is None or ts not in df_sym.index: continue
        
        row = df_sym.loc[ts]
        atr_val = row['atr'] if pd.notna(row['atr']) and row['atr'] > 0 else pos['initial_atr']
        pos['max_fav_px'] = max(pos['max_fav_px'], row['high'])
        pos['max_adv_px'] = min(pos['max_adv_px'], row['low'])

        # Hard Flat Exit at 15:50 ET
        if is_hard_flat_bar:
            exit_px = row['close'] - SLIP
            raw_r = (exit_px - pos['entry_px']) / pos['initial_risk_per_share']
            dollar_pnl = (exit_px - pos['entry_px']) * pos['qty']
            realized_pnl_dollars += dollar_pnl
            closed_trades.append({
                "symbol": sym, "sector": pos['sector'], "entry_time": pos['entry_time'], "exit_time": ts,
                "entry_px": pos['entry_px'], "exit_px": exit_px, "raw_r": raw_r, "dollar_pnl": dollar_pnl,
                "exit_reason": "HARD_FLAT_1550", "mae_r": max(0, (pos['entry_px'] - pos['max_adv_px']) / pos['initial_risk_per_share']),
                "mfe_r": max(0, (pos['max_fav_px'] - pos['entry_px']) / pos['initial_risk_per_share'])
            })
            del open_positions[sym]
            continue

        # Intraday Stop Hit
        if row['low'] <= (pos['virtual_sl'] + SPREAD):
            exit_px = pos['virtual_sl'] - SLIP
            raw_r = (exit_px - pos['entry_px']) / pos['initial_risk_per_share']
            dollar_pnl = (exit_px - pos['entry_px']) * pos['qty']
            realized_pnl_dollars += dollar_pnl
            closed_trades.append({
                "symbol": sym, "sector": pos['sector'], "entry_time": pos['entry_time'], "exit_time": ts,
                "entry_px": pos['entry_px'], "exit_px": exit_px, "raw_r": raw_r, "dollar_pnl": dollar_pnl,
                "exit_reason": "INTRADAY_STOP", "mae_r": max(0, (pos['entry_px'] - pos['max_adv_px']) / pos['initial_risk_per_share']),
                "mfe_r": max(0, (pos['max_fav_px'] - pos['entry_px']) / pos['initial_risk_per_share'])
            })
            del open_positions[sym]
            continue

        # Target 1: Snap Stop to Break-Even + 0.05 ATR
        if row['high'] >= pos['target_tp1'] and pos['tier'] < 1:
            pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'] + (0.05 * atr_val))
            pos['tier'] = 1

        # Target 2: Ratchet Trailing Stop to EMA21
        if row['high'] >= pos['target_tp2'] and pos['tier'] < 2:
            pos['virtual_sl'] = max(pos['virtual_sl'], row['ema21'] - (0.8 * atr_val))
            pos['tier'] = 2
        elif pos['tier'] == 2:
            pos['virtual_sl'] = max(pos['virtual_sl'], row['ema21'] - (0.8 * atr_val))

    unrealized_pnl = sum((fleet_intraday[s].loc[ts]['close'] - p['entry_px']) * p['qty'] 
                         for s, p in open_positions.items() if s in fleet_intraday and ts in fleet_intraday[s].index)
    current_equity = INITIAL_NAV + realized_pnl_dollars + unrealized_pnl
    nav_history.append({"time": ts, "equity": current_equity})

    # 2. Evaluate Intraday Pullback Entries
    if in_entry_session and macro_bull and len(open_positions) < MAX_POSITIONS:
        total_open_risk = sum(p['qty'] * max(0.0, p['entry_px'] - p['virtual_sl']) for p in open_positions.values())
        portfolio_cap = current_equity * MAX_PORTFOLIO_RISK_PCT
        sector_cap = current_equity * MAX_SECTOR_RISK_PCT
        rem_portfolio_risk = portfolio_cap - total_open_risk
        if rem_portfolio_risk <= 0: continue

        candidates = []
        for sym, df_sym in fleet_intraday.items():
            if sym in open_positions or ts not in df_sym.index: continue
            idx = df_sym.index.get_loc(ts)
            if idx < 40: continue
            
            row = df_sym.iloc[idx]
            prev = df_sym.iloc[idx - 1]
            atr_val = row['atr']
            if pd.isna(atr_val) or atr_val <= 0: continue

            sec = TICKER_SECTOR.get(sym)
            if not get_pit_breadth(sec, ts) or not get_pit_stage2(sym, ts): continue

            # Trend Structure
            if not (row['ema9'] > row['ema21'] and row['ema21'] > row['bb_mid']): continue

            # Support Confluence: EMA21 and VWAP
            support_high = max(row['ema21'], row['vwap'])
            support_low = min(row['ema21'], row['vwap'])

            # Pullback Touch & Reversal Bounce
            touched_support = row['low'] <= (support_high + 0.25 * atr_val)
            held_support = row['close'] >= (support_low - 0.20 * atr_val)
            bullish_bounce = (row['close'] >= row['open']) and (row['close'] > prev['close'])

            if touched_support and held_support and bullish_bounce:
                candidates.append((sym, sec, row, atr_val, support_low))

        # Sort by distance to VWAP / EMA21 support for best risk-reward
        candidates.sort(key=lambda x: abs(x[2]['close'] - x[4]))

        for sym, sec, row, atr_val, sup_low in candidates:
            if len(open_positions) >= MAX_POSITIONS: break
            current_sec_risk = sum(p['qty'] * max(0.0, p['entry_px'] - p['virtual_sl']) 
                                   for p in open_positions.values() if p['sector'] == sec)
            rem_sec_risk = sector_cap - current_sec_risk
            if rem_sec_risk <= 0: continue

            entry_px = row['close'] + SLIP + SPREAD
            initial_sl = sup_low - (0.8 * atr_val)
            if (entry_px - initial_sl) < (0.8 * atr_val):
                initial_sl = entry_px - (1.0 * atr_val)
            
            risk_per_share = max(entry_px - initial_sl, 0.01)
            allowed_risk = min(current_equity * BASE_RISK_PCT, rem_portfolio_risk, rem_sec_risk)
            qty = int(allowed_risk / risk_per_share)
            if qty <= 0: continue

            open_positions[sym] = {
                "sector": sec, "entry_time": ts, "entry_px": entry_px, "qty": qty,
                "initial_sl": initial_sl, "virtual_sl": initial_sl, "initial_risk_per_share": risk_per_share,
                "target_tp1": entry_px + (1.5 * risk_per_share),
                "target_tp2": max(row['bb_upper'], entry_px + (2.5 * risk_per_share)),
                "tier": 0, "max_fav_px": entry_px, "max_adv_px": entry_px, "initial_atr": atr_val
            }
            rem_portfolio_risk -= (qty * risk_per_share)

tdf = pd.DataFrame(closed_trades)
eq_df = pd.DataFrame(nav_history).set_index('time')

if len(tdf) == 0:
    print("❌ Zero trades triggered under Option A.")
    exit()

r = tdf['raw_r'].values
wins, losses = r[r > 0], r[r <= 0]
cum = np.cumsum(r)
max_dd_r = np.max(np.maximum.accumulate(cum) - cum)

eq_series = eq_df['equity']
eq_peak = eq_series.cummax()
eq_dd_dollars = eq_peak - eq_series
eq_max_dd_pct = (eq_dd_dollars / eq_peak).max() * 100.0

total_return_pct = ((eq_series.iloc[-1] - INITIAL_NAV) / INITIAL_NAV) * 100.0
days = (pd.to_datetime(END_DATE) - pd.to_datetime(START_DATE)).days
annualized_return_pct = total_return_pct * (365.25 / days)
calmar_ratio = annualized_return_pct / eq_max_dd_pct if eq_max_dd_pct > 0 else 0.0
harvest_dd = r.sum() / max_dd_r if max_dd_r > 0 else 0.0

print("\n" + "=" * 115)
print(" 🏆 OPTION A: INTRADAY BULL PULLBACK AUDIT RESULTS (15:50 HARD FLAT)")
print("=" * 115)
print(f"• Total Trades Evaluated (N) : {len(tdf)}")
print(f"• Win Rate (%)               : {(len(wins)/len(r))*100:.1f}%")
print(f"• Total Net Harvest (R)      : {r.sum():+.2f}R")
print(f"• Profit Factor (PF)         : {abs(wins.sum()/(losses.sum()+1e-9)):.2f}")
print(f"• Expectancy per Trade       : {r.mean():+.2f}R/trade")
print(f"• Average Winner / Loser     : {wins.mean():+.2f}R / {losses.mean():+.2f}R (Payoff: {abs(wins.mean()/(losses.mean()+1e-9)):.2f})")
print(f"• Portfolio Net Return       : +${eq_series.iloc[-1]-INITIAL_NAV:,.2f} ({total_return_pct:+.2f}%)")
print(f"• Realized Max DD (R-Terms)  : {max_dd_r:.2f}R")
print(f"• Realized Max DD (Equity %) : {eq_max_dd_pct:.2f}%")
print(f"• Harvest / MaxDD Ratio      : {harvest_dd:.2f}")
print(f"• Portfolio Calmar Ratio     : {calmar_ratio:.2f}")
print(f"• Mean Adverse Excursion     : {tdf['mae_r'].mean():.2f}R")
print(f"• Mean Favorable Excursion   : {tdf['mfe_r'].mean():.2f}R")
print("-" * 115)

print("\n📊 Sector Attribution Breakdown:")
sec_grp = tdf.groupby('sector').agg(
    Trades=('raw_r', 'count'),
    WinRate=('raw_r', lambda x: f"{(x > 0).mean()*100:.1f}%"),
    Net_R=('raw_r', 'sum'),
    Dollar_PnL=('dollar_pnl', 'sum'),
    PF=('raw_r', lambda x: abs(x[x > 0].sum() / (x[x <= 0].sum() + 1e-9)))
).reset_index()
print(sec_grp.to_string(index=False))

print("\n📈 Ticker Attribution Breakdown (Top 12):")
tick_grp = tdf.groupby('symbol').agg(
    Trades=('raw_r', 'count'),
    WinRate=('raw_r', lambda x: f"{(x > 0).mean()*100:.1f}%"),
    Net_R=('raw_r', 'sum'),
    Dollar_PnL=('dollar_pnl', 'sum'),
    PF=('raw_r', lambda x: abs(x[x > 0].sum() / (x[x <= 0].sum() + 1e-9)))
).reset_index().sort_values('Net_R', ascending=False)
print(tick_grp.head(12).to_string(index=False))
print("=" * 115)
