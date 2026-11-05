#!/usr/bin/env python3
"""
Crypto Mean-Reversion: Quantitative Parameter Optimization Grid (2021-2026)
Tests:
- SMA Window: 20, 30, 50
- Entry Sigma: -2.0, -2.5, -2.8, -3.2 (Stop at -1.0 sigma below entry)
- Macro Trend Gate: None vs. BTC > 200 SMA
- Time-based Exit: None vs. 10-bar hard time stop
"""

import sys
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import itertools
import numpy as np
import pandas as pd
import yfinance as yf

INITIAL_NAV = 50000.0
BASE_RISK_PCT = 0.0050      # 0.50% NAV risk
MAX_CONCURRENT_POSITIONS = 3
SLIPPAGE_BPS = 15.0         # 15 bps taker fee + slip
GAP_PENALTY_BPS = 25.0

CRYPTO_CLUSTERS = {
    "BTC-USD": "CORE_L1",
    "ETH-USD": "CORE_L1",
    "SOL-USD": "ALT_L1",
    "AVAX-USD": "ALT_L1",
    "DOT-USD": "ALT_L1",
    "NEAR-USD": "ALT_L1",
    "UNI-USD": "DEFI",
    "AAVE-USD": "DEFI",
    "MKR-USD": "DEFI",
    "CRV-USD": "DEFI",
    "LINK-USD": "INFRA",
    "GRT-USD": "INFRA",
    "LDO-USD": "INFRA",
    "LTC-USD": "LEGACY",
    "BCH-USD": "LEGACY",
    "XRP-USD": "LEGACY"
}

def load_crypto_dataset(start_date="2020-01-01", end_date="2026-09-01") -> Dict[str, pd.DataFrame]:
    print(f"📥 Loading historical daily UTC bars for 16 crypto assets ({start_date} to {end_date})...")
    data = {}
    for sym in CRYPTO_CLUSTERS.keys():
        try:
            df = yf.download(sym, start=start_date, end=end_date, interval="1d", progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
            df = df.dropna(subset=["close", "high", "low", "open"])
            if len(df) >= 210:
                data[sym] = df
        except Exception as e:
            print(f"Failed to fetch {sym}: {e}")
    return data

def precompute_indicators(data: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    processed = {}
    for sym, df in data.items():
        d = df.copy()
        for sma in [20, 30, 50, 200]:
            d[f"ma{sma}"] = d["close"].rolling(sma).mean()
            d[f"std{sma}"] = d["close"].rolling(sma).std()
            for sig in [2.0, 2.5, 2.8, 3.2, 3.0, 3.5, 3.8, 4.2]:
                d[f"lower_{sma}_{int(sig*10)}s"] = d[f"ma{sma}"] - (sig * d[f"std{sma}"])
        processed[sym] = d
    return processed

def run_grid_backtest(
    data: Dict[str, pd.DataFrame],
    sma_len: int,
    entry_sigma: float,
    use_btc_macro: bool,
    max_hold_days: int,
    start_eval_date: str = "2021-09-01",
    end_eval_date: str = "2026-08-28"
) -> dict:

    stop_sigma = entry_sigma + 1.0
    entry_key = f"lower_{sma_len}_{int(entry_sigma*10)}s"
    stop_key = f"lower_{sma_len}_{int(stop_sigma*10)}s"
    ma_key = f"ma{sma_len}"

    btc_df = data.get("BTC-USD")
    start_dt = pd.to_datetime(start_eval_date)
    end_dt = pd.to_datetime(end_eval_date)

    all_dates = sorted(list(set.union(*[set(df.index) for df in data.values()])))
    sim_dates = [d for d in all_dates if start_dt <= d <= end_dt]

    current_nav = INITIAL_NAV
    open_positions = []
    closed_trades = []
    nav_series = []

    for cur_date in sim_dates:
        # Check BTC Macro Gate
        btc_bullish = True
        if use_btc_macro and btc_df is not None and cur_date in btc_df.index:
            btc_bar = btc_df.loc[cur_date]
            btc_bullish = float(btc_bar["close"]) > float(btc_bar["ma200"])

        # 1. Manage Exits
        retained = []
        for pos in open_positions:
            sym = pos["symbol"]
            df = data[sym]
            if cur_date not in df.index:
                retained.append(pos)
                continue

            bar = df.loc[cur_date]
            high_px = float(bar["high"])
            low_px = float(bar["low"])
            open_px = float(bar["open"])
            close_px = float(bar["close"])

            bars_held = (cur_date - pos["entry_date"]).days

            hit_stop = low_px <= pos["stop_price"]
            hit_target = high_px >= pos["target_price"]
            hit_time_stop = (max_hold_days > 0 and bars_held >= max_hold_days)

            if hit_stop:
                exit_px = min(open_px, pos["stop_price"]) * (1.0 - (SLIPPAGE_BPS + GAP_PENALTY_BPS) / 10000.0)
                pnl = (exit_px - pos["entry_price"]) * pos["units"]
                r_mult = pnl / pos["risk_usd"]
                current_nav += pnl
                closed_trades.append({"r": r_mult, "pnl": pnl, "win": pnl > 0})
            elif hit_target:
                exit_px = max(open_px, pos["target_price"]) * (1.0 - SLIPPAGE_BPS / 10000.0)
                pnl = (exit_px - pos["entry_price"]) * pos["units"]
                r_mult = pnl / pos["risk_usd"]
                current_nav += pnl
                closed_trades.append({"r": r_mult, "pnl": pnl, "win": pnl > 0})
            elif hit_time_stop:
                exit_px = close_px * (1.0 - SLIPPAGE_BPS / 10000.0)
                pnl = (exit_px - pos["entry_price"]) * pos["units"]
                r_mult = pnl / pos["risk_usd"]
                current_nav += pnl
                closed_trades.append({"r": r_mult, "pnl": pnl, "win": pnl > 0})
            else:
                retained.append(pos)

        open_positions = retained

        # MTM NAV
        mtm = current_nav
        for pos in open_positions:
            df = data[pos["symbol"]]
            if cur_date in df.index:
                mtm += (float(df.loc[cur_date]["close"]) - pos["entry_price"]) * pos["units"]
        nav_series.append(mtm)

        # 2. Entries
        if len(open_positions) < MAX_CONCURRENT_POSITIONS:
            if use_btc_macro and not btc_bullish:
                continue

            occupied_clusters = set(p["cluster"] for p in open_positions)
            active_symbols = set(p["symbol"] for p in open_positions)

            for sym, cluster in CRYPTO_CLUSTERS.items():
                if len(open_positions) >= MAX_CONCURRENT_POSITIONS:
                    break
                if sym in active_symbols or cluster in occupied_clusters:
                    continue

                df = data.get(sym)
                if df is None or cur_date not in df.index:
                    continue

                idx = df.index.get_loc(cur_date)
                if idx < sma_len + 5:
                    continue

                cur_bar = df.iloc[idx]
                prev_bar = df.iloc[idx - 1]

                if entry_key not in cur_bar or pd.isna(cur_bar[entry_key]):
                    continue

                # Reclaim condition
                if (prev_bar["close"] < prev_bar[entry_key]) and (cur_bar["close"] > cur_bar[entry_key]) and (cur_bar["close"] < cur_bar[ma_key]):
                    entry_px = float(cur_bar["close"]) * (1.0 + SLIPPAGE_BPS / 10000.0)
                    stop_px = float(cur_bar[stop_key])
                    target_px = float(cur_bar[ma_key])
                    stop_dist = entry_px - stop_px

                    if stop_dist > (entry_px * 0.005):
                        t_risk = mtm * BASE_RISK_PCT
                        units = t_risk / stop_dist
                        open_positions.append({
                            "symbol": sym, "cluster": cluster, "entry_date": cur_date,
                            "entry_price": entry_px, "stop_price": stop_px, "target_price": target_px,
                            "units": units, "risk_usd": t_risk
                        })
                        occupied_clusters.add(cluster)
                        active_symbols.add(sym)

    s = pd.Series(nav_series)
    max_dd = abs(((s - s.cummax()) / s.cummax()).min()) * 100.0 if not s.empty else 0.0
    tot_ret = ((current_nav - INITIAL_NAV) / INITIAL_NAV) * 100.0

    r_vals = [t["r"] for t in closed_trades]
    wins = [r for r in r_vals if r > 0]
    losses = [abs(r) for r in r_vals if r < 0]

    wr = (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0
    mean_r = np.mean(r_vals) if r_vals else 0.0
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else 0.0

    return {
        "sma": sma_len,
        "entry_sigma": entry_sigma,
        "btc_macro": "YES" if use_btc_macro else "NO",
        "time_stop": f"{max_hold_days}d" if max_hold_days > 0 else "NONE",
        "trades": len(closed_trades),
        "win_rate": wr,
        "mean_r": mean_r,
        "pf": pf,
        "return": tot_ret,
        "max_dd": max_dd,
        "score": (tot_ret / max_dd) if max_dd > 0 else 0.0
    }

def main():
    print("=" * 125)
    print(" 🚀 CRYPTO MEAN-REVERSION: 5-YEAR PARAMETER OPTIMIZATION GRID (2021-2026)")
    print("=" * 125)

    raw_data = load_crypto_dataset()
    data = precompute_indicators(raw_data)

    smas = [20, 30, 50]
    sigmas = [2.0, 2.5, 2.8, 3.2]
    btc_gates = [False, True]
    time_stops = [0, 10]

    combinations = list(itertools.product(smas, sigmas, btc_gates, time_stops))
    print(f"\n⚡ Executing backtest across {len(combinations)} unique parameter combinations...\n")

    results = []
    for sma, sig, btc, t_stop in combinations:
        res = run_grid_backtest(data, sma, sig, btc, t_stop)
        results.append(res)

    df_res = pd.DataFrame(results)
    
    # Sort by Return / MaxDD efficiency score
    df_sorted = df_res.sort_values(by="score", ascending=False).reset_index(drop=True)

    formatted_df = df_sorted.copy()
    formatted_df["win_rate"] = formatted_df["win_rate"].apply(lambda x: f"{x:.1f}%")
    formatted_df["mean_r"] = formatted_df["mean_r"].apply(lambda x: f"{x:+.3f}R")
    formatted_df["pf"] = formatted_df["pf"].apply(lambda x: f"{x:.2f}")
    formatted_df["return"] = formatted_df["return"].apply(lambda x: f"{x:+.2f}%")
    formatted_df["max_dd"] = formatted_df["max_dd"].apply(lambda x: f"{x:.2f}%")
    formatted_df["score"] = formatted_df["score"].apply(lambda x: f"{x:.2f}")

    print(" 🏆 TOP 15 OPTIMAL CONFIGURATIONS (Ranked by Return / MaxDD):")
    print("-" * 125)
    print(formatted_df.head(15).to_string(index=False))
    print("=" * 125)

    print("\n ⚠️ BOTTOM 5 CONFIGURATIONS (Vulnerable to Liquidation Cascades):")
    print("-" * 125)
    print(formatted_df.tail(5).to_string(index=False))
    print("=" * 125)

if __name__ == "__main__":
    main()
