#!/usr/bin/env python3
"""
E4 Frozen Geometry Multi-Timeframe Dilution Suite (H1 & H4)
- Setup: 100 SMA | Entry: +/-2.5-Sigma Reclaim | Stop: +/-3.5-Sigma | Target: SMA100 Centerline
- Risk: 0.50% true USD risk per trade ($500 on $100k equity baseline)
- Exact point-in-time cross conversions & adverse stop-first collision logic
- Full Gross vs Friction vs Net attribution breakdown
"""

import os
import sys
import time
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

# 1. Credentials & Invariants
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

OANDA_ENV = "practice" if "practice" in os.getenv("OANDA_URL", "practice") else "live"

import oandapyV20
import oandapyV20.endpoints.instruments as instruments

client = oandapyV20.API(access_token=OANDA_TOKEN, environment=OANDA_ENV)

DATA_DIR = Path("/home/moonrider1968/bots/oanda_data")
(DATA_DIR / "h1").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "h4").mkdir(parents=True, exist_ok=True)

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
    gross_pnl_usd: float
    friction_usd: float
    net_pnl_usd: float
    gross_r: float
    friction_r: float
    net_r: float
    mae_r: float
    mfe_r: float
    exit_reason: str
    bars_held: int

def get_pip_value(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001

def fetch_oanda_candles(instrument: str, granularity: str, count: int = 5000) -> pd.DataFrame:
    params = {"count": count, "granularity": granularity, "price": "M"}
    req = instruments.InstrumentsCandles(instrument=instrument, params=params)
    client.request(req)
    data = req.response.get("candles", [])

    rows = []
    for c in data:
        if not c.get("complete", False):
            continue
        rows.append({
            "timestamp": pd.to_datetime(c["time"]),
            "open": float(c["mid"]["o"]),
            "high": float(c["mid"]["h"]),
            "low": float(c["mid"]["l"]),
            "close": float(c["mid"]["c"]),
            "volume": int(c["volume"])
        })
    df = pd.DataFrame(rows).sort_values("timestamp").set_index("timestamp")
    return df

def get_candle_dataset(symbol: str, granularity: str) -> pd.DataFrame:
    sub = granularity.lower()
    fpath = DATA_DIR / sub / f"{symbol}_{granularity}.csv"
    if not fpath.exists():
        print(f"[*] Downloading {granularity} data for {symbol}...")
        df = fetch_oanda_candles(symbol, granularity, count=5000)
        df.to_csv(fpath)
        time.sleep(0.3)
        return df
    df = pd.read_csv(fpath)
    time_col = next(c for c in df.columns if c.lower() in ["timestamp", "time", "date"])
    df[time_col] = pd.to_datetime(df[time_col], utc=True)
    return df.sort_values(time_col).set_index(time_col)

def generate_e4_signals(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = df.copy()
    pip_val = get_pip_value(symbol)
    
    df["ma"] = df["close"].rolling(100).mean()
    df["std"] = df["close"].rolling(100).std()
    
    df["upper_entry"] = df["ma"] + (2.5 * df["std"])
    df["lower_entry"] = df["ma"] - (2.5 * df["std"])
    df["upper_stop"] = df["ma"] + (3.5 * df["std"])
    df["lower_stop"] = df["ma"] - (3.5 * df["std"])
    
    signals = []
    for i in range(105, len(df) - 5):
        cur = df.iloc[i]
        prev = df.iloc[i - 1]
        t = df.index[i]
        
        # Long Reclaim: Prev < -2.5s -> Cur > -2.5s and Cur < MA
        if prev["close"] < prev["lower_entry"] and cur["close"] > cur["lower_entry"] and cur["close"] < cur["ma"]:
            stop_dist_quote = cur["close"] - cur["lower_stop"]
            stop_pips = max(6.0, stop_dist_quote / pip_val)
            signals.append({
                "timestamp": t,
                "side": "BUY",
                "stop_pips": round(stop_pips, 1),
                "ma_center": cur["ma"]
            })
            
        # Short Reclaim: Prev > +2.5s -> Cur < +2.5s and Cur > MA
        elif prev["close"] > prev["upper_entry"] and cur["close"] < cur["upper_entry"] and cur["close"] > cur["ma"]:
            stop_dist_quote = cur["upper_stop"] - cur["close"]
            stop_pips = max(6.0, stop_dist_quote / pip_val)
            signals.append({
                "timestamp": t,
                "side": "SELL",
                "stop_pips": round(stop_pips, 1),
                "ma_center": cur["ma"]
            })
            
    return pd.DataFrame(signals)

def evaluate_path_with_excursions(
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

        else: # SELL
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
    pit_usdjpy_series: Optional[pd.Series]
) -> Tuple[List[TradeRecord], dict]:
    pip_val = get_pip_value(symbol)
    spread_pips = SPREAD_PIPS_MAP.get(symbol, 1.5)
    half_spread = (spread_pips / 2.0) * pip_val
    entry_slip = ENTRY_SLIPPAGE_PIPS * pip_val
    exit_slip = EXIT_SLIPPAGE_PIPS * pip_val
    total_friction_pips = spread_pips + ENTRY_SLIPPAGE_PIPS + EXIT_SLIPPAGE_PIPS
    
    trades: List[TradeRecord] = []
    base, quote = symbol.split("_")

    for _, sig in signals.iterrows():
        entry_idx = df.index.get_loc(sig["timestamp"]) if sig["timestamp"] in df.index else None
        if entry_idx is None or entry_idx >= len(df) - 1:
            continue
            
        raw_entry = float(df["close"].iloc[entry_idx])
        side = sig["side"]
        stop_dist_quote = float(sig["stop_pips"]) * pip_val
        target_px = float(sig["ma_center"])
        
        if side == "BUY":
            entry_px = raw_entry + half_spread + entry_slip
            stop_px = entry_px - stop_dist_quote
            if target_px <= entry_px:
                continue
            target_pips = (target_px - entry_px) / pip_val
        else:
            entry_px = raw_entry - half_spread - entry_slip
            stop_px = entry_px + stop_dist_quote
            if target_px >= entry_px:
                continue
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
            
        exit_px, reason, bars_held, exit_time, mae_r, mfe_r = evaluate_path_with_excursions(
            df, entry_idx, side, entry_px, stop_px, target_px, pip_val, half_spread, exit_slip
        )
        
        raw_exit = exit_px + half_spread + exit_slip if side == "BUY" else exit_px - half_spread - exit_slip
        gross_pnl_quote = (raw_exit - raw_entry) * units if side == "BUY" else (raw_entry - raw_exit) * units
        friction_quote = (total_friction_pips * pip_val) * units
        net_pnl_quote = (exit_px - entry_px) * units if side == "BUY" else (entry_px - exit_px) * units

        gross_pnl_usd = gross_pnl_quote * quote_to_usd
        friction_usd = friction_quote * quote_to_usd
        net_pnl_usd = net_pnl_quote * quote_to_usd

        gross_r = gross_pnl_usd / target_dollar_risk
        friction_r = friction_usd / target_dollar_risk
        net_r = net_pnl_usd / target_dollar_risk

        trades.append(TradeRecord(
            symbol=symbol, side=side, entry_time=sig["timestamp"], exit_time=exit_time,
            entry_price=entry_px, exit_price=exit_px, stop_price=stop_px, target_price=target_px,
            risk_pips=float(sig["stop_pips"]), target_pips=target_pips, initial_target_r=initial_target_r,
            units=units, dollar_risk=target_dollar_risk, gross_pnl_usd=gross_pnl_usd, friction_usd=friction_usd,
            net_pnl_usd=net_pnl_usd, gross_r=gross_r, friction_r=friction_r, net_r=net_r,
            mae_r=mae_r, mfe_r=mfe_r, exit_reason=reason, bars_held=bars_held
        ))
        
    if not trades:
        return [], {}
        
    net_rs = [t.net_r for t in trades]
    gross_rs = [t.gross_r for t in trades]
    friction_rs = [t.friction_r for t in trades]
    
    net_wins = [r for r in net_rs if r > 0]
    net_losses = [abs(r) for r in net_rs if r < 0]
    gross_wins = [r for r in gross_rs if r > 0]
    gross_losses = [abs(r) for r in gross_rs if r < 0]
    
    net_pf = (sum(net_wins) / sum(net_losses)) if net_losses and sum(net_losses) > 0 else (99.0 if net_wins else 0.0)
    gross_pf = (sum(gross_wins) / sum(gross_losses)) if gross_losses and sum(gross_losses) > 0 else (99.0 if gross_wins else 0.0)
    
    target_hits = [t for t in trades if t.exit_reason == "TARGET_HIT"]
    stop_hits = [t for t in trades if t.exit_reason == "STOP_HIT"]
    
    return trades, {
        "trades": len(trades),
        "target_hit_pct": len(target_hits) / len(trades) * 100.0,
        "stop_hit_pct": len(stop_hits) / len(trades) * 100.0,
        "win_rate": len(net_wins) / len(trades) * 100.0,
        "gross_mean_r": float(np.mean(gross_rs)),
        "friction_r": float(np.mean(friction_rs)),
        "net_mean_r": float(np.mean(net_rs)),
        "gross_pf": gross_pf,
        "net_pf": net_pf,
        "median_r": float(np.median(net_rs)),
        "avg_target_r": float(np.mean([t.initial_target_r for t in trades])),
        "avg_stop_pips": float(np.mean([t.risk_pips for t in trades])),
        "avg_target_pips": float(np.mean([t.target_pips for t in trades])),
        "avg_mae_r": float(np.mean([t.mae_r for t in trades])),
        "avg_mfe_r": float(np.mean([t.mfe_r for t in trades])),
        "avg_bars_held": float(np.mean([t.bars_held for t in trades]))
    }

def print_timeframe_report(tf_label: str, results: Dict[str, dict], all_trades: List[TradeRecord]):
    print("\n" + "=" * 155)
    print(f" ⏱️ TIMEFRAME {tf_label}: 100 SMA | +/-2.5-Sigma Reclaim -> SMA100 Centerline")
    print("=" * 155)
    print(f"{'Symbol':<9} | {'Events':<6} | {'Target%':<7} | {'Stop%':<6} | {'Win%':<6} | {'Gross R':<8} | {'Frict R':<7} | {'Net R':<8} | {'GrossPF':<7} | {'NetPF':<6} | {'Med R':<7} | {'StopPip':<7} | {'TgtPip':<7} | {'HoldBar':<7} | {'MAE(R)':<6} | {'MFE(R)':<6}")
    print("-" * 155)
    for sym, m in results.items():
        print(f"{sym:<9} | {m['trades']:<6d} | {m['target_hit_pct']:>6.1f}% | {m['stop_hit_pct']:>5.1f}% | {m['win_rate']:>5.1f}% | {m['gross_mean_r']:>+7.3f}R | {m['friction_r']:>6.3f}R | {m['net_mean_r']:>+7.3f}R | {m['gross_pf']:>7.2f} | {m['net_pf']:>6.2f} | {m['median_r']:>+6.3f}R | {m['avg_stop_pips']:>6.1f}p | {m['avg_target_pips']:>6.1f}p | {m['avg_bars_held']:>6.1f}b | {m['avg_mae_r']:>5.2f}R | {m['avg_mfe_r']:>5.2f}R")
    print("=" * 155)

    all_net_r = [t.net_r for t in all_trades]
    all_gross_r = [t.gross_r for t in all_trades]
    all_frict_r = [t.friction_r for t in all_trades]
    
    net_wins = [r for r in all_net_r if r > 0]
    net_losses = [abs(r) for r in all_net_r if r < 0]
    gross_wins = [r for r in all_gross_r if r > 0]
    gross_losses = [abs(r) for r in all_gross_r if r < 0]
    
    pooled_net_pf = (sum(net_wins) / sum(net_losses)) if net_losses and sum(net_losses) > 0 else 0.0
    pooled_gross_pf = (sum(gross_wins) / sum(gross_losses)) if gross_losses and sum(gross_losses) > 0 else 0.0
    targets = [t for t in all_trades if t.exit_reason == "TARGET_HIT"]
    stops = [t for t in all_trades if t.exit_reason == "STOP_HIT"]
    pos_pairs = sum(1 for m in results.values() if m["net_mean_r"] > 0)

    print(f" [POOLED STATS] Events: {len(all_trades)} | Target%: {len(targets)/len(all_trades)*100:.1f}% | Stop%: {len(stops)/len(all_trades)*100:.1f}% | Win%: {len(net_wins)/len(all_trades)*100:.1f}%")
    print(f"                Gross Mean R: {np.mean(all_gross_r):+.3f}R | Friction/Trade: {np.mean(all_frict_r):.3f}R | Net Mean R: {np.mean(all_net_r):+.3f}R")
    print(f"                Gross PF: {pooled_gross_pf:.2f} | Net PF: {pooled_net_pf:.2f} | Median Net R: {np.median(all_net_r):+.3f}R | Pos Pairs: {pos_pairs}/9")
    print(f"                Avg Stop Pips: {np.mean([t.risk_pips for t in all_trades]):.1f}p | Avg Target Pips: {np.mean([t.target_pips for t in all_trades]):.1f}p | Avg Bars Held: {np.mean([t.bars_held for t in all_trades]):.1f}")
    print("=" * 155)

    return {
        "tf": tf_label,
        "events": len(all_trades),
        "win_rate": len(net_wins)/len(all_trades)*100.0,
        "gross_mean_r": float(np.mean(all_gross_r)),
        "friction_r": float(np.mean(all_frict_r)),
        "net_mean_r": float(np.mean(all_net_r)),
        "gross_pf": pooled_gross_pf,
        "net_pf": pooled_net_pf,
        "avg_stop_pips": float(np.mean([t.risk_pips for t in all_trades])),
        "avg_target_pips": float(np.mean([t.target_pips for t in all_trades])),
        "avg_bars_held": float(np.mean([t.bars_held for t in all_trades])),
        "pos_pairs": pos_pairs
    }

def main():
    print("=" * 120)
    print(" 🚀 EXECUTING E4 STATISTICAL REVERSION DILUTION BENCHMARK (H1 vs H4)")
    print("=" * 120)

    # Point-in-time USD_JPY rate
    df_uj_h1 = get_candle_dataset("USD_JPY", "H1")
    pit_usdjpy = df_uj_h1["close"].shift(1)

    summaries = []

    for tf in ["H1", "H4"]:
        res, trades = {}, []
        for sym in PAIRS:
            df = get_candle_dataset(sym, tf)
            sig_df = generate_e4_signals(df, sym)
            if not sig_df.empty:
                t, m = run_simulation(sym, df, sig_df, pit_usdjpy)
                if m:
                    res[sym] = m
                    trades.extend(t)
        if res:
            summaries.append(print_timeframe_report(tf, res, trades))

    print("\n" + "=" * 120)
    print(" 🏁 E4 TIMEFRAME DILUTION COMPARISON SUMMARY (M15 vs H1 vs H4)")
    print("=" * 120)
    print(f"{'Timeframe':<10} | {'Events':<6} | {'Avg Stop':<10} | {'Frict R':<9} | {'Gross R':<9} | {'Net R':<9} | {'Gross PF':<9} | {'Net PF':<8} | {'Pos Pairs':<9}")
    print("-" * 120)
    print(f"{'M15 (Ref)':<10} | {'738':<6} | {'23.4p':<10} | {'0.152R':<9} | {'+0.071R':<9} | {'-0.082R':<9} | {'1.12':<9} | {'0.88':<8} | {'2/9':<9}")
    for s in summaries:
        print(f"{s['tf']:<10} | {s['events']:<6d} | {s['avg_stop_pips']:>5.1f}p    | {s['friction_r']:>6.3f}R   | {s['gross_mean_r']:>+7.3f}R  | {s['net_mean_r']:>+7.3f}R  | {s['gross_pf']:>6.2f}    | {s['net_pf']:>6.2f}   | {s['pos_pairs']:>2d}/9")
    print("=" * 120)

if __name__ == "__main__":
    main()
