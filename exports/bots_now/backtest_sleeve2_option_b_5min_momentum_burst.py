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

TRI_SECTOR_UNIVERSE = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM"],
    "XLF": ["JPM", "BAC", "WFC", "GS", "MS", "AXP"],
    "XLI": ["GE", "CAT", "UNP", "HON", "RTX", "ETN"]
}
TICKER_SECTOR = {sym: sec for sec, tickers in TRI_SECTOR_UNIVERSE.items() for sym in tickers}

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
    return dx.rolling(n).mean(), pos_di, neg_di

def calculate_rth_vwap(df):
    typical_px = (df['high'] + df['low'] + df['close']) / 3.0
    pv = typical_px * df['volume']
    session_date = df['time'].dt.date
    cum_pv = pv.groupby(session_date).cumsum()
    cum_v = df['volume'].groupby(session_date).cumsum()
    return cum_pv / (cum_v + 1e-9)

print("=" * 125)
print(" 🚀 OPTION B: 15M SIGNAL / 5M EXECUTION MOMENTUM BURST SIMULATOR")
print("=" * 125)
print(f"📥 Fetching SPY Daily, Weekly Stage-2, Fleet 15m Signals, and Fleet 5m Management Bars...")

# 1. SPY Macro (D-1 Shifted)
spy_df = get_alpaca_bars("SPY", "1Day", start_str=WARMUP_DATE, end_str=END_DATE)
spy_df['ema20'] = spy_df['close'].ewm(span=20, adjust=False).mean()
spy_df['sma50'] = spy_df['close'].rolling(50).mean()
spy_df['macro_bull'] = (spy_df['close'] > spy_df['ema20']) & (spy_df['ema20'] > spy_df['sma50'])
spy_df['macro_bull_pit'] = spy_df['macro_bull'].shift(1).fillna(False)
spy_df['date'] = spy_df['time'].dt.date
spy_macro_map = dict(zip(spy_df['date'], spy_df['macro_bull_pit']))

# 2. Weekly Stage-2 Data (Completed Week Shifted)
weekly_data = {}
all_symbols = [s for tickers in TRI_SECTOR_UNIVERSE.values() for s in tickers]
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

# 3. 15m Signal Bars (RTH Only)
fleet_15m = {}
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
    tr = pd.concat([df_15['high'] - df_15['low'], 
                    (df_15['high'] - df_15['close'].shift(1)).abs(), 
                    (df_15['low'] - df_15['close'].shift(1)).abs()], axis=1).max(axis=1)
    df_15['atr'] = tr.rolling(14).mean()
    df_15['vol_sma20'] = df_15['volume'].rolling(20).mean()
    df_15['rvol'] = df_15['volume'] / (df_15['vol_sma20'] + 1e-9)
    df_15['vwap'] = calculate_rth_vwap(df_15)
    df_15['adx'], df_15['pos_di'], df_15['neg_di'] = compute_adx(df_15)
    df_15['donchian_high'] = df_15['high'].rolling(20).max().shift(1)
    df_15['swing_low10'] = df_15['low'].rolling(10).min().shift(1)
    df_15.set_index('time', inplace=True)
    fleet_15m[sym] = df_15
    time.sleep(0.01)

# 4. 5m Management Bars (RTH Only)
fleet_5m = {}
for sym in all_symbols:
    df_raw = get_alpaca_bars(sym, "5Min", start_str=START_DATE, end_str=END_DATE)
    if df_raw is None or len(df_raw) < 50: continue
    df_5 = df_raw[(df_raw['time'].dt.time >= datetime.time(9, 30)) & 
                  (df_raw['time'].dt.time <= datetime.time(16, 0))].copy().reset_index(drop=True)
    if len(df_5) < 50: continue
    df_5['ema21'] = df_5['close'].ewm(span=21, adjust=False).mean()
    tr = pd.concat([df_5['high'] - df_5['low'], 
                    (df_5['high'] - df_5['close'].shift(1)).abs(), 
                    (df_5['low'] - df_5['close'].shift(1)).abs()], axis=1).max(axis=1)
    df_5['atr'] = tr.rolling(14).mean()
    df_5.set_index('time', inplace=True)
    fleet_5m[sym] = df_5
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

def get_pit_breadth_score(sec, current_time):
    tickers = TRI_SECTOR_UNIVERSE.get(sec, [])
    return sum(1 for s in tickers if get_pit_stage2(s, current_time))

def run_option_b_simulation(thrust_bars=4, required_mfe=0.50):
    all_5m_timestamps = sorted(list(set.union(*[set(df.index) for df in fleet_5m.values()])))
    
    portfolio_nav = INITIAL_NAV
    realized_pnl_dollars = 0.0
    open_positions = {}
    closed_trades = []
    equity_curve = []

    for ts in all_5m_timestamps:
        curr_date = ts.date()
        curr_time = ts.time()
        is_hard_flat_time = (curr_time >= datetime.time(15, 50))
        is_15m_boundary = (ts.minute % 15 == 0) and (curr_time >= datetime.time(10, 0)) and (curr_time <= datetime.time(14, 30))
        macro_bull = spy_macro_map.get(curr_date, False)

        # -------------------------------------------------------------------------
        # 1. 5-MINUTE LIFECYCLE MANAGEMENT OF OPEN POSITIONS
        # -------------------------------------------------------------------------
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            df_5 = fleet_5m.get(sym)
            if df_5 is None or ts not in df_5.index: continue
            
            row_5 = df_5.loc[ts]
            pos['bars_held_5m'] += 1
            pos['max_fav_px'] = max(pos['max_fav_px'], row_5['high'])
            pos['max_adv_px'] = min(pos['max_adv_px'], row_5['low'])
            current_mfe_r = (pos['max_fav_px'] - pos['entry_px']) / pos['initial_risk_per_share']
            atr_5m = row_5['atr'] if pd.notna(row_5['atr']) and row_5['atr'] > 0 else pos['initial_atr_15m']

            # A. 15:50 ET Unconditional Hard Flat
            if is_hard_flat_time:
                exit_px = row_5['close'] - SLIP
                raw_r = (exit_px - pos['entry_px']) / pos['initial_risk_per_share']
                dollar_pnl = (exit_px - pos['entry_px']) * pos['qty']
                realized_pnl_dollars += dollar_pnl
                closed_trades.append({
                    "symbol": sym, "sector": pos['sector'], "entry_time": pos['entry_time'], "exit_time": ts,
                    "entry_px": pos['entry_px'], "exit_px": exit_px, "raw_r": raw_r, "dollar_pnl": dollar_pnl,
                    "exit_reason": "HARD_FLAT_1550", "mae_r": max(0, (pos['entry_px'] - pos['max_adv_px']) / pos['initial_risk_per_share']),
                    "mfe_r": max(0, (pos['max_fav_px'] - pos['entry_px']) / pos['initial_risk_per_share']),
                    "bars_held_5m": pos['bars_held_5m']
                })
                del open_positions[sym]
                continue

            # B. Intraday Stop Loss Hit (5m Resolution)
            if row_5['low'] <= (pos['virtual_sl'] + SPREAD):
                exit_px = pos['virtual_sl'] - SLIP
                raw_r = (exit_px - pos['entry_px']) / pos['initial_risk_per_share']
                dollar_pnl = (exit_px - pos['entry_px']) * pos['qty']
                realized_pnl_dollars += dollar_pnl
                closed_trades.append({
                    "symbol": sym, "sector": pos['sector'], "entry_time": pos['entry_time'], "exit_time": ts,
                    "entry_px": pos['entry_px'], "exit_px": exit_px, "raw_r": raw_r, "dollar_pnl": dollar_pnl,
                    "exit_reason": "STOP_LOSS", "mae_r": max(0, (pos['entry_px'] - pos['max_adv_px']) / pos['initial_risk_per_share']),
                    "mfe_r": max(0, (pos['max_fav_px'] - pos['entry_px']) / pos['initial_risk_per_share']),
                    "bars_held_5m": pos['bars_held_5m']
                })
                del open_positions[sym]
                continue

            # C. Fast Momentum Scratch (Velocity Gate)
            if pos['bars_held_5m'] >= thrust_bars and current_mfe_r < required_mfe:
                exit_px = row_5['close'] - SLIP
                raw_r = (exit_px - pos['entry_px']) / pos['initial_risk_per_share']
                dollar_pnl = (exit_px - pos['entry_px']) * pos['qty']
                realized_pnl_dollars += dollar_pnl
                closed_trades.append({
                    "symbol": sym, "sector": pos['sector'], "entry_time": pos['entry_time'], "exit_time": ts,
                    "entry_px": pos['entry_px'], "exit_px": exit_px, "raw_r": raw_r, "dollar_pnl": dollar_pnl,
                    "exit_reason": f"VELOCITY_SCRATCH_{thrust_bars}B", "mae_r": max(0, (pos['entry_px'] - pos['max_adv_px']) / pos['initial_risk_per_share']),
                    "mfe_r": max(0, (pos['max_fav_px'] - pos['entry_px']) / pos['initial_risk_per_share']),
                    "bars_held_5m": pos['bars_held_5m']
                })
                del open_positions[sym]
                continue

            # D. Asymmetric Ratchets (+1.0R -> BE+0.05ATR, +2.2R -> Dynamic 5m EMA21 Trail)
            if row_5['high'] >= pos['target_tp1'] and pos['tier'] < 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'] + (0.05 * pos['initial_atr_15m']))
                pos['tier'] = 1
            if row_5['high'] >= pos['target_tp2'] and pos['tier'] < 2:
                pos['virtual_sl'] = max(pos['virtual_sl'], row_5['ema21'] - (0.8 * atr_5m))
                pos['tier'] = 2
            elif pos['tier'] == 2:
                pos['virtual_sl'] = max(pos['virtual_sl'], row_5['ema21'] - (0.8 * atr_5m))

        # -------------------------------------------------------------------------
        # 2. CONTINUOUS 5-MINUTE MARK-TO-MARKET PORTFOLIO ACCOUNTING
        # -------------------------------------------------------------------------
        unrealized_pnl_dollars = 0.0
        active_open_risk_dollars = 0.0
        for sym, pos in open_positions.items():
            df_5 = fleet_5m.get(sym)
            if df_5 is not None and ts in df_5.index:
                curr_px = df_5.loc[ts]['close']
                unrealized_pnl_dollars += (curr_px - pos['entry_px']) * pos['qty']
                active_open_risk_dollars += pos['qty'] * max(0.0, pos['entry_px'] - pos['virtual_sl'])

        current_equity = INITIAL_NAV + realized_pnl_dollars + unrealized_pnl_dollars
        equity_curve.append({
            "time": ts, "equity": current_equity, "realized": realized_pnl_dollars,
            "unrealized": unrealized_pnl_dollars, "open_risk": active_open_risk_dollars,
            "slots_occupied": len(open_positions)
        })

        # -------------------------------------------------------------------------
        # 3. 15-MINUTE CANDIDATE DISCOVERY & SYNCHRONOUS RANKING
        # -------------------------------------------------------------------------
        if is_15m_boundary and macro_bull and len(open_positions) < MAX_POSITIONS:
            portfolio_cap = current_equity * MAX_PORTFOLIO_RISK_PCT
            sector_cap = current_equity * MAX_SECTOR_RISK_PCT
            rem_portfolio_risk = portfolio_cap - active_open_risk_dollars
            if rem_portfolio_risk <= 0: continue

            candidates = []
            for sym, df_15 in fleet_15m.items():
                if sym in open_positions or ts not in df_15.index: continue
                idx_15 = df_15.index.get_loc(ts)
                if idx_15 < 40: continue

                row_15 = df_15.iloc[idx_15]
                prev_15 = df_15.iloc[idx_15 - 1]
                atr_15 = row_15['atr']
                if pd.isna(atr_15) or atr_15 <= 0: continue

                sec = TICKER_SECTOR.get(sym)
                breadth_score = get_pit_breadth_score(sec, ts)
                if breadth_score < 4 or not get_pit_stage2(sym, ts): continue

                # True 15m Confluence Breakout
                breakout = (row_15['close'] > row_15['donchian_high']) and (prev_15['close'] <= prev_15['donchian_high'])
                vol_exp = row_15['volume'] > (1.25 * row_15['vol_sma20'])
                momentum = (row_15['ema9'] > row_15['ema21'] > row_15['bb_mid']) and \
                           (row_15['adx'] >= 20.0) and (row_15['pos_di'] > row_15['neg_di']) and (row_15['close'] >= row_15['vwap'])

                if breakout and vol_exp and momentum:
                    score = (row_15['rvol'] * 25.0) + (row_15['adx'] * 1.5) + ((row_15['pos_di'] - row_15['neg_di']) * 1.0) + (breadth_score * 10.0)
                    candidates.append((sym, sec, row_15, atr_15, score))

            # Synchronous Ranking: Highest Quality Opportunities Fill Slots First
            candidates.sort(key=lambda x: x[4], reverse=True)

            for sym, sec, row_15, atr_15, score in candidates:
                if len(open_positions) >= MAX_POSITIONS: break
                current_sec_risk = sum(p['qty'] * max(0.0, p['entry_px'] - p['virtual_sl']) 
                                       for p in open_positions.values() if p['sector'] == sec)
                rem_sec_risk = sector_cap - current_sec_risk
                if rem_sec_risk <= 0: continue

                entry_px = row_15['close'] + SLIP + SPREAD
                swing_stop = row_15['swing_low10']
                ema_stop = row_15['ema21'] - (1.0 * atr_15)
                initial_sl = max(swing_stop, ema_stop)
                if initial_sl >= entry_px: initial_sl = entry_px - (1.0 * atr_15)

                risk_per_share = max(entry_px - initial_sl, 0.01)
                allowed_risk = min(current_equity * BASE_RISK_PCT, rem_portfolio_risk, rem_sec_risk)
                qty = int(allowed_risk / risk_per_share)
                if qty <= 0: continue

                open_positions[sym] = {
                    "sector": sec, "entry_time": ts, "entry_px": entry_px, "qty": qty,
                    "initial_sl": initial_sl, "virtual_sl": initial_sl, "initial_risk_per_share": risk_per_share,
                    "target_tp1": entry_px + (1.0 * risk_per_share),
                    "target_tp2": entry_px + (2.2 * risk_per_share),
                    "tier": 0, "max_fav_px": entry_px, "max_adv_px": entry_px,
                    "initial_atr_15m": atr_15, "bars_held_5m": 0
                }
                rem_portfolio_risk -= (qty * risk_per_share)

    tdf = pd.DataFrame(closed_trades)
    eq_df = pd.DataFrame(equity_curve).set_index('time')

    if len(tdf) == 0:
        return {"n": 0, "wr": 0.0, "net_r": 0.0, "pf": 0.0, "exp": 0.0, "calmar": 0.0, "max_eq_dd_pct": 0.0, "harvest_dd": 0.0, "mae": 0.0, "mfe": 0.0, "df": tdf, "eq_df": eq_df}

    r = tdf['raw_r'].values
    wins, losses = r[r > 0], r[r <= 0]
    wr = (len(wins) / len(r)) * 100.0
    pf = abs(wins.sum() / (losses.sum() + 1e-9))
    exp = r.mean()
    mae = tdf['mae_r'].mean()
    mfe = tdf['mfe_r'].mean()

    # Continuous 5-minute Mark-to-Market Equity Metrics
    eq_series = eq_df['equity']
    eq_peak = eq_series.cummax()
    eq_dd_dollars = eq_peak - eq_series
    max_eq_dd_pct = (eq_dd_dollars / eq_peak).max() * 100.0
    total_return_pct = ((eq_series.iloc[-1] - INITIAL_NAV) / INITIAL_NAV) * 100.0
    days = (pd.to_datetime(END_DATE) - pd.to_datetime(START_DATE)).days
    annualized_return_pct = total_return_pct * (365.25 / days)
    calmar = annualized_return_pct / max_eq_dd_pct if max_eq_dd_pct > 0 else 0.0
    
    cum_r = np.cumsum(r)
    max_dd_r = np.max(np.maximum.accumulate(cum_r) - cum_r) if len(cum_r) > 0 else 0.0
    harvest_dd = r.sum() / max_dd_r if max_dd_r > 0 else 0.0

    return {
        "n": len(tdf), "wr": wr, "net_r": r.sum(), "pf": pf, "exp": exp,
        "calmar": calmar, "max_eq_dd_pct": max_eq_dd_pct, "max_dd_r": max_dd_r,
        "harvest_dd": harvest_dd, "mae": mae, "mfe": mfe, "net_dollar": eq_series.iloc[-1] - INITIAL_NAV,
        "df": tdf, "eq_df": eq_df
    }

if __name__ == "__main__":
    print("\n" + "=" * 135)
    print(" 🔬 OPTION B: DUAL-TIMEFRAME (15M SIGNAL / 5M MANAGEMENT) CAUSAL MATRIX")
    print("=" * 135)
    print(f"{'Variant / Thrust Window':<42} | {'Trades':<7} | {'Win Rate':<9} | {'Net R':<9} | {'PF':<6} | {'Exp/Tr':<10} | {'Eq MaxDD':<9} | {'Harvest/DD':<10} | {'Calmar':<7} | {'MAE/MFE'}")
    print("-" * 135)

    matrix_variants = [
        ("B1. 4x5m (20 min) / Req MFE +0.50R", 4, 0.50),
        ("B2. 6x5m (30 min) / Req MFE +0.50R", 6, 0.50),
        ("B3. 8x5m (40 min) / Req MFE +0.50R", 8, 0.50),
        ("B4. 6x5m (30 min) / Req MFE +0.75R", 6, 0.75),
    ]

    for name, t_bars, req_mfe in matrix_variants:
        res = run_option_b_simulation(thrust_bars=t_bars, required_mfe=req_mfe)
        print(f"{name:<42} | {res['n']:^7} | {res['wr']:^9.1f}% | {res['net_r']:^+9.2f}R | {res['pf']:^6.2f} | {res['exp']:^+10.2f}R/tr | {res['max_eq_dd_pct']:^8.2f}% | {res['harvest_dd']:^10.2f} | {res['calmar']:^7.2f} | {res['mae']:.2f}R / {res['mfe']:.2f}R")

    print("=" * 135)

    best_variant = run_option_b_simulation(thrust_bars=6, required_mfe=0.50)
    b_df = best_variant['df']

    print("\n📊 Sector Attribution Breakdown (Variant B2: 6x5m @ +0.50R):")
    sec_grp = b_df.groupby('sector').agg(
        Trades=('raw_r', 'count'),
        WinRate=('raw_r', lambda x: f"{(x > 0).mean()*100:.1f}%"),
        Net_R=('raw_r', 'sum'),
        Dollar_PnL=('dollar_pnl', 'sum'),
        PF=('raw_r', lambda x: abs(x[x > 0].sum() / (x[x <= 0].sum() + 1e-9)))
    ).reset_index()
    print(sec_grp.to_string(index=False))

    print("\n📈 Ticker Attribution Breakdown (Top 10):")
    tick_grp = b_df.groupby('symbol').agg(
        Trades=('raw_r', 'count'),
        WinRate=('raw_r', lambda x: f"{(x > 0).mean()*100:.1f}%"),
        Net_R=('raw_r', 'sum'),
        Dollar_PnL=('dollar_pnl', 'sum'),
        PF=('raw_r', lambda x: abs(x[x > 0].sum() / (x[x <= 0].sum() + 1e-9)))
    ).reset_index().sort_values('Net_R', ascending=False)
    print(tick_grp.head(10).to_string(index=False))
    print("=" * 135)
