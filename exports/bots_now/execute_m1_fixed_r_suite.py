#!/usr/bin/env python3
"""
OANDA MicroBeast M1 Fixed-R Benchmark Suite
- Risk: 0.50% of current equity per trade
- Initial Stop: -1.00R (1.5x 14-ATR, minimum 6 pips)
- Profit Target: +1.50R
- Max Duration: 30 completed M1 candles
- Priority: Intraday Stop-First -> Target Hit -> Bar 30 Close Timeout
- Trade-by-trade compounding with full spread & slippage friction
"""

import os
import sys
import time
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

# Automatically parse .env if present
env_path = Path("/home/moonrider1968/bots/.env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v_clean = v.strip().strip('"').strip("'")
            os.environ.setdefault(k.strip(), v_clean)

import oandapyV20
import oandapyV20.endpoints.instruments as instruments

OANDA_TOKEN = os.getenv("OANDA_API_KEY") or os.getenv("OANDA_TOKEN") or "REDACTED_OANDA_TOKEN"
OANDA_ENV = "practice" if "practice" in os.getenv("OANDA_URL", "practice") else "live"

client = oandapyV20.API(access_token=OANDA_TOKEN, environment=OANDA_ENV)

OUTPUT_DIR = Path("/home/moonrider1968/bots/oanda_data/m1")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]

INITIAL_EQUITY = 100_000.0
RISK_PCT = 0.0050          # 0.50% equity risk per trade
TARGET_R = 1.50            # +1.50R target
STOP_R = 1.00              # -1.00R hard stop
MAX_HOLD_BARS = 30         # 30 M1 candles timeout
DEFAULT_SLIPPAGE_PIPS = 0.5

SPREAD_PIPS_MAP = {
    "EUR_USD": 1.0,
    "GBP_USD": 1.4,
    "USD_JPY": 1.1,
    "AUD_USD": 1.2,
    "USD_CAD": 1.5,
    "NZD_USD": 1.6,
    "USD_CHF": 1.5,
    "EUR_JPY": 1.6,
    "GBP_JPY": 2.2,
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
    gross_pnl: float
    cost_pnl: float
    net_pnl: float
    r_multiple: float
    exit_reason: str
    bars_held: int
    equity_after: float

def get_pip_value(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001

def fetch_oanda_m1(instrument: str, count: int = 5000) -> pd.DataFrame:
    params = {"count": count, "granularity": "M1", "price": "M"}
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

def generate_microbeast_m1_signals(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = df.copy()
    pip_val = get_pip_value(symbol)
    
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    
    tr = np.maximum(df["high"] - df["low"], np.maximum(abs(df["high"] - df["close"].shift(1)), abs(df["low"] - df["close"].shift(1))))
    df["atr14"] = tr.rolling(14).mean()
    df["atr_pips"] = df["atr14"] / pip_val
    
    signals = []
    for idx in range(200, len(df) - 35):
        row = df.iloc[idx]
        prev = df.iloc[idx - 1]
        timestamp = df.index[idx]
        
        atr_p = max(6.0, float(row["atr_pips"])) if pd.notna(row["atr_pips"]) else 10.0
        stop_pips = round(atr_p * 1.5, 1)
        
        # Long Entry: Trend Alignment + Momentum Breakout
        if (row["close"] > row["ema20"] > row["ema50"] > row["ema200"] and 
            prev["close"] <= prev["ema9"] and row["close"] > row["ema9"] and
            row["close"] > row["open"]):
            signals.append({
                "timestamp": timestamp,
                "side": "BUY",
                "stop_pips": stop_pips
            })
            
        # Short Entry: Downtrend Alignment + Momentum Rejection
        elif (row["close"] < row["ema20"] < row["ema50"] < row["ema200"] and 
              prev["close"] >= prev["ema9"] and row["close"] < row["ema9"] and
              row["close"] < row["open"]):
            signals.append({
                "timestamp": timestamp,
                "side": "SELL",
                "stop_pips": stop_pips
            })
            
    return pd.DataFrame(signals)

def evaluate_m1_trade_path(
    df: pd.DataFrame,
    entry_idx: int,
    side: str,
    entry_price: float,
    stop_price: float,
    target_price: float,
    pip_val: float,
    slippage_pips: float
) -> Tuple[float, str, int, pd.Timestamp]:
    max_idx = min(len(df), entry_idx + MAX_HOLD_BARS + 1)
    slip_distance = slippage_pips * pip_val

    for count, idx in enumerate(range(entry_idx + 1, max_idx), start=1):
        row = df.iloc[idx]
        open_px, high_px, low_px, close_px = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        bar_time = df.index[idx]

        if side == "BUY":
            # 1. Open Gap Check
            if open_px <= stop_price:
                return open_px - slip_distance, "STOP_HIT", count, bar_time
            if open_px >= target_price:
                return open_px - slip_distance, "TARGET_HIT", count, bar_time

            # 2. Intraday Stop (Stop First)
            if low_px <= stop_price:
                return stop_price - slip_distance, "STOP_HIT", count, bar_time

            # 3. Intraday Target
            if high_px >= target_price:
                return target_price - slip_distance, "TARGET_HIT", count, bar_time

        else: # SELL
            # 1. Open Gap Check
            if open_px >= stop_price:
                return open_px + slip_distance, "STOP_HIT", count, bar_time
            if open_px <= target_price:
                return open_px + slip_distance, "TARGET_HIT", count, bar_time

            # 2. Intraday Stop (Stop First)
            if high_px >= stop_price:
                return stop_price + slip_distance, "STOP_HIT", count, bar_time

            # 3. Intraday Target
            if low_px <= target_price:
                return target_price + slip_distance, "TARGET_HIT", count, bar_time

        # 4. Bar 30 Close Timeout
        if count == MAX_HOLD_BARS:
            exit_px = close_px - slip_distance if side == "BUY" else close_px + slip_distance
            return exit_px, "TIMEOUT_30M", count, bar_time

    last_row = df.iloc[-1]
    exit_px = float(last_row["close"]) - slip_distance if side == "BUY" else float(last_row["close"]) + slip_distance
    return exit_px, "TIMEOUT_30M", len(df) - entry_idx, df.index[-1]

def run_simulation(symbol: str, df: pd.DataFrame, signals: pd.DataFrame) -> Tuple[List[TradeRecord], dict]:
    equity = INITIAL_EQUITY
    pip_val = get_pip_value(symbol)
    spread_pips = SPREAD_PIPS_MAP.get(symbol, 1.5)
    slippage_pips = DEFAULT_SLIPPAGE_PIPS
    half_spread = (spread_pips / 2.0) * pip_val

    trades: List[TradeRecord] = []
    last_exit_idx = -1

    for _, sig in signals.iterrows():
        entry_idx = df.index.get_loc(sig["timestamp"]) if sig["timestamp"] in df.index else None
        if entry_idx is None or entry_idx <= last_exit_idx or entry_idx >= len(df) - 1:
            continue

        raw_entry = float(df["close"].iloc[entry_idx])
        side = sig["side"]
        stop_distance_pips = float(sig["stop_pips"])
        stop_dist_price = stop_distance_pips * pip_val

        if side == "BUY":
            entry_px = raw_entry + half_spread + (slippage_pips * pip_val)
            stop_px = entry_px - stop_dist_price
            target_px = entry_px + (TARGET_R * stop_dist_price)
        else:
            entry_px = raw_entry - half_spread - (slippage_pips * pip_val)
            stop_px = entry_px + stop_dist_price
            target_px = entry_px - (TARGET_R * stop_dist_price)

        dollar_risk = equity * RISK_PCT
        risk_per_unit = stop_dist_price
        units = dollar_risk / risk_per_unit if risk_per_unit > 0 else 0
        if units <= 0:
            continue

        exit_px, reason, bars_held, exit_time = evaluate_m1_trade_path(
            df, entry_idx, side, entry_px, stop_px, target_px, pip_val, slippage_pips
        )

        cost_pnl = (spread_pips * pip_val) * units
        gross_pnl = (exit_px - entry_px) * units if side == "BUY" else (entry_px - exit_px) * units
        net_pnl = gross_pnl - (half_spread * units)
        r_multiple = net_pnl / dollar_risk

        equity += net_pnl
        last_exit_idx = entry_idx + bars_held

        trades.append(TradeRecord(
            symbol=symbol,
            side=side,
            entry_time=sig["timestamp"],
            exit_time=exit_time,
            entry_price=entry_px,
            exit_price=exit_px,
            stop_price=stop_px,
            target_price=target_px,
            risk_pips=stop_distance_pips,
            units=units,
            gross_pnl=gross_pnl,
            cost_pnl=cost_pnl,
            net_pnl=net_pnl,
            r_multiple=r_multiple,
            exit_reason=reason,
            bars_held=bars_held,
            equity_after=equity
        ))

    if not trades:
        return [], {}

    pnls = [t.net_pnl for t in trades]
    r_mults = [t.r_multiple for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    
    target_hits = [t for t in trades if t.exit_reason == "TARGET_HIT"]
    stop_hits = [t for t in trades if t.exit_reason == "STOP_HIT"]
    timeouts = [t for t in trades if t.exit_reason == "TIMEOUT_30M"]

    eq_curve = [INITIAL_EQUITY] + [t.equity_after for t in trades]
    peaks = np.maximum.accumulate(eq_curve)
    dds = (peaks - eq_curve) / np.maximum(peaks, 1e-12)
    max_dd = float(np.max(dds) * 100.0)
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (99.0 if wins else 0.0)

    metrics = {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100.0,
        "pf": pf,
        "mean_r": float(np.mean(r_mults)),
        "net_return_pct": (equity / INITIAL_EQUITY - 1.0) * 100.0,
        "max_dd_pct": max_dd,
        "target_hit_pct": len(target_hits) / len(trades) * 100.0,
        "stop_hit_pct": len(stop_hits) / len(trades) * 100.0,
        "timeout_pct": len(timeouts) / len(trades) * 100.0,
        "avg_timeout_r": float(np.mean([t.r_multiple for t in timeouts])) if timeouts else 0.0,
        "ending_equity": equity
    }
    return trades, metrics

def main():
    print("=" * 135)
    print(" 🚀 OANDA M1 FIXED-R BENCHMARK SUITE (0.50% RISK | +1.5R TARGET | -1.0R STOP | 30-MIN TIMEOUT)")
    print("=" * 135)

    results = {}
    all_trades: List[TradeRecord] = []

    for sym in PAIRS:
        csv_file = OUTPUT_DIR / f"{sym}_M1.csv"
        
        # 1. Fetch live M1 candles if not already saved locally
        if not csv_file.exists():
            print(f"[*] Fetching 5,000 M1 candles for {sym} from OANDA API...")
            try:
                df = fetch_oanda_m1(sym, count=5000)
                df.to_csv(csv_file)
                time.sleep(0.3)
            except Exception as e:
                print(f"[!] Failed to fetch {sym}: {e}")
                continue
        else:
            df = pd.read_csv(csv_file)
            time_col = next((c for c in df.columns if c.lower() in ["timestamp", "time", "date"]), None)
            df[time_col] = pd.to_datetime(df[time_col], utc=True)
            df = df.sort_values(time_col).set_index(time_col)

        # 2. Generate Signals
        sig_df = generate_microbeast_m1_signals(df, sym)
        if sig_df.empty:
            print(f"[-] {sym:<10}: 0 signals generated across {len(df)} M1 bars.")
            continue

        # 3. Execute Fixed-R Simulation
        trades, metrics = run_simulation(sym, df, sig_df)
        if metrics:
            results[sym] = metrics
            all_trades.extend(trades)
            print(f"[+] {sym:<10}: {metrics['trades']:4d} trades | Return: {metrics['net_return_pct']:>+6.2f}% | PF: {metrics['pf']:.2f} | Max DD: {metrics['max_dd_pct']:.2f}% | Exp: {metrics['mean_r']:>+5.3f}R")

    if results:
        print()
        print("=" * 135)
        print(f"{'Symbol':<10} | {'Trades':<6} | {'Win %':<6} | {'PF':<5} | {'Mean R':<7} | {'Return %':<8} | {'Max DD':<7} | {'Target %':<8} | {'Stop %':<7} | {'Timeout %':<9} | {'Timeout R':<9}")
        print("=" * 135)
        for sym, m in results.items():
            print(f"{sym:<10} | {m['trades']:<6d} | {m['win_rate']:>5.1f}% | {m['pf']:>5.2f} | {m['mean_r']:>+6.3f}R | {m['net_return_pct']:>+7.2f}% | {m['max_dd_pct']:>6.2f}% | {m['target_hit_pct']:>7.1f}% | {m['stop_hit_pct']:>6.1f}% | {m['timeout_pct']:>8.1f}% | {m['avg_timeout_r']:>+8.3f}R")
        print("=" * 135)

        tot_trades = sum(m["trades"] for m in results.values())
        tot_wins = sum(int(m["trades"] * m["win_rate"] / 100.0) for m in results.values())
        all_r = [t.r_multiple for t in all_trades]
        all_timeouts = [t for t in all_trades if t.exit_reason == "TIMEOUT_30M"]
        all_targets = [t for t in all_trades if t.exit_reason == "TARGET_HIT"]
        all_stops = [t for t in all_trades if t.exit_reason == "STOP_HIT"]

        print("\n" + "=" * 80)
        print(" 📊 PORTFOLIO COMPOSITE METRICS")
        print("=" * 80)
        print(f" Total Trades:          {tot_trades}")
        print(f" Composite Win Rate:    {tot_wins / tot_trades * 100:.2f}%")
        print(f" Mean Expectancy (R):   {np.mean(all_r):+.3f}R")
        print(f" Target Hits (+1.5R):   {len(all_targets)} ({len(all_targets)/tot_trades*100:.1f}%)")
        print(f" Stop Hits (-1.0R):     {len(all_stops)} ({len(all_stops)/tot_trades*100:.1f}%)")
        print(f" Timeouts (30-Min):     {len(all_timeouts)} ({len(all_timeouts)/tot_trades*100:.1f}%) | Avg Timeout R: {np.mean([t.r_multiple for t in all_timeouts]):+.3f}R")
        print("=" * 80)

if __name__ == "__main__":
    main()
