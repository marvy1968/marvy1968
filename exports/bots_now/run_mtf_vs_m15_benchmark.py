#!/usr/bin/env python3
"""
MicroBeast Option A (H1->M5 MTF) vs Option B (Native M15) Clean Alpha Benchmark
- Strict fail-closed token validation
- Point-in-time H1 structural shift (.shift(1)) to eliminate look-ahead
- True USD risk per unit across USD-quoted, USD-base, and Cross pairs
- Pure bid/ask spread + slippage modeling on entry and exit
- Pooled R-statistical evaluation
"""

import os
import sys
import time
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

# 1. Environment & Token Invariants
env_path = Path("/home/moonrider1968/bots/.env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

OANDA_TOKEN = os.getenv("OANDA_API_KEY") or os.getenv("OANDA_TOKEN")
if not OANDA_TOKEN:
    raise RuntimeError("Missing OANDA_API_KEY / OANDA_TOKEN in environment or .env file.")

OANDA_ENV = "practice" if "practice" in os.getenv("OANDA_URL", "practice") else "live"

import oandapyV20
import oandapyV20.endpoints.instruments as instruments

client = oandapyV20.API(access_token=OANDA_TOKEN, environment=OANDA_ENV)

DATA_DIR = Path("/home/moonrider1968/bots/oanda_data")
(DATA_DIR / "m5").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "h1").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "m15").mkdir(parents=True, exist_ok=True)

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]

INITIAL_EQUITY = 100_000.0
RISK_PCT = 0.0050          # 0.50% equity risk per trade ($500 on $100k)
TARGET_R = 1.50            # +1.50R target
STOP_R = 1.00              # -1.00R hard stop
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
    equity_after: float

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

def get_quote_to_usd_conversion(symbol: str, current_price: float, cross_rates: Dict[str, float]) -> float:
    """
    Returns multiplier to convert quote-currency PnL into USD.
    - XXX/USD: Quote is USD -> multiplier = 1.0
    - USD/XXX: Quote is XXX -> multiplier = 1.0 / current_price
    - EUR/JPY, GBP/JPY: Quote is JPY -> multiplier = 1.0 / USD_JPY price
    """
    base, quote = symbol.split("_")
    if quote == "USD":
        return 1.0
    elif base == "USD":
        return 1.0 / current_price if current_price > 0 else 1.0
    elif quote == "JPY":
        usdjpy = cross_rates.get("USD_JPY", 150.0)
        return 1.0 / usdjpy if usdjpy > 0 else 1.0
    return 1.0

# -------------------------------------------------------------------------
# OPTION A: H1 Bias (.shift(1) PIT) + M5 3-Bar Pullback Reversal
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
    
    # Point-In-Time Shift: Use only previously completed H1 candle bias
    h1["bias_pit"] = h1["bias"].shift(1)
    
    m5 = df_m5.copy()
    m5["h1_bias"] = h1["bias_pit"].reindex(m5.index, method="ffill").fillna(0)
    m5["ema20"] = m5["close"].ewm(span=20, adjust=False).mean()
    
    signals = []
    for i in range(25, len(m5) - 5):
        bias = m5["h1_bias"].iloc[i]
        if bias == 0:
            continue
            
        cur = m5.iloc[i]
        p1 = m5.iloc[i-1]
        p2 = m5.iloc[i-2]
        p3 = m5.iloc[i-3]
        
        # Bullish Pullback: 3 consecutive lower highs/lows into EMA20 -> Reversal close
        if bias == 1:
            pullback = (p3["high"] > p2["high"] > p1["high"]) and (p1["low"] <= p1["ema20"] or p2["low"] <= p2["ema20"])
            trigger = cur["close"] > cur["open"] and cur["close"] > p1["high"] and cur["close"] > cur["ema20"]
            if pullback and trigger:
                swing_low = min(p1["low"], p2["low"], p3["low"], cur["low"])
                stop_pips = max(6.0, (cur["close"] - swing_low) / pip_val)
                signals.append({"timestamp": m5.index[i], "side": "BUY", "stop_pips": round(stop_pips, 1)})
                
        # Bearish Pullback: 3 consecutive higher lows/highs into EMA20 -> Reversal close
        elif bias == -1:
            pullback = (p3["low"] < p2["low"] < p1["low"]) and (p1["high"] >= p1["ema20"] or p2["high"] >= p2["ema20"])
            trigger = cur["close"] < cur["open"] and cur["close"] < p1["low"] and cur["close"] < cur["ema20"]
            if pullback and trigger:
                swing_high = max(p1["high"], p2["high"], p3["high"], cur["high"])
                stop_pips = max(6.0, (swing_high - cur["close"]) / pip_val)
                signals.append({"timestamp": m5.index[i], "side": "SELL", "stop_pips": round(stop_pips, 1)})
                
    return pd.DataFrame(signals)

# -------------------------------------------------------------------------
# OPTION B: Native M15 Single Timeframe Engine
# -------------------------------------------------------------------------
def generate_option_b_signals(df_m15: pd.DataFrame, symbol: str) -> pd.DataFrame:
    pip_val = get_pip_value(symbol)
    m15 = df_m15.copy()
    m15["sma20"] = m15["close"].rolling(20).mean()
    m15["sma200"] = m15["close"].rolling(200).mean()
    m15["sma20_slope"] = m15["sma20"] - m15["sma20"].shift(1)
    m15["ema20"] = m15["close"].ewm(span=20, adjust=False).mean()
    
    signals = []
    for i in range(200, len(m15) - 5):
        cur = m15.iloc[i]
        p1 = m15.iloc[i-1]
        p2 = m15.iloc[i-2]
        p3 = m15.iloc[i-3]
        
        # Bullish M15
        if cur["close"] > cur["sma200"] and cur["sma20_slope"] > 0:
            pullback = (p3["high"] > p2["high"] > p1["high"]) and (p1["low"] <= p1["ema20"] or p2["low"] <= p2["ema20"])
            trigger = cur["close"] > cur["open"] and cur["close"] > p1["high"]
            if pullback and trigger:
                swing_low = min(p1["low"], p2["low"], p3["low"], cur["low"])
                stop_pips = max(8.0, (cur["close"] - swing_low) / pip_val)
                signals.append({"timestamp": m15.index[i], "side": "BUY", "stop_pips": round(stop_pips, 1)})
                
        # Bearish M15
        elif cur["close"] < cur["sma200"] and cur["sma20_slope"] < 0:
            pullback = (p3["low"] < p2["low"] < p1["low"]) and (p1["high"] >= p1["ema20"] or p2["high"] >= p2["ema20"])
            trigger = cur["close"] < cur["open"] and cur["close"] < p1["low"]
            if pullback and trigger:
                swing_high = max(p1["high"], p2["high"], p3["high"], cur["high"])
                stop_pips = max(8.0, (swing_high - cur["close"]) / pip_val)
                signals.append({"timestamp": m15.index[i], "side": "SELL", "stop_pips": round(stop_pips, 1)})
                
    return pd.DataFrame(signals)

# -------------------------------------------------------------------------
# SIMULATION ENGINE: Strict Stop-First & True USD Conversions
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

def run_strategy(symbol: str, df: pd.DataFrame, signals: pd.DataFrame, cross_rates: Dict[str, float]) -> Tuple[List[TradeRecord], dict]:
    equity = INITIAL_EQUITY
    pip_val = get_pip_value(symbol)
    spread_pips = SPREAD_PIPS_MAP.get(symbol, 1.5)
    half_spread = (spread_pips / 2.0) * pip_val
    entry_slip = ENTRY_SLIPPAGE_PIPS * pip_val
    exit_slip = EXIT_SLIPPAGE_PIPS * pip_val
    
    trades: List[TradeRecord] = []
    last_exit_idx = -1
    
    for _, sig in signals.iterrows():
        entry_idx = df.index.get_loc(sig["timestamp"]) if sig["timestamp"] in df.index else None
        if entry_idx is None or entry_idx <= last_exit_idx or entry_idx >= len(df) - 1:
            continue
            
        raw_entry = float(df["close"].iloc[entry_idx])
        side = sig["side"]
        stop_dist_quote = float(sig["stop_pips"]) * pip_val
        
        # Transparent Execution Boundaries
        if side == "BUY":
            entry_px = raw_entry + half_spread + entry_slip
            stop_px = entry_px - stop_dist_quote
            target_px = entry_px + (TARGET_R * stop_dist_quote)
        else:
            entry_px = raw_entry - half_spread - entry_slip
            stop_px = entry_px + stop_dist_quote
            target_px = entry_px - (TARGET_R * stop_dist_quote)
            
        # Exact True USD Risk Sizing
        quote_to_usd = get_quote_to_usd_conversion(symbol, entry_px, cross_rates)
        usd_risk_per_unit = stop_dist_quote * quote_to_usd
        target_dollar_risk = equity * RISK_PCT
        
        units = target_dollar_risk / usd_risk_per_unit if usd_risk_per_unit > 0 else 0
        if units <= 0:
            continue
            
        exit_px, reason, bars_held, exit_time = evaluate_path(
            df, entry_idx, side, entry_px, stop_px, target_px, pip_val, half_spread, exit_slip
        )
        
        quote_pnl = (exit_px - entry_px) * units if side == "BUY" else (entry_px - exit_px) * units
        net_pnl_usd = quote_pnl * quote_to_usd
        r_mult = net_pnl_usd / target_dollar_risk
        
        equity += net_pnl_usd
        last_exit_idx = entry_idx + bars_held
        
        trades.append(TradeRecord(
            symbol=symbol, side=side, entry_time=sig["timestamp"], exit_time=exit_time,
            entry_price=entry_px, exit_price=exit_px, stop_price=stop_px, target_price=target_px,
            risk_pips=float(sig["stop_pips"]), units=units, dollar_risk=target_dollar_risk,
            net_pnl_usd=net_pnl_usd, r_multiple=r_mult, exit_reason=reason, bars_held=bars_held,
            equity_after=equity
        ))
        
    if not trades:
        return [], {}
        
    r_mults = [t.r_multiple for t in trades]
    wins = [r for r in r_mults if r > 0]
    losses = [abs(r) for r in r_mults if r < 0]
    target_hits = [t for t in trades if t.exit_reason == "TARGET_HIT"]
    stop_hits = [t for t in trades if t.exit_reason == "STOP_HIT"]
    
    eq_curve = [INITIAL_EQUITY] + [t.equity_after for t in trades]
    peaks = np.maximum.accumulate(eq_curve)
    dds = (peaks - eq_curve) / np.maximum(peaks, 1e-12)
    max_dd = float(np.max(dds) * 100.0)
    pf_r = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (99.0 if wins else 0.0)
    
    return trades, {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100.0,
        "pf_r": pf_r,
        "mean_r": float(np.mean(r_mults)),
        "median_r": float(np.median(r_mults)),
        "target_hit_pct": len(target_hits) / len(trades) * 100.0,
        "stop_hit_pct": len(stop_hits) / len(trades) * 100.0,
        "net_return_pct": (equity / INITIAL_EQUITY - 1.0) * 100.0,
        "max_dd_pct": max_dd,
        "ending_equity": equity
    }

def print_pooled_summary(name: str, results: Dict[str, dict], all_trades: List[TradeRecord]):
    print("\n" + "=" * 135)
    print(f" 📊 {name.upper()} - PAIR-BY-PAIR PERFORMANCE MATRIX")
    print("=" * 135)
    print(f"{'Symbol':<10} | {'Trades':<6} | {'Target %':<8} | {'Stop %':<7} | {'Win %':<6} | {'Mean R':<7} | {'PF (R)':<6} | {'Median R':<8} | {'Return %':<8} | {'Max DD':<7}")
    print("-" * 135)
    for sym, m in results.items():
        print(f"{sym:<10} | {m['trades']:<6d} | {m['target_hit_pct']:>7.1f}% | {m['stop_hit_pct']:>6.1f}% | {m['win_rate']:>5.1f}% | {m['mean_r']:>+6.3f}R | {m['pf_r']:>6.2f} | {m['median_r']:>+7.3f}R | {m['net_return_pct']:>+7.2f}% | {m['max_dd_pct']:>6.2f}%")
    print("=" * 135)

    all_r = [t.r_multiple for t in all_trades]
    wins_r = [r for r in all_r if r > 0]
    loss_r = [abs(r) for r in all_r if r < 0]
    pooled_pf = (sum(wins_r) / sum(loss_r)) if loss_r and sum(loss_r) > 0 else 0.0
    targets = [t for t in all_trades if t.exit_reason == "TARGET_HIT"]
    stops = [t for t in all_trades if t.exit_reason == "STOP_HIT"]
    pos_pairs = sum(1 for m in results.values() if m["mean_r"] > 0)

    print(f" POOLED R STATS: Total Trades: {len(all_trades)} | Win Rate: {len(wins_r)/len(all_trades)*100:.1f}% | Mean Expectancy: {np.mean(all_r):+.3f}R | PF (R): {pooled_pf:.2f}")
    print(f" TARGET/STOP RATIO: Target (+1.5R): {len(targets)} ({len(targets)/len(all_trades)*100:.1f}%) | Stop (-1.0R): {len(stops)} ({len(stops)/len(all_trades)*100:.1f}%) | Breadth: {pos_pairs}/9 Positive Pairs")
    print("=" * 135)

def main():
    print("=" * 115)
    print(" 🚀 RIGOROUS BENCHMARK: OPTION A (H1->M5 MTF) vs OPTION B (NATIVE M15)")
    print("=" * 115)
    
    # Pre-fetch USD_JPY rate for cross conversions
    df_uj = get_candle_dataset("USD_JPY", "H1")
    latest_usdjpy = float(df_uj["close"].iloc[-1])
    cross_rates = {"USD_JPY": latest_usdjpy}
    
    # Run Option A
    res_a, trades_a = {}, []
    for sym in PAIRS:
        df_m5 = get_candle_dataset(sym, "M5")
        df_h1 = get_candle_dataset(sym, "H1")
        sig_a = generate_option_a_signals(df_m5, df_h1, sym)
        if not sig_a.empty:
            t, m = run_strategy(sym, df_m5, sig_a, cross_rates)
            if m:
                res_a[sym] = m
                trades_a.extend(t)
    if res_a:
        print_pooled_summary("Option A (H1->M5 MTF Pullback Engine)", res_a, trades_a)

    # Run Option B
    res_b, trades_b = {}, []
    for sym in PAIRS:
        df_m15 = get_candle_dataset(sym, "M15")
        sig_b = generate_option_b_signals(df_m15, sym)
        if not sig_b.empty:
            t, m = run_strategy(sym, df_m15, sig_b, cross_rates)
            if m:
                res_b[sym] = m
                trades_b.extend(t)
    if res_b:
        print_pooled_summary("Option B (Native M15 Single-Timeframe Engine)", res_b, trades_b)

if __name__ == "__main__":
    main()
