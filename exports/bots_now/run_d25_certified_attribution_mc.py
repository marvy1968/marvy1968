import os, time, datetime, json
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

STATE_FILE = "/home/moonrider1968/bots/governor_state.json"

XLK_LONG_UNIVERSE = ["MSFT", "NVDA", "AVGO", "AMD", "CRM"]
STAGE4_SHORT_FLEET = [
    "AAPL", "MSFT", "NVDA", "AVGO", "AMD", "CRM", "TSLA", "AMZN", "GOOGL", "META",
    "JPM", "BAC", "WFC", "GS", "MS", "AXP",
    "CAT", "GE", "UNP", "HON", "RTX", "ETN"
]
DONCHIAN_SATELLITE_UNIVERSE = [
    "MSFT", "NVDA", "AVGO", "AMD", "CRM", "TSLA", "AMZN", "GOOGL", 
    "META", "AAPL", "NFLX", "SNOW", "PLTR", "DDOG", "CRWD", "NET", "PANW"
]

ALL_SYMBOLS = sorted(list(set(XLK_LONG_UNIVERSE + STAGE4_SHORT_FLEET + DONCHIAN_SATELLITE_UNIVERSE + ["SPY"])))

START_DATE = "2017-01-01"
END_DATE = "2026-08-20"
WARMUP_START = "2016-01-01"
INITIAL_NAV = 100000.0
SLIP, SPREAD = 0.03, 0.02
MAX_POSITIONS = 3

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
print(" 🏛️ B + D25 CERTIFIED ATTRIBUTION & BLOCK-BOOTSTRAP MONTE CARLO HARNESS")
print("=" * 135)
print("📥 Fetching master daily and weekly data...")

master_daily = {}
for sym in ALL_SYMBOLS:
    df_d = get_alpaca_bars(sym, "1Day", start_str=WARMUP_START, end_str=END_DATE)
    if df_d is not None and len(df_d) > 60:
        df_d['ema20'] = df_d['close'].ewm(span=20, adjust=False).mean()
        df_d['sma50'] = df_d['close'].rolling(50).mean()
        df_d['ema10'] = df_d['close'].ewm(span=10, adjust=False).mean()
        df_d['ema21'] = df_d['close'].ewm(span=21, adjust=False).mean()
        df_d['donchian_high'] = df_d['high'].rolling(20).max().shift(1)
        df_d['donchian_55_d3'] = df_d['high'].rolling(55).max().shift(1)
        
        # Volume & Volatility features for validated core
        vol_sma = df_d['volume'].rolling(20).mean()
        df_d['rvol'] = df_d['volume'] / vol_sma
        
        # ATR
        tr = pd.concat([df_d['high'] - df_d['low'], (df_d['high'] - df_d['close'].shift(1)).abs(), (df_d['low'] - df_d['close'].shift(1)).abs()], axis=1).max(axis=1)
        df_d['atr'] = tr.rolling(14).mean()
        
        # ADX Approximation
        plus_dm = df_d['high'].diff()
        minus_dm = df_d['low'].diff()
        plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
        minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)
        tr14 = tr.rolling(14).sum()
        plus_di = 100 * pd.Series(plus_dm).rolling(14).sum() / tr14
        minus_di = 100 * pd.Series(minus_dm).rolling(14).sum() / tr14
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
        df_d['adx'] = pd.Series(dx).rolling(14).mean()

        df_d['date'] = df_d['time'].dt.date
        df_d.set_index('date', inplace=True)
        master_daily[sym] = df_d
    time.sleep(0.01)

# Weekly data for PIT Stage 2/4 checks
weekly_data = {}
for sym in list(set(XLK_LONG_UNIVERSE + STAGE4_SHORT_FLEET)):
    df_w = get_alpaca_bars(sym, "1Week", start_str=WARMUP_START, end_str=END_DATE)
    if df_w is not None and len(df_w) > 35:
        df_w['sma30w'] = df_w['close'].rolling(30).mean()
        df_w['sma30w_slope'] = df_w['sma30w'].diff(3)
        df_w['prev_20w_high'] = df_w['high'].rolling(20).max().shift(1)
        df_w['close_pit'] = df_w['close'].shift(1)
        df_w['sma30w_pit'] = df_w['sma30w'].shift(1)
        df_w['sma30w_slope_pit'] = df_w['sma30w_slope'].shift(1)
        df_w['prev_20w_high_pit'] = df_w['prev_20w_high'].shift(1)
        df_w['date'] = df_w['time'].dt.date
        df_w.set_index('date', inplace=True)
        weekly_data[sym] = df_w

def get_pit_stage2(sym, current_date):
    df_w = weekly_data.get(sym)
    if df_w is None: return False
    past_w = df_w[df_w.index <= current_date]
    if len(past_w) < 32: return False
    row = past_w.iloc[-1]
    if pd.isna(row['sma30w_pit']) or pd.isna(row['sma30w_slope_pit']): return False
    return (row['close_pit'] > row['sma30w_pit'] and row['sma30w_slope_pit'] > 0 and row['close_pit'] >= (0.95 * row['prev_20w_high_pit']))

def get_pit_stage4(sym, current_date):
    df_w = weekly_data.get(sym)
    if df_w is None: return False
    past_w = df_w[df_w.index <= current_date]
    if len(past_w) < 30: return False
    row = past_w.iloc[-1]
    if pd.isna(row['sma30w_pit']) or pd.isna(row['sma30w_slope_pit']): return False
    return (row['close_pit'] < row['sma30w_pit'] and row['sma30w_slope_pit'] < 0)

spy_df = master_daily["SPY"]
spy_df['macro_bull'] = (spy_df['close'] > spy_df['ema20']) & (spy_df['ema20'] > spy_df['sma50'])
spy_df['macro_bull_pit'] = spy_df['macro_bull'].shift(1).fillna(False)
spy_macro_map = dict(zip(spy_df.index, spy_df['macro_bull_pit']))

start_dt = datetime.datetime.strptime(START_DATE, "%Y-%m-%d").date()
end_dt = datetime.datetime.strptime(END_DATE, "%Y-%m-%d").date()
dates = sorted(list(set.union(*[set(master_daily[s].index) for s in ALL_SYMBOLS if s in master_daily])))
dates = [d for d in dates if start_dt <= d <= end_dt]

def get_rolling_corr(sym1, sym2, curr_date, window=60):
    df1 = master_daily.get(sym1)
    df2 = master_daily.get(sym2)
    if df1 is None or df2 is None: return 0.0
    s1 = df1.loc[:curr_date, 'close'].pct_change().dropna().tail(window)
    s2 = df2.loc[:curr_date, 'close'].pct_change().dropna().tail(window)
    common = pd.concat([s1, s2], axis=1).dropna()
    if len(common) < 20: return 0.0
    return float(common.corr().iloc[0, 1])

def run_adaptive_simulation(donchian_risk_pct=0.0):
    realized_pnl = 0.0
    open_longs = {}
    open_donch_longs = {}
    closed_trades = []
    daily_records = []
    pending_short_orders = []

    high_water = INITIAL_NAV
    active_config = "B"
    transition_log = []

    for curr_date in dates:
        macro_bull = spy_macro_map.get(curr_date, False)

        if active_config == "B":
            long_risk, short_risk, max_open_risk = 0.0100, 0.0050, 0.0300
        else:
            long_risk, short_risk, max_open_risk = 0.0075, 0.0025, 0.0225

        # 1. Short Orders
        if not macro_bull and len(pending_short_orders) > 0 and len(open_longs) == 0:
            for order in pending_short_orders:
                sym = order['sym']
                df_d = master_daily.get(sym)
                if df_d is None or curr_date not in df_d.index: continue
                row = df_d.loc[curr_date]
                limit_px = order['target_entry']
                if row['high'] >= limit_px:
                    entry_px = max(row['open'], limit_px) - SLIP
                    risk_prs = order['risk_prs']
                    initial_sl = entry_px + risk_prs
                    target_px = entry_px - (1.5 * risk_prs)
                    hit_stop = (row['high'] >= initial_sl)
                    hit_target = (row['low'] <= target_px)
                    if hit_stop:
                        exit_px = initial_sl + SLIP
                        raw_r = -1.00
                    elif hit_target:
                        exit_px = target_px
                        raw_r = 1.50
                    else:
                        exit_px = row['close']
                        raw_r = (entry_px - exit_px) / risk_prs

                    current_eq = INITIAL_NAV + realized_pnl + sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_longs.items() if curr_date in master_daily[s].index)
                    qty = int((current_eq * short_risk) / risk_prs)
                    if qty > 0:
                        pnl = (entry_px - exit_px) * qty
                        realized_pnl += pnl
                        closed_trades.append({"sleeve": "STAGE4_SHORT", "symbol": sym, "entry_date": order.get('entry_date', curr_date), "exit_date": curr_date, "raw_r": raw_r, "dollar_pnl": pnl, "days_held": 1})
        pending_short_orders = []

        # 2. Manage XLK Longs
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
                closed_trades.append({"sleeve": "XLK_LONG", "symbol": sym, "entry_date": pos['entry_date'], "exit_date": curr_date, "raw_r": raw_r, "dollar_pnl": pnl, "days_held": (curr_date - pos['entry_date']).days})
                del open_longs[sym]
                continue
            mfe_r = (row['high'] - pos['entry_px']) / pos['risk_prs']
            if mfe_r >= 1.5 and pos['tier'] < 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                pos['tier'] = 1
            if pos['tier'] >= 1:
                pos['virtual_sl'] = max(pos['virtual_sl'], row['ema10'] - (0.5 * atr_d))

        # 2.5 Manage Donchian Satellite
        if donchian_risk_pct > 0.0:
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
                    realized_pnl += pnl
                    closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": pos['entry_date'], "exit_date": curr_date, "raw_r": raw_r, "dollar_pnl": pnl, "days_held": (curr_date - pos['entry_date']).days})
                    del open_donch_longs[sym]
                    continue
                mfe_r = (row['high'] - pos['entry_px']) / pos['risk_prs']
                if mfe_r >= 1.5 and pos['tier'] < 1:
                    pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                    pos['tier'] = 1
                if pos['tier'] >= 1:
                    pos['virtual_sl'] = max(pos['virtual_sl'], row['sma50'] - (0.5 * atr_d))

        # 3. Accounting & Governor
        unrealized_longs = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_longs.items() if curr_date in master_daily[s].index)
        donch_unrealized = 0.0
        if donchian_risk_pct > 0.0:
            donch_unrealized = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_donch_longs.items() if curr_date in master_daily[s].index)
        current_eq = INITIAL_NAV + realized_pnl + unrealized_longs + donch_unrealized

        high_water = max(high_water, current_eq)
        drawdown = (high_water - current_eq) / high_water

        if active_config == "B" and drawdown >= 0.05:
            active_config = "D"
            transition_log.append({"date": str(curr_date), "from": "B", "to": "D", "equity": current_eq, "hwm": high_water, "drawdown": drawdown})
        elif active_config == "D" and current_eq >= high_water * 0.995:
            active_config = "B"
            transition_log.append({"date": str(curr_date), "from": "D", "to": "B", "equity": current_eq, "hwm": high_water, "drawdown": drawdown})

        # 4. Long Entries (Fully Validated Core Logic)
        active_symbols = set(open_longs.keys())
        if macro_bull and len(open_longs) < MAX_POSITIONS:
            current_open_risk = sum(p['qty'] * max(0.0, p['entry_px'] - p['virtual_sl']) for p in open_longs.values())
            rem_risk_dollars = (current_eq * max_open_risk) - current_open_risk
            if rem_risk_dollars > 0:
                cands = []
                for sym in XLK_LONG_UNIVERSE:
                    if sym in active_symbols: continue
                    df_d = master_daily.get(sym)
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
                        cands.append((sym, row, atr, (row['rvol'] * 25.0) + (row['adx'] * 1.5)))
                cands.sort(key=lambda x: x[3], reverse=True)
                for sym, row, atr, score in cands:
                    if len(open_longs) >= MAX_POSITIONS: break
                    entry_px = row['close'] + SLIP + SPREAD
                    initial_sl = entry_px - (2.0 * atr)
                    rps = max(entry_px - initial_sl, 0.01)
                    dyn_risk = long_risk * min(max(row['rvol']/1.5, 0.8), 1.6) * min(max(row['adx']/25.0, 0.8), 1.5)
                    qty = int(min(current_eq * dyn_risk, rem_risk_dollars) / rps)
                    if qty > 0:
                        open_longs[sym] = {"entry_date": curr_date, "entry_px": entry_px, "qty": qty, "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "initial_atr": atr}
                        active_symbols.add(sym)
                        rem_risk_dollars -= (qty * rps)

        # 4.5 Donchian Satellite Entries
        active_donch_risk = donchian_risk_pct if active_config == "B" else (donchian_risk_pct * 0.50)
        if donchian_risk_pct > 0.0 and active_donch_risk > 0 and len(open_donch_longs) < MAX_POSITIONS and macro_bull:
            for sym in DONCHIAN_SATELLITE_UNIVERSE:
                if sym in open_donch_longs: continue
                df_d = master_daily.get(sym)
                if df_d is None or curr_date not in df_d.index: continue
                idx = df_d.index.get_loc(curr_date)
                if idx < 200: continue
                row = df_d.iloc[idx]
                atr = row['atr']
                if pd.isna(atr) or atr <= 0: continue
                if row['close'] > row['donchian_55_d3']:
                    max_corr = 0.0
                    for held_sym in open_donch_longs:
                        c = get_rolling_corr(sym, held_sym, curr_date, window=60)
                        max_corr = max(max_corr, c)
                    if max_corr >= 0.80: continue
                    entry_px = row['close'] + SLIP + SPREAD
                    initial_sl = entry_px - (2.00 * atr)
                    rps = max(entry_px - initial_sl, 0.01)
                    qty = int((current_eq * active_donch_risk) / rps)
                    if qty > 0:
                        open_donch_longs[sym] = {
                            "entry_date": curr_date, "entry_px": entry_px, "qty": qty,
                            "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "initial_atr": atr
                        }
                        break

        # 5. Short Orders
        if not macro_bull and len(open_longs) == 0:
            for sym in STAGE4_SHORT_FLEET:
                df_d = master_daily.get(sym)
                if df_d is None or curr_date not in df_d.index: continue
                idx = df_d.index.get_loc(curr_date)
                if idx < 30: continue
                row = df_d.iloc[idx]
                atr = row['atr']
                if pd.isna(atr) or atr <= 0: continue
                if not get_pit_stage4(sym, curr_date): continue
                if (row['close'] < row['ema20']) and (row['ema20'] < row['sma50']):
                    pending_short_orders.append({"sym": sym, "target_entry": row['ema20'], "risk_prs": 0.75 * atr, "entry_date": curr_date})

        daily_records.append({"date": curr_date, "equity": current_eq, "config": active_config})

    # Final Liquidation of Donchian
    if donchian_risk_pct > 0.0:
        for sym, pos in list(open_donch_longs.items()):
            df_d = master_daily[sym]
            window_df = df_d.loc[(df_d.index >= start_dt) & (df_d.index <= end_dt)]
            if window_df.empty: continue
            last_row = window_df.iloc[-1]
            exit_px = last_row['close'] - SLIP
            raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
            pnl = (exit_px - pos['entry_px']) * pos['qty']
            realized_pnl += pnl
            closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": pos['entry_date'], "exit_date": end_dt, "raw_r": raw_r, "dollar_pnl": pnl, "days_held": (end_dt - pos['entry_date']).days})
            del open_donch_longs[sym]

    eq_df = pd.DataFrame(daily_records).drop_duplicates('date', keep='last').set_index('date')
    return eq_df, pd.DataFrame(closed_trades), transition_log

print("\n--- RUNNING B + D25 REGRESSION VERIFICATION ---")
eq_df, tdf, transitions = run_adaptive_simulation(0.0025)

ending_equity = eq_df['equity'].iloc[-1]
eq_peak = eq_df['equity'].cummax()
max_dd = ((eq_peak - eq_df['equity']) / eq_peak).max() * 100.0
years = (end_dt - start_dt).days / 365.25
cagr = (((ending_equity / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0

print(f"D25 REGRESSION: NAV=${ending_equity:,.2f} CAGR={cagr:.2f}% DD={max_dd:.2f}%")
assert abs(cagr - 8.60) <= 0.05, f"Regression CAGR {cagr}% != 8.60%"
assert abs(max_dd - 8.27) <= 0.05, f"Regression DD {max_dd}% != 8.27%"
print("✅ D25 REGRESSION GATE PASSED SUCCESSFULLY.\n")

print("=" * 135)
print(" 📊 B + D25 COMPREHENSIVE TRADE-LEVEL ATTRIBUTION")
print("=" * 135)

donch_trades = tdf[tdf['sleeve'] == 'DONCHIAN_SATELLITE'] if not tdf.empty and 'sleeve' in tdf.columns else pd.DataFrame()

if not donch_trades.empty:
    wins = donch_trades[donch_trades['raw_r'] > 0]
    losses = donch_trades[donch_trades['raw_r'] <= 0]
    
    total_donch_pnl = donch_trades['dollar_pnl'].sum()
    gross_wins = wins['dollar_pnl'].sum()
    gross_losses = abs(losses['dollar_pnl'].sum())
    donch_pf = gross_wins / gross_losses if gross_losses > 0 else float('inf')

    print(f"• Total Donchian Trades          : {len(donch_trades)}")
    print(f"• Win Rate                       : {(len(wins) / len(donch_trades)) * 100:.1f}%")
    print(f"• Profit Factor                  : {donch_pf:.2f}")
    print(f"• Average R Multiple             : {donch_trades['raw_r'].mean():+.2f}R (Wins: {wins['raw_r'].mean():+.2f}R, Losses: {losses['raw_r'].mean():+.2f}R)")
    print(f"• Median R Multiple              : {donch_trades['raw_r'].median():+.2f}R")
    print(f"• Largest Winner / Loser         : {donch_trades['raw_r'].max():+.2f}R / {donch_trades['raw_r'].min():+.2f}R")
    print(f"• Average Holding Period         : {donch_trades['days_held'].mean():.1f} days")
    print(f"• Total Donchian Realized P&L    : ${total_donch_pnl:,.2f}")

    print("\n🏆 Top Profits by Symbol (Top 5):")
    sym_pnl = donch_trades.groupby('symbol')['dollar_pnl'].sum().sort_values(ascending=False)
    sym_cnt = donch_trades.groupby('symbol')['dollar_pnl'].count()
    top5_pnl = sym_pnl.head(5).sum()
    print(f"  - Top-1 Share of Profits       : {(sym_pnl.iloc[0] / total_donch_pnl) * 100:.1f}%")
    print(f"  - Top-3 Share of Profits       : {(sym_pnl.head(3).sum() / total_donch_pnl) * 100:.1f}%")
    print(f"  - Top-5 Share of Profits       : {(top5_pnl / total_donch_pnl) * 100:.1f}%")
    for sym, pnl in sym_pnl.head(5).items():
        print(f"    * {sym}: ${pnl:,.2f} ({sym_cnt[sym]} trades)")

    print("\n📅 Donchian P&L & Trade Count by Year:")
    donch_trades['year'] = pd.to_datetime(donch_trades['exit_date']).dt.year
    yr_pnl = donch_trades.groupby('year')['dollar_pnl'].sum()
    yr_cnt = donch_trades.groupby('year')['dollar_pnl'].count()
    for yr, pnl in yr_pnl.items():
        print(f"  - {yr}: ${pnl:,.2f} ({yr_cnt[yr]} trades)")

    print("\n📦 Sleeve-Level Realized Contribution:")
    sleeve_pnl = tdf.groupby('sleeve')['dollar_pnl'].sum()
    for sleeve, pnl in sleeve_pnl.items():
        print(f"  - {sleeve}: ${pnl:,.2f}")
else:
    print("No Donchian trades recorded.")

print("=" * 135)

print("\n" + "=" * 135)
print(" 🎲 DAILY BLOCK-BOOTSTRAP MONTE CARLO CERTIFICATION (5,000 SIMS)")
print("=" * 135)

daily_ret = eq_df['equity'].pct_change().dropna().values
n_days = len(daily_ret)
block_size = 20
n_blocks = int(np.ceil(n_days / block_size))
n_sims = 5000

sim_cagrs = []
sim_max_dds = []
sim_calmars = []
terminal_pos = 0
dd_exceed_10 = 0
dd_exceed_125 = 0
dd_exceed_15 = 0
dd_exceed_20 = 0

np.random.seed(42)
for _ in range(n_sims):
    starts = np.random.randint(0, n_days - block_size + 1, size=n_blocks)
    boot_ret = np.concatenate([daily_ret[s:s + block_size] for s in starts])[:n_days]
    equity = INITIAL_NAV * np.cumprod(1.0 + boot_ret)
    term_val = equity[-1]
    
    peak = np.maximum.accumulate(equity)
    dd = np.max((peak - equity) / peak)

    if term_val > INITIAL_NAV: terminal_pos += 1
    if dd > 0.10: dd_exceed_10 += 1
    if dd > 0.125: dd_exceed_125 += 1
    if dd > 0.15: dd_exceed_15 += 1
    if dd > 0.20: dd_exceed_20 += 1

    cagr_sim = (((term_val / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
    max_dd_pct = dd * 100.0
    calmar_sim = (cagr_sim / max_dd_pct) if max_dd_pct > 0 else 0.0

    sim_cagrs.append(cagr_sim)
    sim_max_dds.append(max_dd_pct)
    sim_calmars.append(calmar_sim)

print(f"• Positive Terminal Probability    : {(terminal_pos / n_sims) * 100:.1f}%")
print(f"• Median CAGR                    : {np.median(sim_cagrs):.2f}% (5th Pct: {np.percentile(sim_cagrs, 5):.2f}%, 1st Pct: {np.percentile(sim_cagrs, 1):.2f}%)")
print(f"• Median Max Drawdown            : {np.median(sim_max_dds):.2f}% (90th Pct: {np.percentile(sim_max_dds, 90):.2f}%, 95th Pct: {np.percentile(sim_max_dds, 95):.2f}%, 99th Pct: {np.percentile(sim_max_dds, 99):.2f}%)")
print(f"• Median Calmar Ratio            : {np.median(sim_calmars):.2f} (5th Pct: {np.percentile(sim_calmars, 5):.2f}%)")
print(f"• Prob(Max DD > 10.0%)           : {(dd_exceed_10 / n_sims) * 100:.1f}%")
print(f"• Prob(Max DD > 12.5%)           : {(dd_exceed_125 / n_sims) * 100:.1f}%")
print(f"• Prob(Max DD > 15.0%)           : {(dd_exceed_15 / n_sims) * 100:.1f}%")
print(f"• Prob(Max DD > 20.0%)           : {(dd_exceed_20 / n_sims) * 100:.1f}%")
print("=" * 135)
