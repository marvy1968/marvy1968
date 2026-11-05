#!/usr/bin/env python3
"""
M15 1.0-Sigma Mean Reversion Benchmark Suite (6 Cohorts)
Tests 20, 50, and 100 SMA with 1.0-Sigma Envelopes:
- Cohort 1A: 20 SMA  | Entry @ +/-1.0-Sigma | Exit @ 20 SMA Centerline (MR-1)
- Cohort 1B: 20 SMA  | Entry @ +/-1.0-Sigma | Exit @ Opposite +/-1.0-Sigma (MR-2)
- Cohort 2A: 50 SMA  | Entry @ +/-1.0-Sigma | Exit @ 50 SMA Centerline (MR-1)
- Cohort 2B: 50 SMA  | Entry @ +/-1.0-Sigma | Exit @ Opposite +/-1.0-Sigma (MR-2)
- Cohort 3A: 100 SMA | Entry @ +/-1.0-Sigma | Exit @ 100 SMA Centerline (MR-1)
- Cohort 3B: 100 SMA | Entry @ +/-1.0-Sigma | Exit @ Opposite +/-1.0-Sigma (MR-2)

Invalidation Stop: 2.0-Sigma from MA at time of entry.
"""

import os
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

DATA_DIR = Path("/home/moonrider1968/bots/oanda_data")
PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]

INITIAL_EQUITY = 100_000.0
RISK_PCT = 0.0050          # 0.50% equity risk per trade ($500 on $100k)
ENTRY_SLIPPAGE_PIPS = 0.25
EXIT_SLIPPAGE_PIPS = 0.25

SPREAD_PIPS_MAP = {
    "EUR_USD": 1.0, "GBP_USD": 1.4, "USD_JPY": 1.1, "AUD_USD": 1.2,
    "USD_CAD": 1.5, "NZD_USD": 1.6, "USD_CHF": 1.5, "EUR_JPY": 1.6, "GBP_JPY": 2.2
}

@dataclass
class TradeRecord:
    symbol: str
    side: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    risk_pips: float
    units: float
    dollar_risk: float
    net_pnl_usd: float
    r_multiple: float
    exit_reason: str
    bars_held: int

def get_pip_value(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001

def load_local_dataset(symbol: str, granularity: str = "M15") -> pd.DataFrame:
    sub = granularity.lower()
    fpath = DATA_DIR / sub / f"{symbol}_{granularity}.csv"
    if not fpath.exists():
        raise FileNotFoundError(f"Missing cached data: {fpath}")
    df = pd.read_csv(fpath)
    time_col = next(c for c in df.columns if c.lower() in ["timestamp", "time", "date"])
    df[time_col] = pd.to_datetime(df[time_col], utc=True)
    return df.sort_values(time_col).set_index(time_col)

def generate_mr_signals(df_m15: pd.DataFrame, ma_period: int, symbol: str) -> pd.DataFrame:
    df = df_m15.copy()
    pip_val = get_pip_value(symbol)
    
    df["ma"] = df["close"].rolling(ma_period).mean()
    df["std"] = df["close"].rolling(ma_period).std()
    
    df["upper_1s"] = df["ma"] + (1.0 * df["std"])
    df["lower_1s"] = df["ma"] - (1.0 * df["std"])
    df["upper_2s"] = df["ma"] + (2.0 * df["std"])
    df["lower_2s"] = df["ma"] - (2.0 * df["std"])
    
    signals = []
    # Start after warm-up
    for i in range(ma_period + 5, len(df) - 5):
        cur = df.iloc[i]
        prev = df.iloc[i - 1]
        t = df.index[i]
        
        # Long Reclaim: Prev bar closed below -1.0s, Cur bar closed back above -1.0s
        if prev["close"] < prev["lower_1s"] and cur["close"] > cur["lower_1s"] and cur["close"] < cur["ma"]:
            stop_dist_quote = cur["close"] - cur["lower_2s"]
            stop_pips = max(6.0, stop_dist_quote / pip_val)
            signals.append({
                "timestamp": t,
                "side": "BUY",
                "stop_pips": round(stop_pips, 1),
                "ma_center": cur["ma"],
                "opp_band": cur["upper_1s"],
                "lower_2s_stop": cur["lower_2s"]
            })
            
        # Short Reclaim: Prev bar closed above +1.0s, Cur bar closed back below +1.0s
        elif prev["close"] > prev["upper_1s"] and cur["close"] < cur["upper_1s"] and cur["close"] > cur["ma"]:
            stop_dist_quote = cur["upper_2s"] - cur["close"]
            stop_pips = max(6.0, stop_dist_quote / pip_val)
            signals.append({
                "timestamp": t,
                "side": "SELL",
                "stop_pips": round(stop_pips, 1),
                "ma_center": cur["ma"],
                "opp_band": cur["lower_1s"],
                "upper_2s_stop": cur["upper_2s"]
            })
            
    return pd.DataFrame(signals)

def evaluate_mr_path(
    df: pd.DataFrame,
    entry_idx: int,
    side: str,
    entry_px: float,
    stop_px: float,
    target_px: float,
    pip_val: float,
    half_spread: float,
    exit_slip: float
) -> Tuple[float, str, int, pd.Timestamp]:
    for count, idx in enumerate(range(entry_idx + 1, len(df)), start=1):
        row = df.iloc[idx]
        open_px, high_px, low_px = float(row["open"]), float(row["high"]), float(row["low"])
        t = df.index[idx]
        
        if side == "BUY":
            if open_px <= stop_px: return open_px - half_spread - exit_slip, "STOP_HIT", count, t
            if open_px >= target_px: return open_px - half_spread - exit_slip, "TARGET_HIT", count, t
            if low_px <= stop_px: return stop_px - half_spread - exit_slip, "STOP_HIT", count, t
            if high_px >= target_px: return target_px - half_spread - exit_slip, "TARGET_HIT", count, t
        else:
            if open_px >= stop_px: return open_px + half_spread + exit_slip, "STOP_HIT", count, t
            if open_px <= target_px: return open_px + half_spread + exit_slip, "TARGET_HIT", count, t
            if high_px >= stop_px: return stop_px + half_spread + exit_slip, "STOP_HIT", count, t
            if low_px <= target_px: return target_px + half_spread + exit_slip, "TARGET_HIT", count, t
            
    last_row = df.iloc[-1]
    px = float(last_row["close"]) - half_spread - exit_slip if side == "BUY" else float(last_row["close"]) + half_spread + exit_slip
    return px, "DATASET_END", len(df) - entry_idx, df.index[-1]

def run_mr_simulation(
    symbol: str,
    df: pd.DataFrame,
    signals: pd.DataFrame,
    exit_style: str,   # 'MR-1' (Centerline) or 'MR-2' (Opposite Band)
    pit_usdjpy_series: Optional[pd.Series]
) -> Tuple[List[TradeRecord], dict]:
    pip_val = get_pip_value(symbol)
    spread_pips = SPREAD_PIPS_MAP.get(symbol, 1.5)
    half_spread = (spread_pips / 2.0) * pip_val
    entry_slip = ENTRY_SLIPPAGE_PIPS * pip_val
    exit_slip = EXIT_SLIPPAGE_PIPS * pip_val
    
    trades: List[TradeRecord] = []
    base, quote = symbol.split("_")

    for _, sig in signals.iterrows():
        entry_idx = df.index.get_loc(sig["timestamp"]) if sig["timestamp"] in df.index else None
        if entry_idx is None or entry_idx >= len(df) - 1:
            continue
            
        raw_entry = float(df["close"].iloc[entry_idx])
        side = sig["side"]
        stop_dist_quote = float(sig["stop_pips"]) * pip_val
        
        if side == "BUY":
            entry_px = raw_entry + half_spread + entry_slip
            stop_px = entry_px - stop_dist_quote
            target_px = sig["ma_center"] if exit_style == "MR-1" else sig["opp_band"]
            # Ensure target is above entry
            if target_px <= entry_px:
                continue
        else:
            entry_px = raw_entry - half_spread - entry_slip
            stop_px = entry_px + stop_dist_quote
            target_px = sig["ma_center"] if exit_style == "MR-1" else sig["opp_band"]
            # Ensure target is below entry
            if target_px >= entry_px:
                continue

        t_entry = sig["timestamp"]
        if quote == "USD":
            quote_to_usd = 1.0
        elif base == "USD":
            quote_to_usd = 1.0 / entry_px if entry_px > 0 else 1.0
        elif quote == "JPY" and pit_usdjpy_series is not None:
            uj_val = pit_usdjpy_series.asof(t_entry)
            quote_to_usd = 1.0 / uj_val if pd.notna(uj_val) and uj_val > 0 else 1.0 / 150.0
        else:
            quote_to_usd = 1.0

        usd_risk_per_unit = stop_dist_quote * quote_to_usd
        target_dollar_risk = INITIAL_EQUITY * RISK_PCT
        units = target_dollar_risk / usd_risk_per_unit if usd_risk_per_unit > 0 else 0
        if units <= 0:
            continue
            
        exit_px, reason, bars_held, exit_time = evaluate_mr_path(
            df, entry_idx, side, entry_px, stop_px, target_px, pip_val, half_spread, exit_slip
        )
        
        quote_pnl = (exit_px - entry_px) * units if side == "BUY" else (entry_px - exit_px) * units
        net_pnl_usd = quote_pnl * quote_to_usd
        r_mult = net_pnl_usd / target_dollar_risk

        trades.append(TradeRecord(
            symbol=symbol, side=side, entry_time=sig["timestamp"], exit_time=exit_time,
            entry_price=entry_px, exit_price=exit_px, stop_price=stop_px, target_price=target_px,
            risk_pips=float(sig["stop_pips"]), units=units, dollar_risk=target_dollar_risk,
            net_pnl_usd=net_pnl_usd, r_multiple=r_mult, exit_reason=reason, bars_held=bars_held
        ))
        
    if not trades:
        return [], {}
        
    r_mults = [t.r_multiple for t in trades]
    wins = [r for r in r_mults if r > 0]
    losses = [abs(r) for r in r_mults if r < 0]
    target_hits = [t for t in trades if t.exit_reason == "TARGET_HIT"]
    stop_hits = [t for t in trades if t.exit_reason == "STOP_HIT"]
    pf_r = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (99.0 if wins else 0.0)
    
    return trades, {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100.0,
        "pf_r": pf_r,
        "mean_r": float(np.mean(r_mults)),
        "median_r": float(np.median(r_mults)),
        "target_hit_pct": len(target_hits) / len(trades) * 100.0,
        "stop_hit_pct": len(stop_hits) / len(trades) * 100.0
    }

def print_cohort_report(cohort_id: str, title: str, results: Dict[str, dict], all_trades: List[TradeRecord]):
    print("\n" + "=" * 125)
    print(f" 🧪 COHORT {cohort_id}: {title}")
    print("=" * 125)
    print(f"{'Symbol':<10} | {'Events':<6} | {'Target %':<8} | {'Stop %':<7} | {'Win %':<6} | {'Mean R':<7} | {'PF (R)':<6} | {'Median R':<8}")
    print("-" * 125)
    for sym, m in results.items():
        print(f"{sym:<10} | {m['trades']:<6d} | {m['target_hit_pct']:>7.1f}% | {m['stop_hit_pct']:>6.1f}% | {m['win_rate']:>5.1f}% | {m['mean_r']:>+6.3f}R | {m['pf_r']:>6.2f} | {m['median_r']:>+7.3f}R")
    print("=" * 125)

    all_r = [t.r_multiple for t in all_trades]
    wins_r = [r for r in all_r if r > 0]
    loss_r = [abs(r) for r in all_r if r < 0]
    pooled_pf = (sum(wins_r) / sum(loss_r)) if loss_r and sum(loss_r) > 0 else 0.0
    targets = [t for t in all_trades if t.exit_reason == "TARGET_HIT"]
    stops = [t for t in all_trades if t.exit_reason == "STOP_HIT"]
    pos_pairs = sum(1 for m in results.values() if m["mean_r"] > 0)

    print(f" [POOLED STATS] Events: {len(all_trades)} | Win Rate: {len(wins_r)/len(all_trades)*100:.1f}% | Mean R: {np.mean(all_r):+.3f}R | PF (R): {pooled_pf:.2f} | Pos Pairs: {pos_pairs}/9")
    print("=" * 125)
    return {
        "cohort": cohort_id,
        "title": title,
        "trades": len(all_trades),
        "win_rate": len(wins_r)/len(all_trades)*100.0,
        "mean_r": float(np.mean(all_r)),
        "pf_r": pooled_pf,
        "target_pct": len(targets)/len(all_trades)*100.0,
        "stop_pct": len(stops)/len(all_trades)*100.0,
        "pos_pairs": pos_pairs
    }

def main():
    print("=" * 105)
    print(" 🚀 RUNNING 6-COHORT M15 1.0-SIGMA MEAN REVERSION BENCHMARK")
    print("=" * 105)

    df_uj = load_local_dataset("USD_JPY", "H1")
    pit_usdjpy = df_uj["close"].shift(1)

    dfs_m15 = {sym: load_local_dataset(sym, "M15") for sym in PAIRS}

    cohort_configs = [
        ("1A", 20,  "MR-1", "20 SMA  | 1.0-Sigma -> MA Centerline (MR-1)"),
        ("1B", 20,  "MR-2", "20 SMA  | 1.0-Sigma -> Opposite Band (MR-2)"),
        ("2A", 50,  "MR-1", "50 SMA  | 1.0-Sigma -> MA Centerline (MR-1)"),
        ("2B", 50,  "MR-2", "50 SMA  | 1.0-Sigma -> Opposite Band (MR-2)"),
        ("3A", 100, "MR-1", "100 SMA | 1.0-Sigma -> MA Centerline (MR-1)"),
        ("3B", 100, "MR-2", "100 SMA | 1.0-Sigma -> Opposite Band (MR-2)")
    ]

    cohort_summaries = []

    for cohort_id, ma_len, exit_mode, title in cohort_configs:
        res, trades = {}, []
        for sym in PAIRS:
            sig_df = generate_mr_signals(dfs_m15[sym], ma_len, sym)
            if not sig_df.empty:
                t, m = run_mr_simulation(sym, dfs_m15[sym], sig_df, exit_mode, pit_usdjpy)
                if m:
                    res[sym] = m
                    trades.extend(t)
        if res:
            cohort_summaries.append(print_cohort_report(cohort_id, title, res, trades))

    print("\n" + "=" * 100)
    print(" 🏁 6-COHORT MEAN REVERSION BENCHMARK MATRIX")
    print("=" * 100)
    print(f"{'Cohort':<8} | {'Strategy Setup':<42} | {'Events':<6} | {'Win %':<6} | {'Mean R':<7} | {'PF (R)':<6} | {'Pos Pairs':<9}")
    print("-" * 100)
    for c in cohort_summaries:
        print(f"{c['cohort']:<8} | {c['title']:<42} | {c['trades']:<6d} | {c['win_rate']:>5.1f}% | {c['mean_r']:>+6.3f}R | {c['pf_r']:>6.2f} | {c['pos_pairs']:>2d}/9")
    print("=" * 100)

if __name__ == "__main__":
    main()
