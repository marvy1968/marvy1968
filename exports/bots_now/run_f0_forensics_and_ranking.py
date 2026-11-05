#!/usr/bin/env python3
import sys
import numpy as np
import pandas as pd
from pathlib import Path

from run_f0_hardened_portfolio_v2 import (
    PAIRS,
    load_local_dataset,
    generate_filtered_signals,
    evaluate_path,
    get_pip_value,
    SPREAD_PIPS_MAP,
    ENTRY_SLIPPAGE_PIPS,
    EXIT_SLIPPAGE_PIPS,
    get_quote_to_usd_rate,
    dfs,
    all_times,
    time_to_idx,
    INITIAL_NAV,
    RISK_PCT,
    MAX_POSITIONS,
    MAX_FACTOR_EXPOSURE
)

SPLIT_DATE = pd.to_datetime("2025-01-01", utc=True)

print("=" * 95)
print("PANDA FX — F0 REGIME FORENSICS & DETERMINISTIC CAPACITY RANKING (HARDENED)")
print("=" * 95)

# 1. Build Canonical F0 Trade Universe with Strict Next-Bar Open Execution
records = []
for sym in PAIRS:
    df = dfs[sym]
    sig_df = generate_filtered_signals(df, sym)
    pip_val = get_pip_value(sym)
    half_spread = (SPREAD_PIPS_MAP.get(sym, 1.5) / 2.0) * pip_val
    entry_slip = ENTRY_SLIPPAGE_PIPS * pip_val
    exit_slip = EXIT_SLIPPAGE_PIPS * pip_val

    for _, sig in sig_df.iterrows():
        if not sig["pass_f0"]:
            continue
        t = sig["timestamp"]
        idx = df.index.get_loc(t) if t in df.index else None
        if idx is None or idx < 105 or idx >= len(df) - 5:
            continue

        # Signal-time metrics known at bar close (idx)
        cur_close = float(df["close"].iloc[idx])
        prev_close = float(df["close"].iloc[idx - 1])

        # PIT-safe F0 geometry. Raw dfs do not contain indicator columns.
        # Current signal-bar SMA/std use bars through idx.
        cur_window = df["close"].iloc[idx-99:idx+1]
        ma100 = float(cur_window.mean())
        std100 = float(cur_window.std())

        # Previous-bar bands must use information only through idx-1.
        prev_window = df["close"].iloc[idx-100:idx]
        prev_ma100 = float(prev_window.mean())
        prev_std100 = float(prev_window.std())
        upper_25s = prev_ma100 + 2.5 * prev_std100
        lower_25s = prev_ma100 - 2.5 * prev_std100

        side = sig["side"]

        # Canonical execution strictly on next bar open (idx + 1)
        entry_idx = idx + 1
        entry_time = df.index[entry_idx]
        raw_entry = float(df["open"].iloc[entry_idx])
        stop_dist_quote = float(sig["stop_pips"]) * pip_val
        target_px = float(sig["target_px"])

        if side == "BUY":
            entry_px = raw_entry + half_spread + entry_slip
            stop_px = entry_px - stop_dist_quote
            if target_px <= entry_px:
                continue
            excursion = (lower_25s - prev_close)
            target_dist = target_px - entry_px
        else:
            entry_px = raw_entry - half_spread - entry_slip
            stop_px = entry_px + stop_dist_quote
            if target_px >= entry_px:
                continue
            excursion = (prev_close - upper_25s)
            target_dist = entry_px - target_px

        exit_px, exit_reason, bars_held, exit_time, mae_r, mfe_r = evaluate_path(
            df, entry_idx, side, entry_px, stop_px, target_px, pip_val, half_spread, exit_slip
        )

        quote_to_usd = get_quote_to_usd_rate(sym, entry_time)
        target_dollar_risk = 500.0
        usd_risk_per_unit = stop_dist_quote * quote_to_usd
        units = target_dollar_risk / usd_risk_per_unit if usd_risk_per_unit > 0 else 0
        quote_pnl = (exit_px - entry_px) * units if side == "BUY" else (entry_px - exit_px) * units
        net_pnl_usd = quote_pnl * quote_to_usd
        r_mult = net_pnl_usd / target_dollar_risk

        # Signal-time ranking features (uncontaminated at idx)
        net_chg_20 = abs(cur_close - float(df["close"].iloc[idx - 20]))
        gross_chg_20 = (df["high"].iloc[idx-19:idx+1] - df["low"].iloc[idx-19:idx+1]).sum()
        er20 = net_chg_20 / (gross_chg_20 + 1e-9)
        # PIT-safe SMA100 value five completed H4 bars earlier.
        ma100_prev5 = float(df["close"].iloc[idx-104:idx-4].mean())
        slope100 = (ma100 - ma100_prev5) / ma100 * 1000.0
        bw_pct = (5.0 * std100) / ma100 * 100.0
        excursion_sigma = excursion / (std100 + 1e-9)
        target_r = target_dist / stop_dist_quote

        records.append({
            "pair": sym,
            "side": side,
            "signal_time": t,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "entry_px": entry_px,
            "exit_px": exit_px,
            "stop_px": stop_px,
            "target_px": target_px,
            "stop_dist_quote": stop_dist_quote,
            "r_mult": r_mult,
            "bars_held": bars_held,
            "year": entry_time.year,
            "split": "DEV" if entry_time < SPLIT_DATE else "VAL",
            # Ranking metrics
            "excursion_sigma": excursion_sigma,
            "target_r": target_r,
            "bw_pct": bw_pct,
            "er20": er20,
            "slope100_abs": abs(slope100),
        })

f_df = pd.DataFrame(records).sort_values("entry_time").reset_index(drop=True)
print(f"Extracted Trades: {len(f_df)}")
print(f"Mean Expectancy:  {f_df['r_mult'].mean():+.3f}R")
w_r = f_df[f_df["r_mult"] > 0]["r_mult"].sum()
l_r = abs(f_df[f_df["r_mult"] <= 0]["r_mult"].sum())
print(f"Profit Factor:    {w_r / l_r:.2f}")

# =====================================================================
# PART 1: 2024 REGIME FORENSICS
# =====================================================================
print("\n" + "-" * 95)
print("1. 2024 FORENSIC DECOMPOSITION (Failure Regime vs Winning Cohorts)")
print("-" * 95)
y24 = f_df[f_df["year"] == 2024]
y_win = f_df[f_df["year"].isin([2023, 2025, 2026])]

forensic_metrics = [
    ("Trade Count (N)", lambda d: f"{len(d)}"),
    ("Win Rate (%)", lambda d: f"{(d['r_mult'] > 0).mean()*100:.1f}%"),
    ("Net Expectancy (Mean R)", lambda d: f"{d['r_mult'].mean():+.3f}R"),
    ("Excursion Beyond 2.5s (Sigma)", lambda d: f"{d['excursion_sigma'].mean():.3f}s"),
    ("Initial Target R (Reward/Risk)", lambda d: f"{d['target_r'].mean():.2f}R"),
    ("Bandwidth % (5-Sigma / MA100)", lambda d: f"{d['bw_pct'].mean():.2f}%"),
    ("Directional Efficiency (ER20)", lambda d: f"{d['er20'].mean():.3f}"),
    ("100-SMA Absolute Slope (x1000)", lambda d: f"{d['slope100_abs'].mean():.3f}"),
]

print(f"{'Metric':<35} | {'2024 Failure Cohort':<22} | {'Winning Cohorts (23/25/26)':<26} |")
print("-" * 95)
for name, fn in forensic_metrics:
    print(f"{name:<35} | {fn(y24):<22} | {fn(y_win):<26} |")

# =====================================================================
# PART 2: DETERMINISTIC CAPACITY RANKING ENGINE (DEV vs VAL)
# =====================================================================
print("\n" + "-" * 95)
print("2. DETERMINISTIC CAPACITY RANKING BAKE-OFF (Max 3 Pos + Factor Firewall)")
print("DEV: 2023-07-10 to 2024-12-31 | VAL (Isolated): 2025-01-01 to 2026-08-24")
print("-" * 95)

def run_ranked_simulation(events: pd.DataFrame, ranking_key=None):
    cash = INITIAL_NAV
    active_positions = {}
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
                    "pair": pos["pair"],
                    "entry_time": pos["entry_time"],
                    "exit_time": pos["exit_time"],
                    "r_mult": pos["r_mult"],
                    "pnl_usd": pnl_usd,
                    "year": pos["entry_time"].year,
                    "split": "DEV" if pos["entry_time"] < SPLIT_DATE else "VAL"
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
        equity_curve.append({"time": current_time, "mtm_nav": mtm_nav})

        # C. Prioritized Deterministic Admission
        if current_time in entry_map:
            cands = entry_map[current_time]
            # Deterministic sorts with explicit pair/side tie-breakers
            if ranking_key is None:
                cands = sorted(cands, key=lambda x: (x["pair"], x["side"]))
            else:
                cands = sorted(cands, key=lambda x: (-x[ranking_key], x["pair"], x["side"]))

            for cand in cands:
                if len(active_positions) >= MAX_POSITIONS:
                    break

                pair = cand["pair"]
                if any(p["pair"] == pair for p in active_positions.values()):
                    continue

                base, quote = pair.split("_")
                d = 1 if cand["side"] == "BUY" else -1

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
                margin_req = (units * cand["entry_px"] * quote_to_usd) * 0.0333

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
                        "r_mult": cand["r_mult"]
                    }
                    curr_factors[base] = curr_factors.get(base, 0) + d
                    curr_factors[quote] = curr_factors.get(quote, 0) - d

    nav_df = pd.DataFrame(equity_curve).set_index("time")
    trades_df = pd.DataFrame(closed_trades)
    return nav_df, trades_df

def calc_isolated_stats(trades_sub, nav_sub):
    if trades_sub.empty or nav_sub.empty:
        return {"N": 0, "Exp_R": "+0.000R", "PF": "0.00", "CAGR": "+0.00%", "MaxDD": "0.00%"}
    wins = trades_sub[trades_sub["r_mult"] > 0]["r_mult"].sum()
    losses = abs(trades_sub[trades_sub["r_mult"] <= 0]["r_mult"].sum())
    pf = wins / losses if losses > 0 else float("inf")
    
    # Isolate equity curve and reset peak for clean partition stats
    s_nav = nav_sub.copy()
    start = s_nav["mtm_nav"].iloc[0]
    end = s_nav["mtm_nav"].iloc[-1]
    years = (s_nav.index[-1] - s_nav.index[0]).days / 365.25
    cagr = ((end / start) ** (1.0 / years) - 1.0) * 100.0 if years > 0 else 0.0
    
    s_nav["local_peak"] = s_nav["mtm_nav"].cummax()
    s_nav["local_dd"] = (s_nav["mtm_nav"] - s_nav["local_peak"]) / s_nav["local_peak"] * 100.0
    
    return {
        "N": len(trades_sub),
        "Exp_R": f"{trades_sub['r_mult'].mean():+.3f}R",
        "PF": f"{pf:.2f}",
        "CAGR": f"{cagr:+.2f}%",
        "MaxDD": f"{s_nav['local_dd'].min():.2f}%"
    }

ranking_schemes = [
    ("Rank A: Deterministic FIFO (Control)", None),
    ("Rank B: Excursion Depth (Deepest Sigma)", "excursion_sigma"),
    ("Rank C: Reward/Risk (Highest Target_R)", "target_r"),
    ("Rank D: Band Expansion (Highest Bandwidth)", "bw_pct"),
]

summary_records = []
for label, key in ranking_schemes:
    nav, tr = run_ranked_simulation(f_df, ranking_key=key)
    dev_tr = tr[tr["split"] == "DEV"]
    val_tr = tr[tr["split"] == "VAL"]
    dev_nav = nav.loc[:SPLIT_DATE]
    val_nav = nav.loc[SPLIT_DATE:]

    s_all = calc_isolated_stats(tr, nav)
    s_dev = calc_isolated_stats(dev_tr, dev_nav)
    s_val = calc_isolated_stats(val_tr, val_nav)

    summary_records.append({
        "Model": label,
        "DEV N": s_dev["N"], "DEV Exp": s_dev["Exp_R"], "DEV PF": s_dev["PF"], "DEV DD": s_dev["MaxDD"],
        "VAL N": s_val["N"], "VAL Exp": s_val["Exp_R"], "VAL PF": s_val["PF"], "VAL CAGR": s_val["CAGR"], "VAL DD": s_val["MaxDD"],
        "FULL CAGR": s_all["CAGR"], "FULL DD": s_all["MaxDD"]
    })

sum_df = pd.DataFrame(summary_records)
print(sum_df.to_string(index=False))
print("=" * 95)
