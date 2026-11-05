#!/usr/bin/env python3
"""
H4 Mean Reversion Signal Quality & Filter Sweep (F0 - F3)
Baseline Engine: 100 SMA | +/-2.5-Sigma Reclaim | +/-3.5-Sigma Stop | T1 Centerline Target

Cohorts:
- F0: Baseline T1 (No Filter)
- F1: 200-SMA Macro Slope Filter (5-Bar Delta)
- F2: RSI(14) Extreme Confluence (RSI <= 30 Long / >= 70 Short)
- F3: Combined 200-SMA Slope + RSI(14) Confluence

Risk: 0.50% equity ($500 on $100k) | Native H4 Candles | PIT Conversions
"""

import os
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

# Load credentials from .env if present
env_path = Path("/home/moonrider1968/bots/.env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

DATA_DIR = Path("/home/moonrider1968/bots/oanda_data")
PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]

INITIAL_EQUITY = 100_000.0
RISK_PCT = 0.0050
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
    target_pips: float
    initial_target_r: float
    units: float
    dollar_risk: float
    net_pnl_usd: float
    r_multiple: float
    mae_r: float
    mfe_r: float
    mfe_capture: float
    exit_reason: str
    bars_held: int

def get_pip_value(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001

def load_local_dataset(symbol: str, granularity: str = "H4") -> pd.DataFrame:
    sub = granularity.lower()
    search_paths = [
        DATA_DIR / sub / f"{symbol}_{granularity}.csv",
        DATA_DIR / f"{symbol}_{granularity}.csv",
        Path(f"/home/moonrider1968/bots/{symbol}_{granularity}.csv")
    ]
    for fpath in search_paths:
        if fpath.exists():
            df = pd.read_csv(fpath)
            time_col = next(c for c in df.columns if c.lower() in ["timestamp", "time", "date"])
            df[time_col] = pd.to_datetime(df[time_col], utc=True)
            return df.sort_values(time_col).set_index(time_col)
    raise FileNotFoundError(f"Missing cached data for {symbol} ({granularity})")

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)

def generate_filtered_signals(df_h4: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = df_h4.copy()
    pip_val = get_pip_value(symbol)
    
    # Core 100 SMA & Bands
    df["ma100"] = df["close"].rolling(100).mean()
    df["std100"] = df["close"].rolling(100).std()
    df["upper_25s"] = df["ma100"] + (2.5 * df["std100"])
    df["lower_25s"] = df["ma100"] - (2.5 * df["std100"])
    df["upper_35s"] = df["ma100"] + (3.5 * df["std100"])
    df["lower_35s"] = df["ma100"] - (3.5 * df["std100"])
    
    # Filter Indicators
    df["ma200"] = df["close"].rolling(200).mean()
    df["ma200_slope"] = df["ma200"].diff(5)
    df["rsi14"] = compute_rsi(df["close"], 14)
    
    signals = []
    for i in range(105, len(df) - 5):
        cur = df.iloc[i]
        prev = df.iloc[i - 1]
        t = df.index[i]
        
        # Long Reclaim
        if prev["close"] < prev["lower_25s"] and cur["close"] > cur["lower_25s"] and cur["close"] < cur["ma100"]:
            stop_dist_quote = cur["close"] - cur["lower_35s"]
            stop_pips = max(6.0, stop_dist_quote / pip_val)
            
            slope_pass = bool(cur["ma200_slope"] >= 0.0) if pd.notna(cur["ma200_slope"]) else False
            rsi_pass = (prev["rsi14"] <= 30.0) or (cur["rsi14"] <= 32.0)
            
            signals.append({
                "timestamp": t,
                "side": "BUY",
                "stop_pips": round(stop_pips, 1),
                "target_px": cur["ma100"],
                "pass_f0": True,
                "pass_f1": bool(slope_pass),
                "pass_f2": bool(rsi_pass),
                "pass_f3": bool(slope_pass and rsi_pass)
            })
            
        # Short Reclaim
        elif prev["close"] > prev["upper_25s"] and cur["close"] < cur["upper_25s"] and cur["close"] > cur["ma100"]:
            stop_dist_quote = cur["upper_35s"] - cur["close"]
            stop_pips = max(6.0, stop_dist_quote / pip_val)
            
            slope_pass = bool(cur["ma200_slope"] <= 0.0) if pd.notna(cur["ma200_slope"]) else False
            rsi_pass = (prev["rsi14"] >= 70.0) or (cur["rsi14"] >= 68.0)
            
            signals.append({
                "timestamp": t,
                "side": "SELL",
                "stop_pips": round(stop_pips, 1),
                "target_px": cur["ma100"],
                "pass_f0": True,
                "pass_f1": bool(slope_pass),
                "pass_f2": bool(rsi_pass),
                "pass_f3": bool(slope_pass and rsi_pass)
            })
            
    return pd.DataFrame(signals)

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
) -> Tuple[float, str, int, pd.Timestamp, float, float]:
    adverse_max = 0.0
    favorable_max = 0.0
    stop_dist = abs(entry_px - stop_px)

    for count, idx in enumerate(range(entry_idx + 1, len(df)), start=1):
        row = df.iloc[idx]
        open_px, high_px, low_px = float(row["open"]), float(row["high"]), float(row["low"])
        t = df.index[idx]
        
        if side == "BUY":
            adv = max(0.0, entry_px - low_px)
            fav = max(0.0, high_px - entry_px)
            adverse_max = max(adverse_max, adv)
            favorable_max = max(favorable_max, fav)

            if open_px <= stop_px: return open_px - half_spread - exit_slip, "STOP_HIT", count, t, adverse_max/stop_dist, favorable_max/stop_dist
            if open_px >= target_px: return open_px - half_spread - exit_slip, "TARGET_HIT", count, t, adverse_max/stop_dist, favorable_max/stop_dist
            if low_px <= stop_px: return stop_px - half_spread - exit_slip, "STOP_HIT", count, t, adverse_max/stop_dist, favorable_max/stop_dist
            if high_px >= target_px: return target_px - half_spread - exit_slip, "TARGET_HIT", count, t, adverse_max/stop_dist, favorable_max/stop_dist

        else:
            adv = max(0.0, high_px - entry_px)
            fav = max(0.0, entry_px - low_px)
            adverse_max = max(adverse_max, adv)
            favorable_max = max(favorable_max, fav)

            if open_px >= stop_px: return open_px + half_spread + exit_slip, "STOP_HIT", count, t, adverse_max/stop_dist, favorable_max/stop_dist
            if open_px <= target_px: return open_px + half_spread + exit_slip, "TARGET_HIT", count, t, adverse_max/stop_dist, favorable_max/stop_dist
            if high_px >= stop_px: return stop_px + half_spread + exit_slip, "STOP_HIT", count, t, adverse_max/stop_dist, favorable_max/stop_dist
            if low_px <= target_px: return target_px + half_spread + exit_slip, "TARGET_HIT", count, t, adverse_max/stop_dist, favorable_max/stop_dist

    last_row = df.iloc[-1]
    px = float(last_row["close"]) - half_spread - exit_slip if side == "BUY" else float(last_row["close"]) + half_spread + exit_slip
    return px, "DATASET_END", len(df) - entry_idx, df.index[-1], adverse_max/stop_dist, favorable_max/stop_dist

def run_simulation(
    symbol: str,
    df: pd.DataFrame,
    signals: pd.DataFrame,
    filter_col: str,
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
        if not sig[filter_col]:
            continue
            
        entry_idx = df.index.get_loc(sig["timestamp"]) if sig["timestamp"] in df.index else None
        if entry_idx is None or entry_idx >= len(df) - 1:
            continue
            
        raw_entry = float(df["close"].iloc[entry_idx])
        side = sig["side"]
        stop_dist_quote = float(sig["stop_pips"]) * pip_val
        target_px = float(sig["target_px"])
        
        if side == "BUY":
            entry_px = raw_entry + half_spread + entry_slip
            stop_px = entry_px - stop_dist_quote
            if target_px <= entry_px: continue
            target_pips = (target_px - entry_px) / pip_val
        else:
            entry_px = raw_entry - half_spread - entry_slip
            stop_px = entry_px + stop_dist_quote
            if target_px >= entry_px: continue
            target_pips = (entry_px - target_px) / pip_val

        initial_target_r = target_pips / float(sig["stop_pips"]) if float(sig["stop_pips"]) > 0 else 0.0

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
            
        exit_px, reason, bars_held, exit_time, mae_r, mfe_r = evaluate_path(
            df, entry_idx, side, entry_px, stop_px, target_px, pip_val, half_spread, exit_slip
        )
        
        quote_pnl = (exit_px - entry_px) * units if side == "BUY" else (entry_px - exit_px) * units
        net_pnl_usd = quote_pnl * quote_to_usd
        r_mult = net_pnl_usd / target_dollar_risk

        mfe_capture = (r_mult / mfe_r) if (r_mult > 0 and mfe_r > 0) else 0.0

        trades.append(TradeRecord(
            symbol=symbol, side=side, entry_time=sig["timestamp"], exit_time=exit_time,
            entry_price=entry_px, exit_price=exit_px, stop_price=stop_px, target_price=target_px,
            risk_pips=float(sig["stop_pips"]), target_pips=target_pips, initial_target_r=initial_target_r,
            units=units, dollar_risk=target_dollar_risk, net_pnl_usd=net_pnl_usd, r_multiple=r_mult,
            mae_r=mae_r, mfe_r=mfe_r, mfe_capture=mfe_capture, exit_reason=reason, bars_held=bars_held
        ))
        
    if not trades:
        return [], {}
        
    net_rs = [t.r_multiple for t in trades]
    wins = [r for r in net_rs if r > 0]
    losses = [abs(r) for r in net_rs if r < 0]
    target_hits = [t for t in trades if t.exit_reason == "TARGET_HIT"]
    stop_hits = [t for t in trades if t.exit_reason == "STOP_HIT"]
    pos_trades = [t for t in trades if t.r_multiple > 0]
    
    net_pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (99.0 if wins else 0.0)
    
    return trades, {
        "trades": len(trades),
        "target_hit_pct": len(target_hits) / len(trades) * 100.0,
        "stop_hit_pct": len(stop_hits) / len(trades) * 100.0,
        "win_rate": len(wins) / len(trades) * 100.0,
        "net_mean_r": float(np.mean(net_rs)),
        "net_pf": net_pf,
        "median_r": float(np.median(net_rs)),
        "avg_target_r": float(np.mean([t.initial_target_r for t in trades])),
        "avg_mfe_capture": float(np.mean([t.mfe_capture for t in pos_trades])) if pos_trades else 0.0,
        "avg_bars_held": float(np.mean([t.bars_held for t in trades]))
    }

def print_cohort_report(cohort_id: str, title: str, results: Dict[str, dict], all_trades: List[TradeRecord]):
    print("\n" + "=" * 145)
    print(f" 🎯 COHORT {cohort_id}: {title}")
    print("=" * 145)
    print(f"{'Symbol':<9} | {'Events':<6} | {'Target%':<7} | {'Stop%':<6} | {'Win%':<6} | {'Net R':<8} | {'NetPF':<6} | {'Med R':<7} | {'Avg TgtR':<8} | {'MFECapt':<7} | {'HoldBar':<7}")
    print("-" * 145)
    for sym, m in results.items():
        print(f"{sym:<9} | {m['trades']:<6d} | {m['target_hit_pct']:>6.1f}% | {m['stop_hit_pct']:>5.1f}% | {m['win_rate']:>5.1f}% | {m['net_mean_r']:>+7.3f}R | {m['net_pf']:>6.2f} | {m['median_r']:>+6.3f}R | {m['avg_target_r']:>7.2f}R | {m['avg_mfe_capture']*100:>6.1f}% | {m['avg_bars_held']:>6.1f}b")
    print("=" * 145)

    all_net_r = [t.r_multiple for t in all_trades]
    wins = [r for r in all_net_r if r > 0]
    losses = [abs(r) for r in all_net_r if r < 0]
    pooled_net_pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else 0.0
    targets = [t for t in all_trades if t.exit_reason == "TARGET_HIT"]
    stops = [t for t in all_trades if t.exit_reason == "STOP_HIT"]
    pos_pairs = sum(1 for m in results.values() if m["net_mean_r"] > 0)
    pos_trades = [t for t in all_trades if t.r_multiple > 0]
    pooled_mfe_capt = float(np.mean([t.mfe_capture for t in pos_trades])) * 100.0 if pos_trades else 0.0

    print(f" [POOLED STATS] Events: {len(all_trades)} | Target%: {len(targets)/len(all_trades)*100:.1f}% | Stop%: {len(stops)/len(all_trades)*100:.1f}% | Win%: {len(wins)/len(all_trades)*100:.1f}%")
    print(f"                Net Mean R: {np.mean(all_net_r):+.3f}R | Net PF: {pooled_net_pf:.2f} | Median Net R: {np.median(all_net_r):+.3f}R | Pos Pairs: {pos_pairs}/9")
    print(f"                Avg Initial Target R: {np.mean([t.initial_target_r for t in all_trades]):.2f}R | Realized MFE Capture: {pooled_mfe_capt:.1f}% | Avg Bars: {np.mean([t.bars_held for t in all_trades]):.1f}")
    print("=" * 145)

    return {
        "cohort": cohort_id,
        "title": title,
        "events": len(all_trades),
        "target_pct": len(targets)/len(all_trades)*100.0,
        "win_rate": len(wins)/len(all_trades)*100.0,
        "net_mean_r": float(np.mean(all_net_r)),
        "net_pf": pooled_net_pf,
        "mfe_capture": pooled_mfe_capt,
        "avg_bars": float(np.mean([t.bars_held for t in all_trades])),
        "pos_pairs": pos_pairs
    }

def main():
    print("=" * 115)
    print(" 🚀 EXECUTING H4 SIGNAL QUALITY & FILTER CONFLUENCE SWEEP (F0 vs F1 vs F2 vs F3)")
    print("=" * 115)

    df_uj_h1 = load_local_dataset("USD_JPY", "H1")
    pit_usdjpy = df_uj_h1["close"].shift(1)

    dfs_h4 = {sym: load_local_dataset(sym, "H4") for sym in PAIRS}
    signals_h4 = {sym: generate_filtered_signals(dfs_h4[sym], sym) for sym in PAIRS}

    cohort_configs = [
        ("F0", "pass_f0", "Baseline T1 Centerline (No Filter)"),
        ("F1", "pass_f1", "200-SMA Macro Slope Filter (5-Bar Delta)"),
        ("F2", "pass_f2", "RSI(14) Extreme Filter (<=30 Long / >=70 Short)"),
        ("F3", "pass_f3", "Combined 200-SMA Slope + RSI(14) Confluence")
    ]

    summaries = []

    for cid, filter_col, desc in cohort_configs:
        res, trades = {}, []
        for sym in PAIRS:
            if not signals_h4[sym].empty:
                t, m = run_simulation(sym, dfs_h4[sym], signals_h4[sym], filter_col, pit_usdjpy)
                if m:
                    res[sym] = m
                    trades.extend(t)
        if res:
            summaries.append(print_cohort_report(cid, desc, res, trades))

    print("\n" + "=" * 125)
    print(" 🏁 H4 SIGNAL QUALITY & FILTER CONFLUENCE COMPARISON MATRIX SUMMARY")
    print("=" * 125)
    print(f"{'Cohort':<6} | {'Filter Description':<40} | {'Events':<6} | {'Target%':<7} | {'Win%':<6} | {'Net R':<8} | {'Net PF':<6} | {'MFECapt':<7} | {'HoldBar':<7} | {'Pos Pairs':<9}")
    print("-" * 125)
    for s in summaries:
        print(f"{s['cohort']:<6} | {s['title']:<40} | {s['events']:<6d} | {s['target_pct']:>6.1f}% | {s['win_rate']:>5.1f}% | {s['net_mean_r']:>+7.3f}R | {s['net_pf']:>6.2f} | {s['mfe_capture']:>6.1f}% | {s['avg_bars']:>6.1f}b | {s['pos_pairs']:>2d}/9")
    print("=" * 125)

if __name__ == "__main__":
    main()
