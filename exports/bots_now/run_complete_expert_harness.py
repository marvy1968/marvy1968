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
print(" 🔬 COMPLETE 10-EXPERT LONG STRATEGY BENCHMARK ENGINE")
print("=" * 135)
print("📥 Fetching master daily and weekly data...")

master_daily = {}
weekly_data = {}
all_syms = list(set(EXPERT_UNIVERSE + ["SPY"]))

for sym in all_syms:
    df_d = get_alpaca_bars(sym, "1Day", start_str=WARMUP_START, end_str=END_DATE)
    if df_d is not None and len(df_d) > 60:
        df_d['ema20'] = df_d['close'].ewm(span=20, adjust=False).mean()
        df_d['ema50'] = df_d['close'].ewm(span=50, adjust=False).mean()
        df_d['ema100'] = df_d['close'].ewm(span=100, adjust=False).mean()
        df_d['sma50'] = df_d['close'].rolling(50).mean()
        df_d['sma200'] = df_d['close'].rolling(200).mean()
        df_d['sma200_slope'] = df_d['sma200'].diff(20)
        df_d['donchian_20'] = df_d['high'].rolling(20).max().shift(1)
        df_d['donchian_55'] = df_d['high'].rolling(55).max().shift(1)
        df_d['box_high'] = df_d['high'].rolling(30).max().shift(1)
        tr = pd.concat([df_d['high'] - df_d['low'], (df_d['high'] - df_d['close'].shift(1)).abs(), (df_d['low'] - df_d['close'].shift(1)).abs()], axis=1).max(axis=1)
        df_d['atr'] = tr.rolling(14).mean()
        df_d['atr_mean20'] = df_d['atr'].rolling(20).mean()
        df_d['adx'] = compute_adx(df_d)
        df_d['vol_sma50'] = df_d['volume'].rolling(50).mean()
        df_d['rvol'] = df_d['volume'] / (df_d['vol_sma50'] + 1e-9)
        df_d['rsi'] = 100 - (100 / (1 + (df_d['close'].diff().clip(lower=0).rolling(14).mean() / (df_d['close'].diff().clip(upper=0).abs().rolling(14).mean() + 1e-9))))
        df_d['date'] = df_d['time'].dt.date
        df_d.set_index('date', inplace=True)
        master_daily[sym] = df_d

    df_w = get_alpaca_bars(sym, "1Week", start_str=WARMUP_START, end_str=END_DATE)
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

spy_df = master_daily["SPY"]
spy_df['macro_bull'] = (spy_df['close'] > spy_df['ema20']) & (spy_df['ema20'] > spy_df['sma50'])
spy_df['macro_bull_pit'] = spy_df['macro_bull'].shift(1).infer_objects(copy=False).fillna(False)
spy_macro_map = dict(zip(spy_df.index, spy_df['macro_bull_pit']))

start_dt = datetime.datetime.strptime(START_DATE, "%Y-%m-%d").date()
end_dt = datetime.datetime.strptime(END_DATE, "%Y-%m-%d").date()
dates = sorted(list(set.union(*[set(master_daily[s].index) for s in EXPERT_UNIVERSE if s in master_daily])))
dates = [d for d in dates if start_dt <= d <= end_dt]

def evaluate_expert(expert_name, sym, curr_date, df_d, idx):
    if idx < 200: return False
    row = df_d.iloc[idx]
    p = row['close']
    
    if expert_name == "WEINSTEIN":
        df_w = weekly_data.get(sym)
        if df_w is None: return False
        past_w = df_w[df_w['date'] <= curr_date]
        if len(past_w) < 32: return False
        w_row = past_w.iloc[-1]
        return (w_row['close_pit'] > w_row['sma30w_pit'] and w_row['sma30w_slope_pit'] > 0 and p > row['donchian_20'])

    elif expert_name == "MINERVINI":
        trend = (p > row['sma50']) and (row['sma50'] > row['sma200']) and (row['sma200_slope'] > 0)
        vcp = row['atr'] <= (1.05 * row['atr_mean20'])
        breakout = (p > row['donchian_20']) and (row['rvol'] > 1.25)
        return trend and vcp and breakout

    elif expert_name == "ONEIL":
        near_high = p >= (0.85 * df_d['high'].iloc[max(0, idx-250):idx].max())
        return (p > row['sma50']) and near_high and (p > row['donchian_20']) and (row['rvol'] > 1.5)

    elif expert_name == "DARVAS":
        box_top = row['box_high']
        consolidating = (df_d['high'].iloc[idx-30:idx].max() - df_d['low'].iloc[idx-30:idx].min()) / p < 0.20
        return consolidating and (p > box_top) and (row['rvol'] > 1.2)

    elif expert_name == "DONCHIAN":
        return p > row['donchian_55']

    elif expert_name == "SEYKOTA":
        return (p > row['ema50']) and (row['ema50'] > row['ema100']) and (row['adx'] > 25) and (p > row['donchian_20'])

    elif expert_name == "ELDER": # Triple Screen Pullback
        weekly_bull = row['ema20'] > row['ema50']
        pullback = (row['rsi'] < 45) and (p > row['ema50'])
        return weekly_bull and pullback and (p > df_d['close'].iloc[idx-1])

    elif expert_name == "RASCHKE": # Tactical Momentum
        trend = p > row['sma200']
        oversold = row['rsi'] < 35
        reversal = (p > row['open']) and (p > df_d['close'].iloc[idx-1])
        return trend and oversold and reversal

    elif expert_name == "WILLIAMS": # Volatility Breakout
        prev_range = df_d['high'].iloc[idx-1] - df_d['low'].iloc[idx-1]
        trigger = df_d['open'].iloc[idx] + (0.5 * prev_range)
        return (p > row['sma200']) and (p >= trigger) and (row['rvol'] > 1.1)

    elif expert_name == "ZWEIG": # Breadth / Regime Filtered Momentum
        return (p > row['sma200']) and (row['rvol'] > 1.3) and spy_macro_map.get(curr_date, False)

    return False

def run_expert_backtest(expert_name):
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

            if row['low'] <= pos['virtual_sl']:
                exit_px = min(row['open'], pos['virtual_sl']) - SLIP
                raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
                pnl = (exit_px - pos['entry_px']) * pos['qty']
                realized_pnl += pnl
                closed_trades.append({"symbol": sym, "raw_r": raw_r, "dollar_pnl": pnl})
                del open_longs[sym]
                continue

            mfe_r = (row['high'] - pos['entry_px']) / pos['risk_prs']
            if mfe_r >= 1.5 and pos['tier'] < 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                pos['tier'] = 1
            if pos['tier'] >= 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], row['ema50'] - (0.5 * atr_d))

        unrealized = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_longs.items() if curr_date in master_daily[s].index)
        current_eq = INITIAL_NAV + realized_pnl + unrealized
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

                if evaluate_expert(expert_name, sym, curr_date, df_d, idx):
                    entry_px = row['close'] + SLIP + SPREAD
                    initial_sl = entry_px - (2.0 * atr)
                    rps = max(entry_px - initial_sl, 0.01)
                    qty = int((current_eq * 0.01) / rps)
                    if qty > 0:
                        open_longs[sym] = {
                            "entry_date": curr_date, "entry_px": entry_px, "qty": qty,
                            "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "initial_atr": atr
                        }
                        break

    for sym, pos in list(open_longs.items()):
        df_d = master_daily[sym]
        window_df = df_d.loc[(df_d.index >= start_dt) & (df_d.index <= end_dt)]
        if window_df.empty: continue
        last_row = window_df.iloc[-1]
        exit_px = last_row['close'] - SLIP
        raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
        pnl = (exit_px - pos['entry_px']) * pos['qty']
        realized_pnl += pnl
        closed_trades.append({"symbol": sym, "raw_r": raw_r, "dollar_pnl": pnl})
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
    win_rate = (len(wins) / len(r_arr)) * 100.0 if len(r_arr) > 0 else 0.0

    return {
        "expert": expert_name, "cagr": cagr, "max_dd": max_dd,
        "calmar": calmar, "pf": pf, "win_rate": win_rate, "trades": len(tdf), "ending_nav": eq_df['equity'].iloc[-1]
    }

EXPERTS = ["WEINSTEIN", "MINERVINI", "ONEIL", "DARVAS", "DONCHIAN", "SEYKOTA", "ELDER", "RASCHKE", "WILLIAMS", "ZWEIG"]

print("\n" + "=" * 115)
print(f"{'Expert Module':<20} | {'CAGR':<7} | {'Max DD':<7} | {'Calmar':<7} | {'PF':<5} | {'Win Rate':<9} | {'Trades':<7} | {'Terminal NAV'}")
print("-" * 115)

for exp in EXPERTS:
    res = run_expert_backtest(exp)
    print(f"{res['expert']:<20} | {res['cagr']:>5.2f}% | {res['max_dd']:>5.2f}% | {res['calmar']:>6.2f} | {res['pf']:>4.2f} | {res['win_rate']:>7.2f}% | {res['trades']:>6} | ${res['ending_nav']:>12,.2f}")

print("=" * 115)
