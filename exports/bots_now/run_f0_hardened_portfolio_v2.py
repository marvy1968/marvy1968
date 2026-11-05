#!/usr/bin/env python3
import sys
import numpy as np
import pandas as pd
from pathlib import Path

from run_h4_filter_sweep import (
    PAIRS,
    load_local_dataset,
    generate_filtered_signals,
    evaluate_path,
    get_pip_value,
    SPREAD_PIPS_MAP,
    ENTRY_SLIPPAGE_PIPS,
    EXIT_SLIPPAGE_PIPS
)

INITIAL_NAV = 100000.0
RISK_PCT = 0.0050          # 0.50% MTM NAV
MAX_POSITIONS = 3
MAX_FACTOR_EXPOSURE = 2    # Max net long/short positions per currency factor

print("=" * 95)
print("PANDA FX — F0 HARDENED PORTFOLIO V2 CERTIFICATION ENGINE")
print("=" * 95)

# 1. Load Datasets & Build PIT Conversion Map
dfs = {}
for sym in PAIRS:
    dfs[sym] = load_local_dataset(sym, "H4")

pit_quotes = {
    "USD_JPY": dfs["USD_JPY"]["close"].shift(1),
    "USD_CAD": dfs["USD_CAD"]["close"].shift(1),
    "USD_CHF": dfs["USD_CHF"]["close"].shift(1),
}

def get_quote_to_usd_rate(pair: str, t: pd.Timestamp) -> float:
    base, quote = pair.split("_")
    if quote == "USD":
        return 1.0
    elif quote == "JPY":
        rate = pit_quotes["USD_JPY"].asof(t)
        return 1.0 / rate if rate and rate > 0 else 1.0 / 150.0
    elif quote == "CAD":
        rate = pit_quotes["USD_CAD"].asof(t)
        return 1.0 / rate if rate and rate > 0 else 1.0 / 1.35
    elif quote == "CHF":
        rate = pit_quotes["USD_CHF"].asof(t)
        return 1.0 / rate if rate and rate > 0 else 1.0 / 0.88
    return 1.0

# 2. Extract Canonical F0 Trades & Assert Exact Parity
raw_events = []
for sym in PAIRS:
    df = dfs[sym]
    sig_df = generate_filtered_signals(df, sym)
    pip_val = get_pip_value(sym)
    spread_pips = SPREAD_PIPS_MAP.get(sym, 1.5)
    half_spread = (spread_pips / 2.0) * pip_val
    entry_slip = ENTRY_SLIPPAGE_PIPS * pip_val
    exit_slip = EXIT_SLIPPAGE_PIPS * pip_val

    for _, sig in sig_df.iterrows():
        if not sig["pass_f0"]:
            continue
        t = sig["timestamp"]
        entry_idx = df.index.get_loc(t) if t in df.index else None
        if entry_idx is None or entry_idx >= len(df) - 1:
            continue

        raw_entry = float(df["close"].iloc[entry_idx])
        side = sig["side"]
        stop_dist_quote = float(sig["stop_pips"]) * pip_val
        target_px = float(sig["target_px"])

        if side == "BUY":
            entry_px = raw_entry + half_spread + entry_slip
            stop_px = entry_px - stop_dist_quote
            if target_px <= entry_px:
                continue
        else:
            entry_px = raw_entry - half_spread - entry_slip
            stop_px = entry_px + stop_dist_quote
            if target_px >= entry_px:
                continue

        exit_px, exit_reason, bars_held, exit_time, mae_r, mfe_r = evaluate_path(
            df, entry_idx, side, entry_px, stop_px, target_px, pip_val, half_spread, exit_slip
        )

        quote_to_usd = get_quote_to_usd_rate(sym, t)
        target_dollar_risk = 500.0  # 0.50% on $100k
        usd_risk_per_unit = stop_dist_quote * quote_to_usd
        units = target_dollar_risk / usd_risk_per_unit if usd_risk_per_unit > 0 else 0
        quote_pnl = (exit_px - entry_px) * units if side == "BUY" else (entry_px - exit_px) * units
        net_pnl_usd = quote_pnl * quote_to_usd
        r_mult = net_pnl_usd / target_dollar_risk

        raw_events.append({
            "pair": sym,
            "side": side,
            "signal_time": t,
            "entry_time": t,
            "exit_time": exit_time,
            "entry_px": entry_px,
            "exit_px": exit_px,
            "stop_px": stop_px,
            "target_px": target_px,
            "stop_dist_quote": stop_dist_quote,
            "r_mult": r_mult,
            "exit_reason": exit_reason,
            "bars_held": bars_held,
            "entry_idx": entry_idx
        })

events_df = pd.DataFrame(raw_events).sort_values("entry_time").reset_index(drop=True)

# Parity Assertions
print(f"PARITY CHECK: Total Extracted Trades = {len(events_df)}")
if len(events_df) != 628:
    sys.exit(f"FATAL: Parity violation. Expected 628 trades, got {len(events_df)}. Aborting.")

baseline_mean_r = events_df["r_mult"].mean()
w = events_df[events_df["r_mult"] > 0]["r_mult"].sum()
l = abs(events_df[events_df["r_mult"] <= 0]["r_mult"].sum())
baseline_pf = w / l

print(f"PARITY CHECK: Mean Realized R        = {baseline_mean_r:+.3f}R (Expected: +0.101R)")
print(f"PARITY CHECK: Profit Factor          = {baseline_pf:.2f} (Expected: 1.16)")

if abs(baseline_mean_r - 0.101) > 0.005 or abs(baseline_pf - 1.16) > 0.02:
    sys.exit("FATAL: Expectancy or PF mismatch against frozen F0 control. Aborting.")
print(">>> F0_SIGNAL_PARITY = PASS <<<\n")

# 3. Master Chronological Portfolio Engine
all_times = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
time_to_idx = {p: {t: i for i, t in enumerate(dfs[p].index)} for p in dfs}

def simulate_h4_portfolio(events: pd.DataFrame, max_positions: int = 3, use_firewall: bool = True):
    cash = INITIAL_NAV
    active_positions = {}  # pid -> dict
    closed_trades = []
    equity_curve = []
    pid_counter = 0

    entry_map = {}
    for _, row in events.iterrows():
        entry_map.setdefault(row["entry_time"], []).append(row)

    for current_time in all_times:
        # A. Process Exits
        active_pids = list(active_positions.keys())
        for pid in active_pids:
            pos = active_positions[pid]
            if current_time >= pos["exit_time"]:
                quote_to_usd = get_quote_to_usd_rate(pos["pair"], pos["exit_time"])
                pnl_per_unit = (pos["exit_px"] - pos["entry_px"]) if pos["side"] == "BUY" else (pos["entry_px"] - pos["exit_px"])
                pnl_usd = pnl_per_unit * pos["units"] * quote_to_usd
                cash += pos["margin"] + pnl_usd

                closed_trades.append({
                    "pid": pid,
                    "pair": pos["pair"],
                    "side": pos["side"],
                    "entry_time": pos["entry_time"],
                    "exit_time": pos["exit_time"],
                    "r_mult": pos["r_mult"],
                    "pnl_usd": pnl_usd,
                    "bars_held": pos["bars_held"],
                    "year": pos["entry_time"].year
                })
                del active_positions[pid]

        # B. Mark-to-Market Valuation
        unrealized_usd = 0.0
        active_margin = 0.0
        curr_factors = {}

        for pid, pos in active_positions.items():
            pair = pos["pair"]
            active_margin += pos["margin"]
            if current_time in time_to_idx[pair]:
                c_idx = time_to_idx[pair][current_time]
                cur_close = float(dfs[pair]["close"].iloc[c_idx])
                quote_to_usd = get_quote_to_usd_rate(pair, current_time)
                pnl_per_unit = (cur_close - pos["entry_px"]) if pos["side"] == "BUY" else (pos["entry_px"] - cur_close)
                unrealized_usd += pnl_per_unit * pos["units"] * quote_to_usd

            base, quote = pair.split("_")
            d = 1 if pos["side"] == "BUY" else -1
            curr_factors[base] = curr_factors.get(base, 0) + d
            curr_factors[quote] = curr_factors.get(quote, 0) - d

        mtm_nav = cash + active_margin + unrealized_usd
        equity_curve.append({"time": current_time, "mtm_nav": mtm_nav, "open_pos": len(active_positions)})

        # C. Screen & Size New Entries
        if current_time in entry_map:
            for cand in entry_map[current_time]:
                if len(active_positions) >= max_positions:
                    break

                pair = cand["pair"]
                if any(p["pair"] == pair for p in active_positions.values()):
                    continue

                base, quote = pair.split("_")
                d = 1 if cand["side"] == "BUY" else -1

                if use_firewall:
                    if abs(curr_factors.get(base, 0) + d) > MAX_FACTOR_EXPOSURE:
                        continue
                    if abs(curr_factors.get(quote, 0) - d) > MAX_FACTOR_EXPOSURE:
                        continue

                quote_to_usd = get_quote_to_usd_rate(pair, current_time)
                target_risk_usd = mtm_nav * RISK_PCT
                risk_per_unit_usd = cand["stop_dist_quote"] * quote_to_usd

                if risk_per_unit_usd <= 0:
                    continue

                units = target_risk_usd / risk_per_unit_usd
                margin_req = (units * cand["entry_px"] * quote_to_usd) * 0.0333  # 30:1 leverage

                if cash >= margin_req and units > 0:
                    cash -= margin_req
                    pid_counter += 1
                    active_positions[pid_counter] = {
                        "pair": pair,
                        "side": cand["side"],
                        "entry_time": cand["entry_time"],
                        "exit_time": cand["exit_time"],
                        "entry_px": cand["entry_px"],
                        "exit_px": cand["exit_px"],
                        "units": units,
                        "margin": margin_req,
                        "r_mult": cand["r_mult"],
                        "bars_held": cand["bars_held"]
                    }
                    curr_factors[base] = curr_factors.get(base, 0) + d
                    curr_factors[quote] = curr_factors.get(quote, 0) - d

    nav_df = pd.DataFrame(equity_curve).set_index("time")
    nav_df["peak"] = nav_df["mtm_nav"].cummax()
    nav_df["dd_pct"] = (nav_df["mtm_nav"] - nav_df["peak"]) / nav_df["peak"] * 100.0
    trades_df = pd.DataFrame(closed_trades)
    return nav_df, trades_df

def print_portfolio_metrics(name: str, nav_df: pd.DataFrame, trades_df: pd.DataFrame):
    start_nav = nav_df["mtm_nav"].iloc[0]
    final_nav = nav_df["mtm_nav"].iloc[-1]
    years = (nav_df.index[-1] - nav_df.index[0]).days / 365.25
    cagr = ((final_nav / start_nav) ** (1.0 / years) - 1.0) * 100.0 if years > 0 else 0.0
    max_dd = nav_df["dd_pct"].min()
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0.0

    wins = trades_df[trades_df["r_mult"] > 0]["r_mult"].sum()
    losses = abs(trades_df[trades_df["r_mult"] <= 0]["r_mult"].sum())
    pf = wins / losses if losses > 0 else float("inf")
    mean_r = trades_df["r_mult"].mean() if not trades_df.empty else 0.0
    wr = (trades_df["r_mult"] > 0).mean() * 100.0 if not trades_df.empty else 0.0

    print(f"=== {name} ===")
    print(f"Trades Executed:   {len(trades_df):<4} (Capacity: {len(trades_df)/628*100:4.1f}% of F0 universe)")
    print(f"Win Rate:          {wr:4.1f}%")
    print(f"Net Mean R:        {mean_r:+6.3f}R")
    print(f"Profit Factor:     {pf:4.2f}")
    print(f"Start NAV:         ${start_nav:,.2f}")
    print(f"Final NAV:         ${final_nav:,.2f}")
    print(f"Compounded CAGR:   {cagr:+5.2f}%")
    print(f"Max True MTM DD:   {max_dd:5.2f}%")
    print(f"Calmar Ratio:      {calmar:4.2f}")
    print("-" * 50)
    print("Year-by-Year Compounded Return & Metrics:")
    for yr, group in trades_df.groupby("year"):
        y_wins = group[group["r_mult"] > 0]["r_mult"].sum()
        y_losses = abs(group[group["r_mult"] <= 0]["r_mult"].sum())
        y_pf = y_wins / y_losses if y_losses > 0 else float("inf")
        print(f"  {yr} | N: {len(group):<3} | WR: {(group['r_mult']>0).mean()*100:4.1f}% | Mean R: {group['r_mult'].mean():+6.3f}R | PF: {y_pf:4.2f} | PnL: ${group['pnl_usd'].sum():+9.2f}")
    print()

# Run all 3 configurations
nav1, t1 = simulate_h4_portfolio(events_df, max_positions=999, use_firewall=False)
print_portfolio_metrics("PORTFOLIO 1: F0 UNCONSTRAINED (All 628 Concurrent Trades)", nav1, t1)

nav2, t2 = simulate_h4_portfolio(events_df, max_positions=3, use_firewall=False)
print_portfolio_metrics("PORTFOLIO 2: F0 CONSTRAINED (Max 3 Positions, No Factor Cap)", nav2, t2)

nav3, t3 = simulate_h4_portfolio(events_df, max_positions=3, use_firewall=True)
print_portfolio_metrics("PORTFOLIO 3: F0 HARDENED GOVERNED (Max 3 Positions + Factor Firewall)", nav3, t3)
print("=" * 95)
