#!/usr/bin/env python3
import sys
from pathlib import Path
import numpy as np
import pandas as pd

INITIAL_NAV = 100000.0
RISK_PCT = 0.0050          # 0.50% MTM NAV
MAX_POSITIONS = 3
MAX_CURRENCY_EXPOSURE = 2  # Max net long/short positions per currency factor
SPLIT_DATE = pd.to_datetime("2024-01-01", utc=True)

DATA_DIR = Path("/home/moonrider1968/bots/oanda_data/h4")

PAIRS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", 
    "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"
]

BASE_SPREAD_PIPS = {
    "EUR_USD": 1.2, "GBP_USD": 1.8, "USD_JPY": 1.4,
    "AUD_USD": 1.4, "USD_CAD": 1.6, "NZD_USD": 1.8,
    "USD_CHF": 1.6, "EUR_JPY": 2.0, "GBP_JPY": 2.5
}

def get_pip_val(pair):
    return 0.01 if "JPY" in pair else 0.0001

def load_real_oanda_h4():
    dfs = {}
    if not DATA_DIR.exists():
        sys.exit(f"FATAL: Directory {DATA_DIR} does not exist. ABORTING.")
        
    for pair in PAIRS:
        fpath = DATA_DIR / f"{pair}_H4.csv"
        if not fpath.exists():
            sys.exit(f"FATAL: Missing real data file: {fpath}. ABORTING without fallback.")
        
        df = pd.read_csv(fpath)
        t_col = 'timestamp' if 'timestamp' in df.columns else 'time'
        df['time'] = pd.to_datetime(df[t_col], utc=True)
        df = df.sort_values('time').reset_index(drop=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        dfs[pair] = df

    if len(dfs) != len(PAIRS):
        sys.exit(f"FATAL: Required {len(PAIRS)} pairs, found {len(dfs)}. ABORTING.")
        
    return dfs

def prep_indicators(df, trend_lookback=55, trend_exit=20, mr_lookback=50, mr_z=2.5):
    res = df.copy()
    # Trend Sleeve indicators (strictly shifted to prevent lookahead)
    res['ema200'] = res['close'].ewm(span=200, adjust=False).mean()
    res['ema200_slope'] = res['ema200'] - res['ema200'].shift(5)
    res['donch_high'] = res['high'].shift(1).rolling(trend_lookback).max()
    res['donch_low'] = res['low'].shift(1).rolling(trend_lookback).min()
    res['donch_exit_high'] = res['high'].shift(1).rolling(trend_exit).max()
    res['donch_exit_low'] = res['low'].shift(1).rolling(trend_exit).min()

    # Reversion Sleeve indicators
    res['sma_mr'] = res['close'].rolling(mr_lookback).mean()
    res['std_mr'] = res['close'].rolling(mr_lookback).std(ddof=0)
    res['z_score'] = (res['close'] - res['sma_mr']) / (res['std_mr'] + 1e-9)
    res['z_score_prev'] = res['z_score'].shift(1)

    # ATR
    tr1 = res['high'] - res['low']
    tr2 = (res['high'] - res['close'].shift(1)).abs()
    tr3 = (res['low'] - res['close'].shift(1)).abs()
    res['atr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean()
    
    return res.dropna().reset_index(drop=True)

def simulate_portfolio(data_dict, mode="BOTH", friction_mult=1.0, use_factor_cap=True,
                       trend_stop_atr=2.0, mr_stop_atr=2.0, mr_max_bars=12, mr_z_thresh=2.5):
    all_dates = sorted(list(set.union(*[set(df['time']) for df in data_dict.values()])))
    date_to_idx = {p: {t: i for i, t in enumerate(data_dict[p]['time'])} for p in data_dict}
    
    cash = INITIAL_NAV
    positions = {}
    closed_trades = []
    daily_mfs = []
    trade_id_counter = 0

    for dt in all_dates:
        # 1. Update & Exit Open Positions
        pos_keys = list(positions.keys())
        for pid in pos_keys:
            pos = positions[pid]
            pair = pos['pair']
            df = data_dict[pair]
            if dt not in date_to_idx[pair]:
                continue
            idx = date_to_idx[pair][dt]
            bar = df.iloc[idx]
            pos['bars_held'] += 1

            pip_val = get_pip_val(pair)
            spread = BASE_SPREAD_PIPS[pair] * pip_val * friction_mult
            exit_signal = False
            exit_px = bar['open']

            # Stop-loss check with adverse gap handling
            if pos['dir'] == 1:
                if bar['low'] <= pos['stop_px']:
                    exit_signal = True
                    exit_px = min(bar['open'], pos['stop_px']) - spread
            else:
                if bar['high'] >= pos['stop_px']:
                    exit_signal = True
                    exit_px = max(bar['open'], pos['stop_px']) + spread

            # Sleeve exits
            if not exit_signal:
                if pos['sleeve'] == "TREND":
                    if pos['dir'] == 1 and bar['low'] <= bar['donch_exit_low']:
                        exit_signal = True
                        exit_px = min(bar['open'], bar['donch_exit_low']) - spread
                    elif pos['dir'] == -1 and bar['high'] >= bar['donch_exit_high']:
                        exit_signal = True
                        exit_px = max(bar['open'], bar['donch_exit_high']) + spread
                elif pos['sleeve'] == "REVERSION":
                    if pos['dir'] == 1 and bar['high'] >= bar['sma_mr']:
                        exit_signal = True
                        exit_px = bar['sma_mr'] - spread
                    elif pos['dir'] == -1 and bar['low'] <= bar['sma_mr']:
                        exit_signal = True
                        exit_px = bar['sma_mr'] + spread
                    elif pos['bars_held'] >= mr_max_bars:
                        exit_signal = True
                        exit_px = (bar['close'] - spread) if pos['dir'] == 1 else (bar['close'] + spread)

            if exit_signal:
                pnl_per_unit = (exit_px - pos['entry_px']) if pos['dir'] == 1 else (pos['entry_px'] - exit_px)
                dollar_pnl = pnl_per_unit * pos['units'] / (bar['close'] if "JPY" in pair else 1.0)
                cash += pos['cost_basis'] + dollar_pnl
                r_mult = pnl_per_unit / pos['initial_risk_dist']
                closed_trades.append({
                    "id": pid,
                    "pair": pair,
                    "sleeve": pos['sleeve'],
                    "entry_date": pos['entry_date'],
                    "exit_date": dt,
                    "r_mult": r_mult,
                    "pnl": dollar_pnl,
                    "bars": pos['bars_held'],
                    "split": "IN_SAMPLE" if pos['entry_date'] < SPLIT_DATE else "OUT_OF_SAMPLE"
                })
                del positions[pid]

        # 2. Factor Exposures Check
        curr_exposures = {}
        for pos in positions.values():
            base, quote = pos['pair'].split("_")
            curr_exposures[base] = curr_exposures.get(base, 0) + pos['dir']
            curr_exposures[quote] = curr_exposures.get(quote, 0) - pos['dir']

        # 3. Next Bar Entries
        for pair in PAIRS:
            if len(positions) >= MAX_POSITIONS:
                break
            df = data_dict[pair]
            if dt not in date_to_idx[pair]:
                continue
            idx = date_to_idx[pair][dt]
            if idx < 205 or idx >= len(df) - 1:
                continue

            bar = df.iloc[idx]
            next_bar = df.iloc[idx + 1]
            pip_val = get_pip_val(pair)
            spread = BASE_SPREAD_PIPS[pair] * pip_val * friction_mult

            trend_sig = 0
            if mode in ["BOTH", "TREND_ONLY"]:
                if bar['close'] > bar['ema200'] and bar['ema200_slope'] > 0 and bar['close'] > bar['donch_high']:
                    trend_sig = 1
                elif bar['close'] < bar['ema200'] and bar['ema200_slope'] < 0 and bar['close'] < bar['donch_low']:
                    trend_sig = -1

            mr_sig = 0
            if mode in ["BOTH", "REVERSION_ONLY"]:
                if bar['z_score_prev'] <= -mr_z_thresh and bar['z_score'] > bar['z_score_prev'] and bar['close'] > bar['open']:
                    mr_sig = 1
                elif bar['z_score_prev'] >= mr_z_thresh and bar['z_score'] < bar['z_score_prev'] and bar['close'] < bar['open']:
                    mr_sig = -1

            chosen_sig = trend_sig if trend_sig != 0 else mr_sig
            chosen_sleeve = "TREND" if trend_sig != 0 else ("REVERSION" if mr_sig != 0 else None)

            if chosen_sig != 0:
                if any(p['pair'] == pair for p in positions.values()):
                    continue

                base, quote = pair.split("_")
                if use_factor_cap:
                    new_base = curr_exposures.get(base, 0) + chosen_sig
                    new_quote = curr_exposures.get(quote, 0) - chosen_sig
                    if abs(new_base) > MAX_CURRENCY_EXPOSURE or abs(new_quote) > MAX_CURRENCY_EXPOSURE:
                        continue

                entry_fill = (next_bar['open'] + spread) if chosen_sig == 1 else (next_bar['open'] - spread)
                stop_mult = trend_stop_atr if chosen_sleeve == "TREND" else mr_stop_atr
                risk_dist = bar['atr'] * stop_mult
                if risk_dist <= 0:
                    continue

                stop_px = (entry_fill - risk_dist) if chosen_sig == 1 else (entry_fill + risk_dist)
                current_nav = cash + sum(p['cost_basis'] for p in positions.values())
                risk_dollars = current_nav * RISK_PCT
                units = (risk_dollars / risk_dist) * (bar['close'] if "JPY" in pair else 1.0)
                cost = (units * entry_fill) / (bar['close'] if "JPY" in pair else 1.0) * 0.03

                if cash >= cost and units > 0:
                    cash -= cost
                    trade_id_counter += 1
                    positions[trade_id_counter] = {
                        "pair": pair,
                        "dir": chosen_sig,
                        "sleeve": chosen_sleeve,
                        "entry_date": next_bar['time'],
                        "entry_px": entry_fill,
                        "stop_px": stop_px,
                        "initial_risk_dist": risk_dist,
                        "units": units,
                        "cost_basis": cost,
                        "bars_held": 0
                    }
                    curr_exposures[base] = curr_exposures.get(base, 0) + chosen_sig
                    curr_exposures[quote] = curr_exposures.get(quote, 0) - chosen_sig

        # MTM Accounting
        unrealized = 0
        for pos in positions.values():
            p_df = data_dict[pos['pair']]
            if dt in date_to_idx[pos['pair']]:
                cur_px = p_df.iloc[date_to_idx[pos['pair']][dt]]['close']
                pnl = (cur_px - pos['entry_px']) if pos['dir'] == 1 else (pos['entry_px'] - cur_px)
                unrealized += (pnl * pos['units']) / (cur_px if "JPY" in pos['pair'] else 1.0)
        daily_mfs.append({"time": dt, "nav": cash + unrealized + sum(p['cost_basis'] for p in positions.values())})

    p_df = pd.DataFrame(daily_mfs).set_index("time")
    p_df['peak'] = p_df['nav'].cummax()
    p_df['dd_pct'] = (p_df['nav'] - p_df['peak']) / p_df['peak'] * 100.0
    return p_df, pd.DataFrame(closed_trades)

def get_stats(trades_df, perf_df):
    if trades_df.empty:
        return {"N": 0, "WR": "0.0%", "Exp_R": "+0.00R", "PF": "0.00", "CAGR": "0.0%", "MaxDD": "0.0%"}
    r = trades_df['r_mult'].values
    wins = r[r > 0]
    wr = len(wins) / len(r) * 100.0
    exp_r = np.mean(r)
    pf = wins.sum() / (abs(r[r <= 0].sum()) + 1e-9)
    
    start_nav = perf_df['nav'].iloc[0]
    end_nav = perf_df['nav'].iloc[-1]
    years = (perf_df.index[-1] - perf_df.index[0]).days / 365.25
    cagr = ((end_nav / start_nav) ** (1.0 / years) - 1.0) * 100.0 if years > 0 else 0.0
    max_dd = perf_df['dd_pct'].min()
    
    return {
        "N": len(r),
        "WR": f"{wr:.1f}%",
        "Exp_R": f"{exp_r:+.3f}R",
        "PF": f"{pf:.2f}",
        "CAGR": f"{cagr:.2f}%",
        "MaxDD": f"{max_dd:.2f}%"
    }

if __name__ == "__main__":
    print("=" * 85)
    print("PANDA FX DUAL-EDGE H4: AUTHENTIC OANDA DATA VERIFICATION")
    print("=" * 85)
    
    raw_dfs = load_real_oanda_h4()
    print("REAL_DATA_ONLY: PASS")
    print(f"PAIRS_LOADED:   {len(raw_dfs)}/{len(PAIRS)} PASS")
    print("SYNTHETIC_DATA: FORBIDDEN")
    print(f"SAMPLE RANGE:   {raw_dfs['EUR_USD']['time'].iloc[0].strftime('%Y-%m-%d')} to {raw_dfs['EUR_USD']['time'].iloc[-1].strftime('%Y-%m-%d')}")

    # Baseline indicators (55/20/50/2.5)
    baseline_dfs = {p: prep_indicators(raw_dfs[p], trend_lookback=55, trend_exit=20, mr_lookback=50, mr_z=2.5) for p in raw_dfs}

    # 1. Compare Portfolios A, B, C, D
    print("\n--- 1. PORTFOLIO CONFIGURATION COMPARISON (1.0x Friction) ---")
    configs = [
        ("Portfolio A (Trend Only)", "TREND_ONLY", False),
        ("Portfolio B (Reversion Only)", "REVERSION_ONLY", False),
        ("Portfolio C (Trend + Reversion Unconstrained)", "BOTH", False),
        ("Portfolio D (Dual-Edge + Factor Governor)", "BOTH", True)
    ]
    
    results = []
    t_records = {}
    for name, m, gov in configs:
        perf, trades = simulate_portfolio(baseline_dfs, mode=m, friction_mult=1.0, use_factor_cap=gov)
        t_records[name] = trades
        st = get_stats(trades, perf)
        st["Config"] = name
        results.append(st)
        
    print(pd.DataFrame(results)[["Config", "N", "WR", "Exp_R", "PF", "CAGR", "MaxDD"]].to_string(index=False))

    # 2. Out-of-Sample (2024-2026) Validation on Portfolio D
    print("\n--- 2. IN-SAMPLE vs OUT-OF-SAMPLE BREAKDOWN (Portfolio D) ---")
    t_d = t_records["Portfolio D (Dual-Edge + Factor Governor)"]
    if not t_d.empty:
        is_t = t_d[t_d['split'] == "IN_SAMPLE"]
        oos_t = t_d[t_d['split'] == "OUT_OF_SAMPLE"]
        is_pf = is_t[is_t['r_mult']>0]['r_mult'].sum()/(abs(is_t[is_t['r_mult']<=0]['r_mult'].sum())+1e-9) if not is_t.empty else 0
        oos_pf = oos_t[oos_t['r_mult']>0]['r_mult'].sum()/(abs(oos_t[oos_t['r_mult']<=0]['r_mult'].sum())+1e-9) if not oos_t.empty else 0
        print(f"In-Sample   (Pre-2024)  | N: {len(is_t):<4} | Exp: {is_t['r_mult'].mean():+.3f}R | PF: {is_pf:.2f}")
        print(f"Out-of-Sample (2024-2026) | N: {len(oos_t):<4} | Exp: {oos_t['r_mult'].mean():+.3f}R | PF: {oos_pf:.2f}")

    # 3. Hostile Friction Ladder on Portfolio D
    print("\n--- 3. HOSTILE FRICTION LADDER (Portfolio D) ---")
    f_ladder = [1.0, 1.5, 2.0, 3.0]
    f_results = []
    for f in f_ladder:
        p_f, t_f = simulate_portfolio(baseline_dfs, mode="BOTH", friction_mult=f, use_factor_cap=True)
        st = get_stats(t_f, p_f)
        st["Friction"] = f"{f:.1f}x"
        f_results.append(st)
    print(pd.DataFrame(f_results)[["Friction", "N", "Exp_R", "PF", "CAGR", "MaxDD"]].to_string(index=False))

    # 4. Pair Attribution (Portfolio D @ 1.0x)
    print("\n--- 4. PAIR PERFORMANCE ATTRIBUTION (Portfolio D) ---")
    if not t_d.empty:
        pair_summary = t_d.groupby("pair").agg(
            trades=("r_mult", "count"),
            win_rate=("r_mult", lambda x: f"{(x > 0).mean()*100:.1f}%"),
            total_r=("r_mult", "sum"),
            exp_r=("r_mult", lambda x: f"{x.mean():+.3f}R")
        ).sort_values(by="total_r", ascending=False)
        print(pair_summary.to_string())

    # 5. Coarse Robustness Surface Grid (Dynamic indicator mutation)
    print("\n--- 5. COARSE ROBUSTNESS SURFACE (Writing to fx_dual_edge_surface.csv) ---")
    surface_records = []
    for t_look in [40, 55, 80]:
        for t_stop in [1.5, 2.0, 2.5]:
            for z_val in [2.0, 2.5, 3.0]:
                for mr_hold in [8, 12, 18]:
                    mutated_dfs = {p: prep_indicators(raw_dfs[p], trend_lookback=t_look, trend_exit=20, mr_lookback=50, mr_z=z_val) for p in raw_dfs}
                    p_grid, t_grid = simulate_portfolio(
                        mutated_dfs, mode="BOTH", friction_mult=1.0, use_factor_cap=True,
                        trend_stop_atr=t_stop, mr_stop_atr=2.0, mr_max_bars=mr_hold, mr_z_thresh=z_val
                    )
                    st = get_stats(t_grid, p_grid)
                    surface_records.append({
                        "trend_lookback": t_look,
                        "trend_stop": t_stop,
                        "z_entry": z_val,
                        "mr_max_bars": mr_hold,
                        "N": st["N"],
                        "Exp_R": st["Exp_R"],
                        "PF": st["PF"],
                        "CAGR": st["CAGR"],
                        "MaxDD": st["MaxDD"]
                    })
    surf_df = pd.DataFrame(surface_records)
    surf_df.to_csv("fx_dual_edge_surface.csv", index=False)
    print(f"Surface generated with {len(surf_df)} configurations. Top 5 parameter sets by Profit Factor:")
    print(surf_df.sort_values(by="PF", ascending=False).head(5).to_string(index=False))
    print("=" * 85)
