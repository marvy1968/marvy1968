#!/usr/bin/env python3
import glob
from pathlib import Path
import numpy as np
import pandas as pd

DATA_DIR = Path("/home/moonrider1968/bots/data/elite20_v31")
INITIAL_NAV = 100000.0
MAX_CONCURRENT = 3
CORE_RISK_PCT = 0.0050
FRICTION_BPS = 0.0005
SPLIT_DATE = pd.to_datetime("2024-01-01")

def load_all():
    dfs = {}
    for f in glob.glob(str(DATA_DIR / "*.csv")):
        sym = Path(f).stem.upper()
        df = pd.read_csv(f)
        df.columns = [c.lower() for c in df.columns]
        df['time'] = pd.to_datetime(df['date'] if 'date' in df.columns else df['time'])
        df = df.sort_values('time').reset_index(drop=True)
        if len(df) >= 250:
            df['sma20'] = df['close'].rolling(20).mean()
            df['sma50'] = df['close'].rolling(50).mean()
            df['sma200'] = df['close'].rolling(200).mean()
            df['sma200_slope'] = df['sma200'] - df['sma200'].shift(20)
            
            tr1 = df['high'] - df['low']
            tr2 = (df['high'] - df['close'].shift(1)).abs()
            tr3 = (df['low'] - df['close'].shift(1)).abs()
            df['atr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean()
            
            vol = df['volume'] if 'volume' in df.columns else pd.Series(1, index=df.index)
            tp = (df['high'] + df['low'] + df['close']) / 3.0
            df['vwap'] = (tp * vol).rolling(20).sum() / (vol.rolling(20).sum() + 1e-9)
            df['swing_high'] = df['high'].rolling(10).max()
            dfs[sym] = df.dropna().set_index('time')
    return dfs

print("Loading historical bar data...")
data = load_all()
all_dates = sorted(list(set.union(*[set(df.index) for df in data.values()])))
all_dates = [d for d in all_dates if d >= pd.to_datetime("2021-06-01")]
spy_df = data.get("SPY")

def run_simulation(use_macro_filter=False):
    cash = INITIAL_NAV
    positions = {}
    daily_records = []
    closed_trades = []
    pending_orders = []

    for dt in all_dates:
        # 1. Execute Open Fills
        for order in pending_orders:
            sym = order['sym']
            if sym not in data or dt not in data[sym].index:
                continue
            bar = data[sym].loc[dt]
            open_px = bar['open']

            if order['action'] == 'ENTRY':
                if len(positions) < MAX_CONCURRENT and sym not in positions:
                    fill_px = open_px * (1.0 + FRICTION_BPS)
                    stop_px = order['intended_stop']
                    r_dist = fill_px - stop_px
                    if r_dist > 0:
                        current_nav = cash + sum(p['shares'] * data[s].loc[dt]['open'] for s, p in positions.items() if dt in data[s].index)
                        risk_dollars = current_nav * CORE_RISK_PCT
                        shares = int(risk_dollars / r_dist)
                        cost = shares * fill_px
                        if shares > 0 and cash >= cost:
                            cash -= cost
                            positions[sym] = {
                                'shares': shares,
                                'entry_px': fill_px,
                                'initial_stop': stop_px,
                                'current_stop': stop_px,
                                'r_dist': r_dist,
                                'scaled_in': False,
                                'harvested': False,
                                'entry_date': dt,
                                'peak_px': fill_px,
                                'bars': 0
                            }

            elif order['action'] == 'ADD' and sym in positions:
                p = positions[sym]
                if not p['scaled_in']:
                    fill_px = open_px * (1.0 + FRICTION_BPS)
                    add_shares = int(p['shares'] * 0.5)
                    cost = add_shares * fill_px
                    if add_shares > 0 and cash >= cost:
                        cash -= cost
                        new_shares = p['shares'] + add_shares
                        p['entry_px'] = ((p['shares'] * p['entry_px']) + (add_shares * fill_px)) / new_shares
                        p['shares'] = new_shares
                        p['scaled_in'] = True

            elif order['action'] == 'HARVEST' and sym in positions:
                p = positions[sym]
                if not p['harvested']:
                    fill_px = open_px * (1.0 - FRICTION_BPS)
                    trim_shares = int(p['shares'] * 0.5)
                    if trim_shares > 0:
                        cash += trim_shares * fill_px
                        p['shares'] -= trim_shares
                        p['harvested'] = True
                        p['current_stop'] = max(p['current_stop'], p['entry_px'])

            elif order['action'] == 'EXIT' and sym in positions:
                p = positions[sym]
                fill_px = open_px * (1.0 - FRICTION_BPS)
                proceeds = p['shares'] * fill_px
                cash += proceeds
                r_realized = (fill_px - p['entry_px']) / p['r_dist']
                closed_trades.append({
                    'sym': sym,
                    'entry_date': p['entry_date'],
                    'exit_date': dt,
                    'pnl': proceeds - (p['shares'] * p['entry_px']),
                    'r_multiple': r_realized
                })
                del positions[sym]

        pending_orders = []

        # 2. Intraday Checks & Exits
        for sym in list(positions.keys()):
            if dt not in data[sym].index:
                continue
            bar = data[sym].loc[dt]
            p = positions[sym]
            p['bars'] += 1
            p['peak_px'] = max(p['peak_px'], bar['high'])

            if bar['low'] <= p['current_stop']:
                exit_fill = bar['open'] if bar['open'] < p['current_stop'] else p['current_stop']
                exit_fill *= (1.0 - FRICTION_BPS)
                proceeds = p['shares'] * exit_fill
                cash += proceeds
                r_realized = (exit_fill - p['entry_px']) / p['r_dist']
                closed_trades.append({
                    'sym': sym,
                    'entry_date': p['entry_date'],
                    'exit_date': dt,
                    'pnl': proceeds - (p['shares'] * p['entry_px']),
                    'r_multiple': r_realized
                })
                del positions[sym]
                continue

            if p['bars'] >= 45:
                pending_orders.append({'action': 'EXIT', 'sym': sym})
                continue

            mfe_r = (bar['high'] - p['entry_px']) / p['r_dist']
            if not p['harvested'] and mfe_r >= 2.0:
                pending_orders.append({'action': 'HARVEST', 'sym': sym})

            if not p['scaled_in'] and (bar['low'] <= bar['sma20']) and (bar['close'] >= bar['sma50']):
                pending_orders.append({'action': 'ADD', 'sym': sym})

        # 3. Macro Filter Evaluation & Screening
        spy_bullish = True
        if use_macro_filter and spy_df is not None and dt in spy_df.index:
            spy_bullish = spy_df.loc[dt]['close'] > spy_df.loc[dt]['sma200']

        if spy_bullish:
            for sym, df in data.items():
                if dt not in df.index or sym in positions or any(o['sym'] == sym for o in pending_orders):
                    continue
                bar = df.loc[dt]
                idx = df.index.get_loc(dt)
                if idx < 1:
                    continue
                prev = df.iloc[idx - 1]

                stage_2 = (bar['close'] > bar['sma200']) and (bar['sma200_slope'] > 0)
                ma_aligned = bar['sma20'] > bar['sma50']
                breakout = (bar['close'] > prev['swing_high']) and (bar['close'] > bar['vwap'])

                if stage_2 and ma_aligned and breakout:
                    r_dist = max(bar['atr'] * 1.2, bar['close'] * 0.015)
                    pending_orders.append({'action': 'ENTRY', 'sym': sym, 'intended_stop': bar['close'] - r_dist})

        open_eq = sum(p['shares'] * data[s].loc[dt]['close'] for s, p in positions.items() if dt in data[s].index)
        daily_records.append({'date': dt, 'nav': cash + open_eq})

    perf = pd.DataFrame(daily_records).set_index('date')
    perf['peak'] = perf['nav'].cummax()
    perf['dd_pct'] = (perf['nav'] - perf['peak']) / perf['peak'] * 100.0
    return perf, pd.DataFrame(closed_trades)

print("Running baseline unfiltered simulation...")
p_unfiltered, t_unfiltered = run_simulation(use_macro_filter=False)

print("Running SPY > 200 SMA macro-filtered simulation...")
p_filtered, t_filtered = run_simulation(use_macro_filter=True)

def calc_metrics(df, name):
    start = df['nav'].iloc[0]
    end = df['nav'].iloc[-1]
    years = (df.index[-1] - df.index[0]).days / 365.25
    cagr = ((end / start) ** (1.0 / years) - 1.0) * 100.0
    max_dd = df['dd_pct'].min()
    daily_ret = df['nav'].pct_change().dropna()
    sharpe = (daily_ret.mean() / (daily_ret.std() + 1e-9)) * np.sqrt(252)
    calmar = abs(cagr / max_dd) if abs(max_dd) > 0 else 0.0
    return {
        "Config": name,
        "End NAV": f"${end:,.0f}",
        "CAGR": f"{cagr:.2f}%",
        "Max DD %": f"{max_dd:.2f}%",
        "Sharpe": f"{sharpe:.2f}",
        "Calmar": f"{calmar:.2f}"
    }

print("\n" + "=" * 85)
print("MACRO REGIME GATE IMPACT (SPY > 200 SMA FILTER)")
print("=" * 85)
res = [
    calc_metrics(p_unfiltered, "1. Unfiltered Baseline"),
    calc_metrics(p_filtered, "2. SPY > 200 SMA Macro Filter"),
    calc_metrics(p_filtered.loc[p_filtered.index >= SPLIT_DATE], "   - Out-of-Sample (2024-2026)")
]
print(pd.DataFrame(res).to_string(index=False))
print("=" * 85)

# Loss Isolation Analysis
t_unfiltered['entry_date'] = pd.to_datetime(t_unfiltered['entry_date'])
t_unfiltered['exit_date'] = pd.to_datetime(t_unfiltered['exit_date'])
t_filtered['entry_date'] = pd.to_datetime(t_filtered['entry_date'])
t_filtered['exit_date'] = pd.to_datetime(t_filtered['exit_date'])

unfiltered_keys = set(zip(t_unfiltered['sym'], t_unfiltered['entry_date'].dt.strftime('%Y-%m-%d')))
filtered_keys = set(zip(t_filtered['sym'], t_filtered['entry_date'].dt.strftime('%Y-%m-%d')))

eliminated_keys = unfiltered_keys - filtered_keys
eliminated_df = t_unfiltered[t_unfiltered.apply(lambda r: (r['sym'], r['entry_date'].strftime('%Y-%m-%d')) in eliminated_keys, axis=1)].copy()
eliminated_df['year'] = eliminated_df['entry_date'].dt.year

target_losses = eliminated_df[
    (eliminated_df['year'].isin([2022, 2025])) & 
    (eliminated_df['r_multiple'] <= 0)
].sort_values(by=['year', 'r_multiple'], ascending=[True, True])

cols = ['year', 'sym', 'entry_date', 'exit_date', 'r_multiple', 'pnl']
target_losses['entry_date'] = target_losses['entry_date'].dt.strftime('%Y-%m-%d')
target_losses['exit_date'] = target_losses['exit_date'].dt.strftime('%Y-%m-%d')
target_losses['r_multiple'] = target_losses['r_multiple'].map(lambda x: f"{x:+.2f}R")
target_losses['pnl'] = target_losses['pnl'].map(lambda x: f"${x:,.2f}")

print("\n" + "=" * 85)
print("SPECIFIC LOSING TRADES ELIMINATED BY SPY > 200 SMA (2022 & 2025)")
print("=" * 85)
if not target_losses.empty:
    print(target_losses[cols].to_string(index=False))
    print("-" * 85)
    print(f"Total Losing Trades Eliminated in 2022/2025: {len(target_losses)}")
else:
    print("No losing trades found in the specified window.")
print("=" * 85)
