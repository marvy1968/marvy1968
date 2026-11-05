#!/usr/bin/env python3
import sys
import numpy as np
import pandas as pd
from pathlib import Path

from run_h4_filter_sweep import (
    PAIRS,
    load_local_dataset,
    generate_filtered_signals as f0_signal_generator,
    evaluate_path,
    get_pip_value,
    SPREAD_PIPS_MAP,
    ENTRY_SLIPPAGE_PIPS,
    EXIT_SLIPPAGE_PIPS
)

# Pull the certified execution engine components from v2
import run_f0_hardened_portfolio_v2 as engine

INITIAL_NAV = engine.INITIAL_NAV
RISK_PCT = engine.RISK_PCT
MAX_POSITIONS = engine.MAX_POSITIONS
MAX_FACTOR_EXPOSURE = engine.MAX_FACTOR_EXPOSURE
dfs = engine.dfs
get_quote_to_usd_rate = engine.get_quote_to_usd_rate
simulate_h4_portfolio = engine.simulate_h4_portfolio
print_portfolio_metrics = engine.print_portfolio_metrics

def build_events_from_signals(signal_func, strategy_name="CHALLENGER"):
    """
    Feeds pure signals through the canonical evaluate_path engine to produce
    the exact standardized events DataFrame required by simulate_h4_portfolio.
    """
    raw_events = []
    for sym in PAIRS:
        df = dfs[sym]
        sig_df = signal_func(df, sym)
        pip_val = get_pip_value(sym)
        spread_pips = SPREAD_PIPS_MAP.get(sym, 1.5)
        half_spread = (spread_pips / 2.0) * pip_val
        entry_slip = ENTRY_SLIPPAGE_PIPS * pip_val
        exit_slip = EXIT_SLIPPAGE_PIPS * pip_val

        for _, sig in sig_df.iterrows():
            if "pass_filter" in sig and not sig["pass_filter"]:
                continue
            if "pass_f0" in sig and not sig["pass_f0"]:
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

    if not raw_events:
        return pd.DataFrame()
    return pd.DataFrame(raw_events).sort_values("entry_time").reset_index(drop=True)

def verify_canonical_f0_parity():
    print("=" * 80)
    print("ASSERTING CANONICAL ENGINE PARITY (F0 CONTROL)")
    print("=" * 80)
    f0_events = build_events_from_signals(f0_signal_generator, "F0_CANONICAL")
    
    # Run Portfolio 3: Max 3 positions + Factor Firewall
    nav3, t3 = simulate_h4_portfolio(f0_events, max_positions=3, use_firewall=True)
    
    n_trades = len(t3)
    start_nav = nav3["mtm_nav"].iloc[0]
    final_nav = nav3["mtm_nav"].iloc[-1]
    years = (nav3.index[-1] - nav3.index[0]).days / 365.25
    cagr = ((final_nav / start_nav) ** (1.0 / years) - 1.0) * 100.0
    max_dd = nav3["dd_pct"].min()
    
    wins = t3[t3["r_mult"] > 0]["r_mult"].sum()
    losses = abs(t3[t3["r_mult"] <= 0]["r_mult"].sum())
    pf = wins / losses if losses > 0 else float("inf")

    print(f"Results: Trades={n_trades} | CAGR={cagr:+.2f}% | MaxDD={max_dd:.2f}% | PF={pf:.2f}")

    if n_trades != 278 or abs(cagr - 3.85) > 0.10 or abs(abs(max_dd) - 16.61) > 0.20:
        print("\n" + "!" * 80)
        print("FATAL: CANONICAL ENGINE PARITY VIOLATION")
        print("Expected: 278 trades, +3.85% CAGR, -16.61% MaxDD, 1.15 PF")
        print("Aborting. No challenger will run on an uncertified engine.")
        print("!" * 80)
        sys.exit(1)

    print(">>> PARITY CHECK PASSED: Hardened engine verified against canonical F0 baseline.\n")
    return True

if __name__ == "__main__":
    verify_canonical_f0_parity()
