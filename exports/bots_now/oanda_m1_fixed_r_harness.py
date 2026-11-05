"""
M1 Fixed-R Diagnostic Harness (EVENT_STUDY Mode)
Architecture: MicroBeast EMA Reclaim / Momentum
Exit Geometry: Fixed +1.5R Target vs -1.0R Stop (No Timeout)
Classification: FORMALLY REJECTED / ARCHIVED
"""
import hashlib
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

INITIAL_EQUITY = 100_000.0
RISK_PCT = 0.0050          # 0.50% equity risk per trade ($500 fixed unit base)
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

def get_pip_value(symbol: str, price: Optional[float] = None) -> float:
    return 0.01 if "JPY" in symbol else 0.0001

def calculate_max_drawdown(equity_curve: np.ndarray) -> float:
    if len(equity_curve) == 0:
        return 0.0
    cummax = np.maximum.accumulate(equity_curve)
    drawdowns = (cummax - equity_curve) / cummax
    return float(np.max(drawdowns) * 100.0) if len(drawdowns) > 0 else 0.0

def evaluate_m1_path_no_timeout(
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

def run_fixed_r_harness(
    symbol: str,
    df: pd.DataFrame,
    signals: pd.DataFrame
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
        else:
            quote_to_usd = 1.0 / 150.0 if "JPY" in quote else 1.0

        usd_risk_per_unit = stop_dist_quote * quote_to_usd
        target_dollar_risk = INITIAL_EQUITY * RISK_PCT
        units = target_dollar_risk / usd_risk_per_unit if usd_risk_per_unit > 0 else 0
        if units <= 0:
            continue
            
        exit_px, reason, bars_held, exit_time = evaluate_m1_path_no_timeout(
            df, entry_idx, side, entry_px, stop_px, target_px, pip_val, half_spread, exit_slip
        )
        
        quote_pnl = (exit_px - entry_px) * units if side == "BUY" else (entry_px - exit_px) * units
        net_pnl_usd = quote_pnl * quote_to_usd
        r_mult = net_pnl_usd / target_dollar_risk

        trades.append(TradeRecord(
            symbol=symbol, side=side, entry_time=sig["timestamp"], exit_time=exit_time,
            entry_price=entry_px, exit_price=exit_px, stop_price=stop_px, target_price=target_price,
            risk_pips=float(sig["stop_pips"]), units=units, dollar_risk=target_dollar_risk,
            net_pnl_usd=net_pnl_usd, r_multiple=r_mult, exit_reason=reason, bars_held=bars_held
        ))
        
    if not trades:
        return [], {}
        
    trades.sort(key=lambda x: x.exit_time)
    net_rs = [t.r_multiple for t in trades]
    wins = [r for r in net_rs if r > 0]
    losses = [abs(r) for r in net_rs if r < 0]
    target_hits = [t for t in trades if t.exit_reason == "TARGET_HIT"]
    stop_hits = [t for t in trades if t.exit_reason == "STOP_HIT"]
    
    # Chronological equity curve simulation for accurate Max Drawdown
    pnl_series = np.array([t.net_pnl_usd for t in trades])
    cum_equity = INITIAL_EQUITY + np.cumsum(pnl_series)
    max_dd = calculate_max_drawdown(cum_equity)
    
    net_pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (99.0 if wins else 0.0)
    
    return trades, {
        "trades": len(trades),
        "target_hit_pct": len(target_hits) / len(trades) * 100.0,
        "stop_hit_pct": len(stop_hits) / len(trades) * 100.0,
        "timeout_pct": 0.0,
        "avg_timeout_r": 0.0,
        "win_rate": len(wins) / len(trades) * 100.0,
        "total_pnl_usd": float(np.sum(pnl_series)),
        "pf": net_pf,
        "max_dd_pct": max_dd,
        "mean_r": float(np.mean(net_rs)),
        "median_r": float(np.median(net_rs)),
        "avg_bars_held": float(np.mean([t.bars_held for t in trades]))
    }

def print_performance_table(results: Dict[str, dict]):
    print("=" * 135)
    print(" 📊 M1 EVENT-STUDY BENCHMARK (OVERLAPPING PERMITTED | +1.5R TARGET vs -1.0R STOP)")
    print("=" * 135)
    print(f"{'Symbol':<10} | {'Events':<6} | {'Target %':<8} | {'Stop %':<7} | {'Win %':<6} | {'Mean R':<7} | {'PF':<5} | {'Max DD %':<8} | {'Avg Bars':<8}")
    print("-" * 135)
    for sym, m in results.items():
        print(f"{sym:<10} | {m['trades']:<6d} | {m['target_hit_pct']:>7.1f}% | {m['stop_hit_pct']:>6.1f}% | {m['win_rate']:>5.1f}% | {m['mean_r']:>+6.3f}R | {m['pf']:>5.2f} | {m['max_dd_pct']:>7.2f}% | {m['avg_bars_held']:>7.1f}b")
    print("=" * 135)
