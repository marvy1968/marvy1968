#!/usr/bin/env python3
"""
MicroBeast 4-Cohort Inversion Diagnostic Suite (Pure Event-Study)
- Cohort A1: H1->M5 Continuation (Original)
- Cohort A2: H1->M5 Inverted (Exhaustion Fade)
- Cohort B1: Native M15 Continuation (Original)
- Cohort B2: Native M15 Inverted (Exhaustion Fade)

Mechanics:
- Pure event study: 1-to-1 timestamp matching between Original and Inverted cohorts (no overlap suppression)
- Strict PIT USD/JPY conversion via df_uj["close"].shift(1)
- Identical true USD risk per trade ($500 on $100k equity baseline)
- Exact bid/ask spread + slippage modeling on entries and exits
"""

import os
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

# Fail-closed credential validation
env_path = Path("/home/moonrider1968/bots/.env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

OANDA_TOKEN = os.getenv("OANDA_API_KEY") or os.getenv("OANDA_TOKEN")
if not OANDA_TOKEN:
    raise RuntimeError("Missing OANDA_API_KEY / OANDA_TOKEN in environment or .env")

DATA_DIR = Path("/home/moonrider1968/bots/oanda_data")
PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]

INITIAL_EQUITY = 100_000.0
RISK_PCT = 0.0050          # 0.50% equity risk per trade ($500 on $100k)
TARGET_R = 1.50
STOP_R = 1.00
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

def load_local_dataset(symbol: str, granularity: str) -> pd.DataFrame:
    sub = granularity.lower()
    fpath = DATA_DIR / sub / f"{symbol}_{granularity}.csv"
    if not fpath.exists():
        raise FileNotFoundError(f"Missing cached data: {fpath}")
    df = pd.read_csv(fpath)
    time_col = next(c for c in df.columns if c.lower() in ["timestamp", "time", "date"])
    df[time_col] = pd.to_datetime(df[time_col], utc=True)
    return df.sort_values(time_col).set_index(time_col)

# -------------------------------------------------------------------------
# SIGNAL GENERATION
# -------------------------------------------------------------------------
def generate_option_a_signals(df_m5: pd.DataFrame, df_h1: pd.DataFrame, symbol: str) -> pd.DataFrame:
    pip_val = get_pip_value(symbol)
    h1 = df_h1.copy()
    h1["sma20"] = h1["close"].rolling(20).mean()
    h1["sma200"] = h1["close"].rolling(200).mean()
    h1["sma20_slope"] = h1["sma20"] - h1["sma20"].shift(1)
    
    h1["bias"] = 0
    h1.loc[(h1["close"] > h1["sma200"]) & (h1["sma20_slope"] > 0), "bias"] = 1
    h1.loc[(h1["close"] < h1["sma200"]) & (h1["sma20_slope"] < 0), "bias"] = -1
    
    # Point-in-time H1 shift
    h1["bias_pit"] = h1["bias"].shift(1)
    
    m5 = df_m5.copy()
    m5["h1_bias"] = h1["bias_pit"].reindex(m5.index, method="ffill").fillna(0)
    m5["ema20"] = m5["close"].ewm(span=20, adjust=False).mean()
    
    signals = []
    for i in range(25, len(m5) - 5):
        bias = m5["h1_bias"].iloc[i]
        if bias == 0:
            continue
            
        cur, p1, p2, p3 = m5.iloc[i], m5.iloc[i-1], m5.iloc[i-2], m5.iloc[i-3]
        
        if bias == 1:
            pullback = (p3["high"] > p2["high"] > p1["high"]) and (p1["low"] <= p1["ema20"] or p2["low"] <= p2["ema20"])
            trigger = cur["close"] > cur["open"] and cur["close"] > p1["high"] and cur["close"] > cur["ema20"]
            if pullback and trigger:
                swing_low = min(p1["low"], p2["low"], p3["low"], cur["low"])
                stop_pips = max(6.0, (cur["close"] - swing_low) / pip_val)
                signals.append({"timestamp": m5.index[i], "side": "BUY", "stop_pips": round(stop_pips, 1)})
                
        elif bias == -1:
            pullback = (p3["low"] < p2["low"] < p1["low"]) and (p1["high"] >= p1["ema20"] or p2["high"] >= p2["ema20"])
            trigger = cur["close"] < cur["open"] and cur["close"] < p1["low"] and cur["close"] < cur["ema20"]
            if pullback and trigger:
                swing_high = max(p1["high"], p2["high"], p3["high"], cur["high"])
                stop_pips = max(6.0, (swing_high - cur["close"]) / pip_val)
                signals.append({"timestamp": m5.index[i], "side": "SELL", "stop_pips": round(stop_pips, 1)})
                
    return pd.DataFrame(signals)

def generate_option_b_signals(df_m15: pd.DataFrame, symbol: str) -> pd.DataFrame:
    pip_val = get_pip_value(symbol)
    m15 = df_m15.copy()
    m15["sma20"] = m15["close"].rolling(20).mean()
    m15["sma200"] = m15["close"].rolling(200).mean()
    m15["sma20_slope"] = m15["sma20"] - m15["sma20"].shift(1)
    m15["ema20"] = m15["close"].ewm(span=20, adjust=False).mean()
    
    signals = []
    for i in range(200, len(m15) - 5):
        cur, p1, p2, p3 = m15.iloc[i], m15.iloc[i-1], m15.iloc[i-2], m15.iloc[i-3]
        
        if cur["close"] > cur["sma200"] and cur["sma20_slope"] > 0:
            pullback = (p3["high"] > p2["high"] > p1["high"]) and (p1["low"] <= p1["ema20"] or p2["low"] <= p2["ema20"])
            trigger = cur["close"] > cur["open"] and cur["close"] > p1["high"]
            if pullback and trigger:
                swing_low = min(p1["low"], p2["low"], p3["low"], cur["low"])
                stop_pips = max(8.0, (cur["close"] - swing_low) / pip_val)
                signals.append({"timestamp": m15.index[i], "side": "BUY", "stop_pips": round(stop_pips, 1)})
                
        elif cur["close"] < cur["sma200"] and cur["sma20_slope"] < 0:
            pullback = (p3["low"] < p2["low"] < p1["low"]) and (p1["high"] >= p1["ema20"] or p2["high"] >= p2["ema20"])
            trigger = cur["close"] < cur["open"] and cur["close"] < p1["low"]
            if pullback and trigger:
                swing_high = max(p1["high"], p2["high"], p3["high"], cur["high"])
                stop_pips = max(8.0, (swing_high - cur["close"]) / pip_val)
                signals.append({"timestamp": m15.index[i], "side": "SELL", "stop_pips": round(stop_pips, 1)})
                
    return pd.DataFrame(signals)

# -------------------------------------------------------------------------
# EVENT-STUDY EVALUATION ENGINE
# -------------------------------------------------------------------------
def evaluate_path(
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
            # 1. Open Gap
            if open_px <= stop_px: return open_px - half_spread - exit_slip, "STOP_HIT", count, t
            if open_px >= target_px: return open_px - half_spread - exit_slip, "TARGET_HIT", count, t
            # 2. Intraday (Adverse Stop Checked First)
            if low_px <= stop_px: return stop_px - half_spread - exit_slip, "STOP_HIT", count, t
            if high_px >= target_px: return target_px - half_spread - exit_slip, "TARGET_HIT", count, t
        else:
            # 1. Open Gap
            if open_px >= stop_px: return open_px + half_spread + exit_slip, "STOP_HIT", count, t
            if open_px <= target_px: return open_px + half_spread + exit_slip, "TARGET_HIT", count, t
            # 2. Intraday (Adverse Stop Checked First)
            if high_px >= stop_px: return stop_px + half_spread + exit_slip, "STOP_HIT", count, t
            if low_px <= target_px: return target_px + half_spread + exit_slip, "TARGET_HIT", count, t
            
    last_row = df.iloc[-1]
    px = float(last_row["close"]) - half_spread - exit_slip if side == "BUY" else float(last_row["close"]) + half_spread + exit_slip
    return px, "DATASET_END", len(df) - entry_idx, df.index[-1]

def run_event_study(
    symbol: str,
    df: pd.DataFrame,
    signals: pd.DataFrame,
    invert: bool,
    pit_usdjpy_series: Optional[pd.Series]
) -> Tuple[List[TradeRecord], dict]:
    pip_val = get_pip_value(symbol)
    spread_pips = SPREAD_PIPS_MAP.get(symbol, 1.5)
    half_spread = (spread_pips / 2.0) * pip_val
    entry_slip = ENTRY_SLIPPAGE_PIPS * pip_val
    exit_slip = EXIT_SLIPPAGE_PIPS * pip_val
    
    trades: List[TradeRecord] = []
    base, quote = symbol.split("_")

    # Pure event study: evaluate EVERY signal timestamp independently
    for _, sig in signals.iterrows():
        entry_idx = df.index.get_loc(sig["timestamp"]) if sig["timestamp"] in df.index else None
        if entry_idx is None or entry_idx >= len(df) - 1:
            continue
            
        raw_entry = float(df["close"].iloc[entry_idx])
        raw_side = sig["side"]
        side = ("SELL" if raw_side == "BUY" else "BUY") if invert else raw_side
        stop_dist_quote = float(sig["stop_pips"]) * pip_val
        
        if side == "BUY":
            entry_px = raw_entry + half_spread + entry_slip
            stop_px = entry_px - stop_dist_quote
            target_px = entry_px + (TARGET_R * stop_dist_quote)
        else:
            entry_px = raw_entry - half_spread - entry_slip
            stop_px = entry_px + stop_dist_quote
            target_px = entry_px - (TARGET_R * stop_dist_quote)
            
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
            
        exit_px, reason, bars_held, exit_time = evaluate_path(
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

    print(f" [POOLED STATS] Total Events: {len(all_trades)} | Win Rate: {len(wins_r)/len(all_trades)*100:.1f}% | Mean R: {np.mean(all_r):+.3f}R | PF (R): {pooled_pf:.2f} | Pos Pairs: {pos_pairs}/9")
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
    print("=" * 95)
    print(" 🚀 RUNNING 4-COHORT EVENT-STUDY INVERSION BENCHMARK")
    print("=" * 95)

    # Point-in-time USD_JPY shift(1)
    df_uj = load_local_dataset("USD_JPY", "H1")
    pit_usdjpy = df_uj["close"].shift(1)

    signals_m5, signals_m15 = {}, {}
    dfs_m5, dfs_m15 = {}, {}

    for sym in PAIRS:
        df_m5 = load_local_dataset(sym, "M5")
        df_h1 = load_local_dataset(sym, "H1")
        df_m15 = load_local_dataset(sym, "M15")
        
        dfs_m5[sym] = df_m5
        dfs_m15[sym] = df_m15
        
        signals_m5[sym] = generate_option_a_signals(df_m5, df_h1, sym)
        signals_m15[sym] = generate_option_b_signals(df_m15, sym)

    cohort_summaries = []

    # 1. Cohort A1: H1->M5 Continuation (Original)
    res_a1, trades_a1 = {}, []
    for sym in PAIRS:
        if not signals_m5[sym].empty:
            t, m = run_event_study(sym, dfs_m5[sym], signals_m5[sym], invert=False, pit_usdjpy_series=pit_usdjpy)
            if m: res_a1[sym] = m; trades_a1.extend(t)
    cohort_summaries.append(print_cohort_report("A1", "H1->M5 Continuation (Original)", res_a1, trades_a1))

    # 2. Cohort A2: H1->M5 Inverted (Exhaustion Fade)
    res_a2, trades_a2 = {}, []
    for sym in PAIRS:
        if not signals_m5[sym].empty:
            t, m = run_event_study(sym, dfs_m5[sym], signals_m5[sym], invert=True, pit_usdjpy_series=pit_usdjpy)
            if m: res_a2[sym] = m; trades_a2.extend(t)
    cohort_summaries.append(print_cohort_report("A2", "H1->M5 Inverted (Exhaustion Fade)", res_a2, trades_a2))

    # 3. Cohort B1: M15 Continuation (Original)
    res_b1, trades_b1 = {}, []
    for sym in PAIRS:
        if not signals_m15[sym].empty:
            t, m = run_event_study(sym, dfs_m15[sym], signals_m15[sym], invert=False, pit_usdjpy_series=pit_usdjpy)
            if m: res_b1[sym] = m; trades_b1.extend(t)
    cohort_summaries.append(print_cohort_report("B1", "Native M15 Continuation (Original)", res_b1, trades_b1))

    # 4. Cohort B2: M15 Inverted (Exhaustion Fade)
    res_b2, trades_b2 = {}, []
    for sym in PAIRS:
        if not signals_m15[sym].empty:
            t, m = run_event_study(sym, dfs_m15[sym], signals_m15[sym], invert=True, pit_usdjpy_series=pit_usdjpy)
            if m: res_b2[sym] = m; trades_b2.extend(t)
    cohort_summaries.append(print_cohort_report("B2", "Native M15 Inverted (Exhaustion Fade)", res_b2, trades_b2))

    # Final Diagnostic Matrix
    print("\n" + "=" * 95)
    print(" 🏁 4-COHORT EVENT-STUDY DIAGNOSTIC MATRIX")
    print("=" * 95)
    print(f"{'Cohort':<8} | {'Setup Name':<34} | {'Events':<6} | {'Win %':<6} | {'Mean R':<7} | {'PF (R)':<6} | {'Pos Pairs':<9}")
    print("-" * 95)
    for c in cohort_summaries:
        print(f"{c['cohort']:<8} | {c['title']:<34} | {c['trades']:<6d} | {c['win_rate']:>5.1f}% | {c['mean_r']:>+6.3f}R | {c['pf_r']:>6.2f} | {c['pos_pairs']:>2d}/9")
    print("=" * 95)

if __name__ == "__main__":
    main()
