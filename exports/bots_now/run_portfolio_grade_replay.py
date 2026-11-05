#!/usr/bin/env python3
import os
import glob
from pathlib import Path
import numpy as np
import pandas as pd

DATA_DIR = Path("/home/moonrider1968/bots/data/elite20_v31")
INITIAL_NAV = 100000.0
MAX_CONCURRENT = 3
CORE_RISK_PCT = 0.0050    # 0.50% NAV risk per unit (1.0R)
FRICTION_BPS = 0.0005     # 5 bps
SPLIT_DATE = pd.to_datetime("2024-01-01")

def load_all_bars():
    dfs = {}
    for f in glob.glob(str(DATA_DIR / "*.csv")):
        sym = Path(f).stem.upper()
        df = pd.read_csv(f)
        df.columns = [c.lower() for c in df.columns]
        df['time'] = pd.to_datetime(df['date'] if 'date' in df.columns else df['time'])
        df = df.sort_values('time').reset_index(drop=True)
        if len(df) >= 250:
            # Indicator Calculations
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
data = load_all_bars()
all_dates = sorted(list(set.union(*[set(df.index) for df in data.values()])))
all_dates = [d for d in all_dates if d >= pd.to_datetime("2021-06-01")]

print(f"Loaded {len(data)} symbols across {len(all_dates)} trading days.")

# Portfolio State
cash = INITIAL_NAV
positions = {} # sym -> dict
daily_records = []
closed_trades = []

pending_orders = [] # list of dicts: action, sym, etc.

for i, dt in enumerate(all_dates):
    # 1. Execute Pending Orders at Day T Open
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
                    current_nav = cash + sum(p['shares'] * data[p_sym].loc[dt]['open'] for p_sym, p in positions.items() if dt in data[p_sym].index)
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
                            'split': "IN_SAMPLE" if dt < SPLIT_DATE else "OUT_OF_SAMPLE",
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
                    # Keep total risk constrained to 1.0R
                    p['current_stop'] = max(p['current_stop'], p['initial_stop'])

        elif order['action'] == 'HARVEST' and sym in positions:
            p = positions[sym]
            if not p['harvested']:
                fill_px = open_px * (1.0 - FRICTION_BPS)
                trim_shares = int(p['shares'] * 0.5)
                if trim_shares > 0:
                    cash += trim_shares * fill_px
                    p['shares'] -= trim_shares
                    p['harvested'] = True
                    p['current_stop'] = max(p['current_stop'], p['entry_px']) # lock BE

        elif order['action'] == 'EXIT' and sym in positions:
            p = positions[sym]
            fill_px = order.get('fill_override', open_px * (1.0 - FRICTION_BPS))
            proceeds = p['shares'] * fill_px
            cash += proceeds
            net_pnl = proceeds - (p['shares'] * p['entry_px'])
            r_realized = (fill_px - p['entry_px']) / p['r_dist']
            closed_trades.append({
                'sym': sym,
                'entry_date': p['entry_date'],
                'exit_date': dt,
                'r_multiple': r_realized,
                'pnl': net_pnl,
                'split': p['split']
            })
            del positions[sym]

    pending_orders = []

    # 2. Intra-day Price Check & EOD Stop Management
    for sym in list(positions.keys()):
        if dt not in data[sym].index:
            continue
        bar = data[sym].loc[dt]
        p = positions[sym]
        p['bars'] += 1
        p['peak_px'] = max(p['peak_px'], bar['high'])
        
        # Stop-out check (Gap-through handling)
        if bar['low'] <= p['current_stop']:
            # If opened below stop, gap execution at Open; else filled at stop price
            exit_fill = bar['open'] if bar['open'] < p['current_stop'] else p['current_stop']
            exit_fill *= (1.0 - FRICTION_BPS)
            proceeds = p['shares'] * exit_fill
            cash += proceeds
            r_realized = (exit_fill - p['entry_px']) / p['r_dist']
            closed_trades.append({
                'sym': sym,
                'entry_date': p['entry_date'],
                'exit_date': dt,
                'r_multiple': r_realized,
                'pnl': proceeds - (p['shares'] * p['entry_px']),
                'split': p['split']
            })
            del positions[sym]
            continue

        # Time stop (45 bars max)
        if p['bars'] >= 45:
            pending_orders.append({'action': 'EXIT', 'sym': sym})
            continue

        # Check Zone Harvest Trigger (+2.0R MFE)
        mfe_r = (bar['high'] - p['entry_px']) / p['r_dist']
        if not p['harvested'] and mfe_r >= 2.0:
            pending_orders.append({'action': 'HARVEST', 'sym': sym})

        # Check Zone Add Trigger (Pullback to 20-50 band)
        if not p['scaled_in'] and (bar['low'] <= bar['sma20']) and (bar['close'] >= bar['sma50']):
            pending_orders.append({'action': 'ADD', 'sym': sym})

    # 3. Screen New Stage 2 Setups at Close (for T+1 open fill)
    for sym, df in data.items():
        if dt not in df.index:
            continue
        if sym in positions or any(o['sym'] == sym for o in pending_orders):
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
            intended_stop = bar['close'] - r_dist
            pending_orders.append({
                'action': 'ENTRY',
                'sym': sym,
                'intended_stop': intended_stop
            })

    # 4. Daily Mark-to-Market (MTM) NAV
    open_equity = sum(p['shares'] * data[s].loc[dt]['close'] for s, p in positions.items() if dt in data[s].index)
    total_nav = cash + open_equity
    daily_records.append({'date': dt, 'nav': total_nav, 'open_positions': len(positions)})

# --- Performance Analytics ---
perf_df = pd.DataFrame(daily_records).set_index('date')
perf_df['peak'] = perf_df['nav'].cummax()
perf_df['dd_pct'] = (perf_df['nav'] - perf_df['peak']) / perf_df['peak'] * 100.0

# Split Metrics
is_df = perf_df.loc[perf_df.index < SPLIT_DATE]
oos_df = perf_df.loc[perf_df.index >= SPLIT_DATE]

def calc_stats(sub_df, name):
    start = sub_df['nav'].iloc[0]
    end = sub_df['nav'].iloc[-1]
    years = (sub_df.index[-1] - sub_df.index[0]).days / 365.25
    cagr = ((end / start) ** (1.0 / years) - 1.0) * 100.0
    max_dd = sub_df['dd_pct'].min()
    daily_ret = sub_df['nav'].pct_change().dropna()
    sharpe = (daily_ret.mean() / (daily_ret.std() + 1e-9)) * np.sqrt(252)
    calmar = abs(cagr / max_dd) if abs(max_dd) > 0 else 0.0
    return {
        "Period": name,
        "Start NAV": f"${start:,.0f}",
        "End NAV": f"${end:,.0f}",
        "CAGR": f"{cagr:.2f}%",
        "Max DD %": f"{max_dd:.2f}%",
        "Sharpe": f"{sharpe:.2f}",
        "Calmar": f"{calmar:.2f}"
    }

metrics = [
    calc_stats(perf_df, "Full Lifetime (2021-2026)"),
    calc_stats(is_df, "In-Sample (2021-2023)"),
    calc_stats(oos_df, "Out-of-Sample (2024-2026)")
]

print("\n" + "=" * 92)
print("PORTFOLIO-GRADE MTM REPLAY: COMP compounded CAGR & DRAWDOWN (MAX 3 CONCURRENT, 5 BPS)")
print("=" * 92)
print(pd.DataFrame(metrics).to_string(index=False))
print("=" * 92)
