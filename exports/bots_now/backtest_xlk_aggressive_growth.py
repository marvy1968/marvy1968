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

START_DATE = "2023-11-01"
END_DATE = "2024-06-30"
WARMUP_DATE = "2022-11-01"

MAX_POSITIONS = 3
BASE_RISK_PCT = 0.012
MAX_PORTFOLIO_RISK_PCT = 0.06
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

print("=" * 125)
print(" 🚀 XLK PURE TECH LEADERSHIP MODEL: DYNAMIC POSITION SIZING & SWING RUNNERS")
print("=" * 125)
print(f"📥 Loading Daily Data ({START_DATE} to {END_DATE})...")

spy_df = get_alpaca_bars("SPY", "1Day", start_str=WARMUP_DATE, end_str=END_DATE)
spy_df['ema20'] = spy_df['close'].ewm(span=20, adjust=False).mean()
spy_df['sma50'] = spy_df['close'].rolling(50).mean()
spy_df['macro_bull'] = (spy_df['close'] > spy_df['ema20']) & (spy_df['ema20'] > spy_df['sma50'])
spy_df['macro_bull_pit'] = spy_df['macro_bull'].shift(1).fillna(False)
spy_df['date'] = spy_df['time'].dt.date
spy_macro_map = dict(zip(spy_df['date'], spy_df['macro_bull_pit']))

weekly_data = {}
for sym in XLK_UNIVERSE:
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

fleet_daily = {}
for sym in XLK_UNIVERSE:
    df_d = get_alpaca_bars(sym, "1Day", start_str=START_DATE, end_str=END_DATE)
    if df_d is None or len(df_d) < 50: continue
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
    fleet_daily[sym] = df_d
    time.sleep(0.01)

def get_pit_stage2(sym, current_date):
    df_w = weekly_data.get(sym)
    if df_w is None: return False
    past_w = df_w[df_w['time'].dt.date <= current_date]
    if len(past_w) < 32: return False
    row = past_w.iloc[-1]
    if pd.isna(row['sma30w_pit']) or pd.isna(row['sma30w_slope_pit']): return False
    return (row['close_pit'] > row['sma30w_pit'] and 
            row['sma30w_slope_pit'] > 0 and 
            row['close_pit'] >= (0.95 * row['prev_20w_high_pit']))

all_trading_dates = sorted(list(set.union(*[set(df.index) for df in fleet_daily.values()])))

portfolio_nav = INITIAL_NAV
realized_pnl_dollars = 0.0
open_positions = {}
closed_trades = []
equity_curve = []

print("⚙️ Executing XLK Aggressive Growth Simulation...")

for curr_date in all_trading_dates:
    macro_bull = spy_macro_map.get(curr_date, False)

    # 1. Manage Active Positions Daily
    for sym in list(open_positions.keys()):
        pos = open_positions[sym]
        df_d = fleet_daily.get(sym)
        if df_d is None or curr_date not in df_d.index: continue
        
        row = df_d.loc[curr_date]
        pos['max_fav_px'] = max(pos['max_fav_px'], row['high'])
        pos['max_adv_px'] = min(pos['max_adv_px'], row['low'])
        atr_daily = row['atr'] if pd.notna(row['atr']) and row['atr'] > 0 else pos['initial_atr']

        if row['low'] <= pos['virtual_sl']:
            exit_px = min(row['open'], pos['virtual_sl']) - SLIP
            raw_r = (exit_px - pos['entry_px']) / pos['initial_risk_per_share']
            dollar_pnl = (exit_px - pos['entry_px']) * pos['qty']
            realized_pnl_dollars += dollar_pnl
            closed_trades.append({
                "symbol": sym, "entry_date": pos['entry_date'], "exit_date": curr_date,
                "entry_px": pos['entry_px'], "exit_px": exit_px, "raw_r": raw_r, "dollar_pnl": dollar_pnl,
                "exit_reason": "ATR_STOP_HIT", "mae_r": max(0, (pos['entry_px'] - pos['max_adv_px']) / pos['initial_risk_per_share']),
                "mfe_r": max(0, (pos['max_fav_px'] - pos['entry_px']) / pos['initial_risk_per_share'])
            })
            del open_positions[sym]
            continue

        current_mfe_r = (row['high'] - pos['entry_px']) / pos['initial_risk_per_share']
        if current_mfe_r >= 1.5 and pos['tier'] < 1:
            pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
            pos['tier'] = 1
        
        if pos['tier'] >= 1:
            trail_stop = row['ema10'] - (0.5 * atr_daily)
            pos['virtual_sl'] = max(pos['virtual_sl'], trail_stop)

    # 2. Portfolio Accounting
    unrealized_pnl_dollars = 0.0
    active_open_risk_dollars = 0.0
    for sym, pos in open_positions.items():
        df_d = fleet_daily.get(sym)
        if df_d is not None and curr_date in df_d.index:
            curr_px = df_d.loc[curr_date]['close']
            unrealized_pnl_dollars += (curr_px - pos['entry_px']) * pos['qty']
            active_open_risk_dollars += pos['qty'] * max(0.0, pos['entry_px'] - pos['virtual_sl'])

    current_equity = INITIAL_NAV + realized_pnl_dollars + unrealized_pnl_dollars
    equity_curve.append({"date": curr_date, "equity": current_equity})

    # 3. Daily Breakout Candidate Scan & Dynamic Sizing
    if macro_bull and len(open_positions) < MAX_POSITIONS:
        portfolio_cap = current_equity * MAX_PORTFOLIO_RISK_PCT
        rem_portfolio_risk = portfolio_cap - active_open_risk_dollars
        if rem_portfolio_risk <= 0: continue

        candidates = []
        for sym, df_d in fleet_daily.items():
            if sym in open_positions or curr_date not in df_d.index: continue
            idx = df_d.index.get_loc(curr_date)
            if idx < 30: continue

            row = df_d.iloc[idx]
            prev = df_d.iloc[idx - 1]
            atr_val = row['atr']
            if pd.isna(atr_val) or atr_val <= 0: continue

            if not get_pit_stage2(sym, curr_date): continue

            breakout = (row['close'] > row['donchian_high']) and (prev['close'] <= prev['donchian_high'])
            vol_exp = row['rvol'] > 1.25
            trend = row['close'] > row['ema21']

            if breakout and vol_exp and trend:
                score = (row['rvol'] * 25.0) + (row['adx'] * 1.5)
                candidates.append((sym, row, atr_val, score))

        candidates.sort(key=lambda x: x[3], reverse=True)

        for sym, row, atr_val, score in candidates:
            if len(open_positions) >= MAX_POSITIONS: break

            entry_px = row['close'] + SLIP + SPREAD
            initial_sl = entry_px - (2.0 * atr_val)
            risk_per_share = max(entry_px - initial_sl, 0.01)

            # Dynamic Sizing based on RVOL and ADX
            rvol_mult = min(max(row['rvol'] / 1.5, 0.8), 1.6)
            adx_mult = min(max(row['adx'] / 25.0, 0.8), 1.5)
            dynamic_risk_pct = BASE_RISK_PCT * rvol_mult * adx_mult

            allowed_risk = min(current_equity * dynamic_risk_pct, rem_portfolio_risk)
            qty = int(allowed_risk / risk_per_share)
            if qty <= 0: continue

            open_positions[sym] = {
                "entry_date": curr_date, "entry_px": entry_px, "qty": qty,
                "initial_sl": initial_sl, "virtual_sl": initial_sl, "initial_risk_per_share": risk_per_share,
                "tier": 0, "max_fav_px": entry_px, "max_adv_px": entry_px, "initial_atr": atr_val
            }
            rem_portfolio_risk -= (qty * risk_per_share)

# Mark to market final date
for sym, pos in open_positions.items():
    df_d = fleet_daily.get(sym)
    last_row = df_d.iloc[-1]
    exit_px = last_row['close'] - SLIP
    raw_r = (exit_px - pos['entry_px']) / pos['initial_risk_per_share']
    dollar_pnl = (exit_px - pos['entry_px']) * pos['qty']
    realized_pnl_dollars += dollar_pnl
    closed_trades.append({
        "symbol": sym, "entry_date": pos['entry_date'], "exit_date": last_row.name,
        "entry_px": pos['entry_px'], "exit_px": exit_px, "raw_r": raw_r, "dollar_pnl": dollar_pnl,
        "exit_reason": "MARK_TO_MARKET", "mae_r": max(0, (pos['entry_px'] - pos['max_adv_px']) / pos['initial_risk_per_share']),
        "mfe_r": max(0, (pos['max_fav_px'] - pos['entry_px']) / pos['initial_risk_per_share'])
    })

tdf = pd.DataFrame(closed_trades)
eq_df = pd.DataFrame(equity_curve).set_index('date')

if len(tdf) == 0:
    print("❌ Zero trades triggered.")
    exit()

r = tdf['raw_r'].values
wins, losses = r[r > 0], r[r <= 0]
cum = np.cumsum(r)
max_dd_r = np.max(np.maximum.accumulate(cum) - cum) if len(cum) > 0 else 0.0
wr = (len(wins) / len(r)) * 100.0
pf = abs(wins.sum() / (losses.sum() + 1e-9))
exp = r.mean()

eq_series = eq_df['equity']
eq_peak = eq_series.cummax()
eq_dd_dollars = eq_peak - eq_series
max_eq_dd_pct = (eq_dd_dollars / eq_peak).max() * 100.0
total_return_pct = ((eq_series.iloc[-1] - INITIAL_NAV) / INITIAL_NAV) * 100.0
days = (pd.to_datetime(END_DATE) - pd.to_datetime(START_DATE)).days
annualized_return_pct = total_return_pct * (365.25 / days)
calmar = annualized_return_pct / max_eq_dd_pct if max_eq_dd_pct > 0 else 0.0

print("\n" + "=" * 125)
print(" 🏆 XLK AGGRESSIVE GROWTH SWING RESULTS (2023-2024 BULL CYCLE)")
print("=" * 125)
print(f"• Total Trades (N)           : {len(tdf)}")
print(f"• Win Rate (%)               : {wr:.1f}%")
print(f"• Total Net Harvest (R)      : {r.sum():+.2f}R")
print(f"• Profit Factor (PF)         : {pf:.2f}")
print(f"• Expectancy per Trade       : {exp:+.2f}R/trade")
print(f"• Portfolio Net PnL          : +${eq_series.iloc[-1]-INITIAL_NAV:,.2f} ({total_return_pct:+.2f}%)")
print(f"• Equity Max Drawdown (%)    : {max_eq_dd_pct:.2f}%")
print(f"• Annualized Calmar Ratio    : {calmar:.2f}")
print(f"• Mean MAE / MFE             : {tdf['mae_r'].mean():.2f}R / {tdf['mfe_r'].mean():.2f}R")
print("-" * 125)

print("\n📈 Ticker Attribution Breakdown:")
tick_grp = tdf.groupby('symbol').agg(
    Trades=('raw_r', 'count'),
    WinRate=('raw_r', lambda x: f"{(x > 0).mean()*100:.1f}%"),
    Net_R=('raw_r', 'sum'),
    Dollar_PnL=('dollar_pnl', 'sum'),
    PF=('raw_r', lambda x: abs(x[x > 0].sum() / (x[x <= 0].sum() + 1e-9)))
).reset_index().sort_values('Net_R', ascending=False)
print(tick_grp.to_string(index=False))
print("=" * 125)
