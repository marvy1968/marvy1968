#!/usr/bin/env python3
"""
H4 Extreme-Tail Mean Reversion: 60/40 In-Sample (IS) vs Out-of-Sample (OOS) Engine
Frozen Architecture: 100 SMA | +/-2.5s Reclaim | +/-3.5s Stop | T1 Centerline Target
Evaluates temporal stability across In-Sample (60%) and Out-of-Sample (40%) partitions.
"""

import os
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

DATA_DIR = Path("/home/moonrider1968/bots/oanda_data")
PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]

INITIAL_EQUITY = 100_000.0
RISK_PCT = 0.0050  # 0.50% per trade
ENTRY_SLIPPAGE_PIPS = 0.25
EXIT_SLIPPAGE_PIPS = 0.25

SPREAD_PIPS_MAP = {
    "EUR_USD": 1.0, "GBP_USD": 1.4, "USD_JPY": 1.1, "AUD_USD": 1.2,
    "USD_CAD": 1.5, "NZD_USD": 1.6, "USD_CHF": 1.5, "EUR_JPY": 1.6, "GBP_JPY": 2.2
}

@dataclass
class TradeInstance:
    trade_id: int
    symbol: str
    side: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_px: float
    stop_px: float
    target_px: float
    units: float
    dollar_risk: float
    quote_to_usd: float
    pip_val: float
    half_spread: float
    exit_slip: float
    path_bars: List[Tuple[pd.Timestamp, float, float, float]]
    exit_reason: str = "OPEN"
    final_exit_px: float = 0.0
    net_pnl_usd: float = 0.0
    r_multiple: float = 0.0

def get_pip_value(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001

def load_dataset(symbol: str, granularity: str = "H4") -> pd.DataFrame:
    sub = granularity.lower()
    for p in [DATA_DIR / sub / f"{symbol}_{granularity}.csv", DATA_DIR / f"{symbol}_{granularity}.csv", Path(f"/home/moonrider1968/bots/{symbol}_{granularity}.csv")]:
        if p.exists():
            df = pd.read_csv(p)
            time_col = next(c for c in df.columns if c.lower() in ["timestamp", "time", "date"])
            df[time_col] = pd.to_datetime(df[time_col], utc=True)
            return df.sort_values(time_col).set_index(time_col)
    raise FileNotFoundError(f"Missing data for {symbol} ({granularity})")

def generate_signals(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = df.copy()
    pip_val = get_pip_value(symbol)
    df["ma100"] = df["close"].rolling(100).mean()
    df["std100"] = df["close"].rolling(100).std()
    df["upper_25s"] = df["ma100"] + (2.5 * df["std100"])
    df["lower_25s"] = df["ma100"] - (2.5 * df["std100"])
    df["upper_35s"] = df["ma100"] + (3.5 * df["std100"])
    df["lower_35s"] = df["ma100"] - (3.5 * df["std100"])
    
    signals = []
    for i in range(105, len(df) - 5):
        cur = df.iloc[i]
        prev = df.iloc[i - 1]
        t = df.index[i]
        
        if prev["close"] < prev["lower_25s"] and cur["close"] > cur["lower_25s"] and cur["close"] < cur["ma100"]:
            stop_dist = cur["close"] - cur["lower_35s"]
            signals.append({
                "timestamp": t, "symbol": symbol, "side": "BUY",
                "stop_pips": max(6.0, stop_dist / pip_val), "target_px": float(cur["ma100"])
            })
        elif prev["close"] > prev["upper_25s"] and cur["close"] < cur["upper_25s"] and cur["close"] > cur["ma100"]:
            stop_dist = cur["upper_35s"] - cur["close"]
            signals.append({
                "timestamp": t, "symbol": symbol, "side": "SELL",
                "stop_pips": max(6.0, stop_dist / pip_val), "target_px": float(cur["ma100"])
            })
    return pd.DataFrame(signals)

def precompute_trade(
    df: pd.DataFrame,
    sig: dict,
    pit_usdjpy: Optional[pd.Series],
    trade_id: int,
    current_nav: float,
    partition_end: Optional[pd.Timestamp] = None
) -> TradeInstance:
    sym = sig["symbol"]
    side = sig["side"]
    pip_val = get_pip_value(sym)
    spread = SPREAD_PIPS_MAP.get(sym, 1.5)
    half_spread = (spread / 2.0) * pip_val
    entry_slip = ENTRY_SLIPPAGE_PIPS * pip_val
    exit_slip = EXIT_SLIPPAGE_PIPS * pip_val
    stop_dist = sig["stop_pips"] * pip_val
    
    entry_idx = df.index.get_loc(sig["timestamp"])
    raw_entry = float(df["close"].iloc[entry_idx])
    
    if side == "BUY":
        entry_px = raw_entry + half_spread + entry_slip
        stop_px = entry_px - stop_dist
        target_px = sig["target_px"]
    else:
        entry_px = raw_entry - half_spread - entry_slip
        stop_px = entry_px + stop_dist
        target_px = sig["target_px"]
        
    base, quote = sym.split("_")
    t_entry = sig["timestamp"]
    
    if quote == "USD": quote_to_usd = 1.0
    elif base == "USD": quote_to_usd = 1.0 / entry_px if entry_px > 0 else 1.0
    elif quote == "JPY" and pit_usdjpy is not None:
        uj = pit_usdjpy.asof(t_entry)
        quote_to_usd = 1.0 / uj if pd.notna(uj) and uj > 0 else 1.0 / 150.0
    else: quote_to_usd = 1.0
    
    risk_per_unit = stop_dist * quote_to_usd
    dollar_risk = current_nav * RISK_PCT
    units = dollar_risk / risk_per_unit if risk_per_unit > 0 else 0
    
    path_bars = []
    exit_px, reason, exit_time = entry_px, "DATASET_END", df.index[-1]
    
    for idx in range(entry_idx + 1, len(df)):
        row = df.iloc[idx]
        o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
        t = df.index[idx]
        path_bars.append((t, o, h, l))
        
        if side == "BUY":
            if o <= stop_px: exit_px, reason, exit_time = o - half_spread - exit_slip, "STOP_HIT", t; break
            if o >= target_px: exit_px, reason, exit_time = o - half_spread - exit_slip, "TARGET_HIT", t; break
            if l <= stop_px: exit_px, reason, exit_time = stop_px - half_spread - exit_slip, "STOP_HIT", t; break
            if h >= target_px: exit_px, reason, exit_time = target_px - half_spread - exit_slip, "TARGET_HIT", t; break
        else:
            if o >= stop_px: exit_px, reason, exit_time = o + half_spread + exit_slip, "STOP_HIT", t; break
            if o <= target_px: exit_px, reason, exit_time = o + half_spread + exit_slip, "TARGET_HIT", t; break
            if h >= stop_px: exit_px, reason, exit_time = stop_px + half_spread + exit_slip, "STOP_HIT", t; break
            if l <= target_px: exit_px, reason, exit_time = target_px + half_spread + exit_slip, "TARGET_HIT", t; break
            
    # If neither stop nor target occurred before the partition boundary,
    # liquidate at the last observable close INSIDE the partition.
    if reason == "DATASET_END" and partition_end is not None:
        valid = df.loc[
            (df.index > t_entry) &
            (df.index < partition_end)
        ]
        if not valid.empty:
            exit_time = valid.index[-1]
            raw_exit = float(valid["close"].iloc[-1])
            if side == "BUY":
                exit_px = raw_exit - half_spread - exit_slip
            else:
                exit_px = raw_exit + half_spread + exit_slip
            reason = "PARTITION_END"

    pnl = (exit_px - entry_px) * units if side == "BUY" else (entry_px - exit_px) * units
    net_pnl_usd = pnl * quote_to_usd
    r_mult = net_pnl_usd / dollar_risk if dollar_risk > 0 else 0.0
    
    return TradeInstance(
        trade_id=trade_id, symbol=sym, side=side, entry_time=t_entry, exit_time=exit_time,
        entry_px=entry_px, stop_px=stop_px, target_px=target_px, units=units, dollar_risk=dollar_risk,
        quote_to_usd=quote_to_usd, pip_val=pip_val, half_spread=half_spread, exit_slip=exit_slip,
        path_bars=path_bars, exit_reason=reason, final_exit_px=exit_px, net_pnl_usd=net_pnl_usd, r_multiple=r_mult
    )

def run_partition(all_signals: pd.DataFrame, dfs: Dict[str, pd.DataFrame], pit_usdjpy: pd.Series, start_ts: pd.Timestamp, end_ts: pd.Timestamp, max_gross_risk_pct: Optional[float] = None) -> dict:
    part_sigs = all_signals[(all_signals["timestamp"] >= start_ts) & (all_signals["timestamp"] < end_ts)]
    sorted_sigs = part_sigs.sort_values(by=["timestamp", "symbol"]).to_dict("records")
    
    all_timestamps = sorted(list(set().union(*(df.loc[(df.index >= start_ts) & (df.index < end_ts)].index for df in dfs.values()))))
    
    realized_nav = INITIAL_EQUITY
    active_trades: List[TradeInstance] = []
    closed_trades: List[TradeInstance] = []
    
    equity_curve = []
    peak_concurrent = 0
    
    sig_idx = 0
    num_sigs = len(sorted_sigs)
    
    for t in all_timestamps:
        still_active = []
        for trade in active_trades:
            if trade.exit_time <= t:
                realized_nav += trade.net_pnl_usd
                closed_trades.append(trade)
            else:
                still_active.append(trade)
        active_trades = still_active
        
        incoming_sigs = []
        while sig_idx < num_sigs and sorted_sigs[sig_idx]["timestamp"] == t:
            incoming_sigs.append(sorted_sigs[sig_idx])
            sig_idx += 1
            
        for sig in incoming_sigs:
            sym = sig["symbol"]
            if any(p.symbol == sym for p in active_trades):
                continue
                
            current_aggregate_risk = sum(p.dollar_risk for p in active_trades)
            proposed_risk = realized_nav * RISK_PCT
            if max_gross_risk_pct is not None and (current_aggregate_risk + proposed_risk) > (realized_nav * max_gross_risk_pct + 1e-6):
                continue
                
            trade_id = len(closed_trades) + len(active_trades) + 1
            trade_obj = precompute_trade(
                dfs[sym],
                sig,
                pit_usdjpy,
                trade_id,
                realized_nav,
                partition_end=end_ts
            )
            active_trades.append(trade_obj)
            
        unrealized_pnl = 0.0
        for trade in active_trades:
            curr_px = trade.entry_px
            for bar_t, o, h, l in trade.path_bars:
                if bar_t == t:
                    curr_px = (h + l) / 2.0
                    break
                elif bar_t > t:
                    break
            pnl = (curr_px - trade.entry_px) * trade.units if trade.side == "BUY" else (trade.entry_px - curr_px) * trade.units
            unrealized_pnl += pnl * trade.quote_to_usd
            
        equity_curve.append((t, realized_nav + unrealized_pnl))
        peak_concurrent = max(peak_concurrent, len(active_trades))

    # Settle every trade whose synthetic liquidation occurs at the
    # partition boundary. This prevents open P&L from disappearing.
    for trade in active_trades:
        realized_nav += trade.net_pnl_usd
        closed_trades.append(trade)
    active_trades = []

    # Append terminal partition equity so DD includes the final liquidation.
    if all_timestamps:
        equity_curve.append((all_timestamps[-1], realized_nav))

    eq_df = pd.DataFrame(equity_curve, columns=["time", "equity"])
    if not eq_df.empty:
        eq_df["cummax"] = eq_df["equity"].cummax()
        eq_df["dd"] = (eq_df["cummax"] - eq_df["equity"]) / eq_df["cummax"]
        max_dd_pct = float(eq_df["dd"].max() * 100.0)
    else:
        max_dd_pct = 0.0
        
    net_rs = [t.r_multiple for t in closed_trades]
    wins = [r for r in net_rs if r > 0]
    losses = [abs(r) for r in net_rs if r < 0]
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else 0.0
    
    pair_rs = defaultdict(list)
    for t in closed_trades:
        pair_rs[t.symbol].append(t.r_multiple)
    pos_pairs = sum(1 for sym, rs in pair_rs.items() if np.mean(rs) > 0)
    
    return {
        "trades": len(closed_trades),
        "win_rate": len(wins) / len(closed_trades) * 100.0 if closed_trades else 0.0,
        "mean_r": float(np.mean(net_rs)) if net_rs else 0.0,
        "net_pf": pf,
        "return_pct": ((realized_nav - INITIAL_EQUITY) / INITIAL_EQUITY) * 100.0,
        "max_dd_pct": max_dd_pct,
        "pos_pairs": pos_pairs,
        "evaluated_pairs": len(pair_rs)
    }

def main():
    print("=" * 135)
    print(" 🚀 H4 EXTREME-TAIL WALK-FORWARD OOS VALIDATION ENGINE (60% IS vs 40% OOS)")
    print("=" * 135)
    
    df_uj_h1 = load_dataset("USD_JPY", "H1")
    pit_usdjpy = df_uj_h1["close"].shift(1)
    
    dfs = {sym: load_dataset(sym, "H4") for sym in PAIRS}
    all_sigs = pd.concat([generate_signals(dfs[sym], sym) for sym in PAIRS], ignore_index=True)
    
    global_timeline = sorted(list(set().union(*(df.index for df in dfs.values()))))
    total_bars = len(global_timeline)
    split_idx = int(total_bars * 0.60)
    
    start_time = global_timeline[0]
    split_time = global_timeline[split_idx]
    end_time = global_timeline[-1]
    
    print(f" [DATASET PARTITION OVERVIEW]")
    print(f" • Full Timeline:       {start_time.date()} to {end_time.date()} ({total_bars} total H4 bars)")
    print(f" • In-Sample (60%):     {start_time.date()} to {split_time.date()} ({split_idx} bars)")
    print(f" • Out-of-Sample (40%):   {split_time.date()} to {end_time.date()} ({total_bars - split_idx} bars)")
    print("=" * 135)
    
    configs = [
        ("Gross 1.5% Cap", 0.015),
        ("Uncapped Risk", None)
    ]
    
    for label, rcap in configs:
        is_res = run_partition(all_sigs, dfs, pit_usdjpy, start_time, split_time, max_gross_risk_pct=rcap)
        oos_res = run_partition(all_sigs, dfs, pit_usdjpy, split_time, end_time, max_gross_risk_pct=rcap)
        
        exp_decay = (oos_res["mean_r"] / is_res["mean_r"]) if is_res["mean_r"] > 0 else 0.0
        pf_decay = (oos_res["net_pf"] / is_res["net_pf"]) if is_res["net_pf"] > 0 else 0.0
        
        print(f"\n 📊 PORTFOLIO CONFIGURATION: {label.upper()}")
        print(f"{'Partition':<18} | {'Trades':<8} | {'Win %':<7} | {'Net Mean R':<11} | {'Net PF':<8} | {'Comp Return %':<14} | {'True MaxDD':<10} | {'Pos Pairs':<9}")
        print("-" * 135)
        print(f"{'In-Sample (60%)':<18} | {is_res['trades']:<8d} | {is_res['win_rate']:>5.1f}% | {is_res['mean_r']:>+8.3f}R   | {is_res['net_pf']:>6.2f} | {is_res['return_pct']:>+12.2f}% | {is_res['max_dd_pct']:>8.2f}% | {is_res['pos_pairs']}/{is_res['evaluated_pairs']}")
        print(f"{'Out-of-Sample (40%)':<18} | {oos_res['trades']:<8d} | {oos_res['win_rate']:>5.1f}% | {oos_res['mean_r']:>+8.3f}R   | {oos_res['net_pf']:>6.2f} | {oos_res['return_pct']:>+12.2f}% | {oos_res['max_dd_pct']:>8.2f}% | {oos_res['pos_pairs']}/{oos_res['evaluated_pairs']}")
        print("-" * 135)
        print(f" [STABILITY DIAGNOSTIC] Expectancy Retention (OOS/IS): {exp_decay*100:>5.1f}% | PF Retention: {pf_decay*100:>5.1f}%")
        print("=" * 135)

if __name__ == "__main__":
    main()
