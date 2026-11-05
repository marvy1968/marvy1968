#!/usr/bin/env python3
"""
Crypto Mean-Reversion: Multi-Expert Ensemble Diagnostic (2021-2026)
Experts Implemented:
- Expert 1 (Weinstein Stage): 200 SMA Flat/Rising Slope or BTC Macro Support
- Expert 2 (Oliver Velez Reversal): Reclaim close in upper 35% of bar range & body > 0.8 ATR
- Expert 3 (RSI Exhaustion): RSI(14) < 35 with upward momentum hook
- Expert 4 (Volume Flow): Volume > 1.25x 20-day average
Evaluates: Baseline (-3.2s) vs. +1 Expert, +2 Experts, and Full Consensus
"""

import sys
from dataclasses import dataclass
from typing import List, Dict, Tuple
import numpy as np
import pandas as pd
import yfinance as yf

INITIAL_NAV = 50000.0
BASE_RISK_PCT = 0.0050
MAX_CONCURRENT_POSITIONS = 3
SLIPPAGE_BPS = 15.0
GAP_PENALTY_BPS = 25.0

CRYPTO_CLUSTERS = {
    "BTC-USD": "CORE_L1",
    "ETH-USD": "CORE_L1",
    "SOL-USD": "ALT_L1",
    "AVAX-USD": "ALT_L1",
    "DOT-USD": "ALT_L1",
    "UNI-USD": "DEFI",
    "AAVE-USD": "DEFI",
    "CRV-USD": "DEFI",
    "LINK-USD": "INFRA",
    "GRT-USD": "INFRA",
    "LDO-USD": "INFRA",
    "LTC-USD": "LEGACY",
    "BCH-USD": "LEGACY",
    "XRP-USD": "LEGACY"
}

@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    r_multiple: float
    net_pnl: float

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ma20"] = d["close"].rolling(20).mean()
    d["std20"] = d["close"].rolling(20).std()
    d["lower_32s"] = d["ma20"] - (3.2 * d["std20"])
    d["lower_42s"] = d["ma20"] - (4.2 * d["std20"])
    d["ma200"] = d["close"].rolling(200).mean()
    d["vol_ma20"] = d["volume"].rolling(20).mean()

    # ATR(14)
    hl = d["high"] - d["low"]
    hc = (d["high"] - d["close"].shift(1)).abs()
    lc = (d["low"] - d["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(14).mean()

    # RSI(14)
    delta = d["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    d["rsi14"] = 100 - (100 / (1 + rs))

    return d

def load_data() -> Dict[str, pd.DataFrame]:
    data = {}
    for sym in CRYPTO_CLUSTERS.keys():
        try:
            df = yf.download(sym, start="2020-01-01", end="2026-09-01", interval="1d", progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
            df = df.dropna(subset=["close", "high", "low", "open", "volume"])
            if sym == "UNI-USD":
                df = df[df["close"] > 0.50]
            if len(df) >= 210:
                data[sym] = compute_indicators(df)
        except Exception:
            pass
    return data

def simulate_ensemble(data: Dict[str, pd.DataFrame], active_experts: List[str]) -> dict:
    start_dt = pd.to_datetime("2021-09-01")
    end_dt = pd.to_datetime("2026-08-28")

    btc_df = data.get("BTC-USD")
    all_dates = sorted(list(set.union(*[set(df.index) for df in data.values()])))
    sim_dates = [d for d in all_dates if start_dt <= d <= end_dt]

    current_nav = INITIAL_NAV
    open_positions = []
    closed_trades = []
    nav_series = []

    for cur_date in sim_dates:
        # Exits
        retained = []
        for pos in open_positions:
            df = data[pos["symbol"]]
            if cur_date not in df.index:
                retained.append(pos)
                continue
            bar = df.loc[cur_date]
            hit_stop = float(bar["low"]) <= pos["stop_px"]
            hit_target = float(bar["high"]) >= pos["target_px"]

            if hit_stop and hit_target:
                hit_target = False

            if hit_stop:
                exit_px = min(float(bar["open"]), pos["stop_px"]) * (1.0 - (SLIPPAGE_BPS + GAP_PENALTY_BPS) / 10000.0)
                pnl = (exit_px - pos["entry_px"]) * pos["units"]
                current_nav += pnl
                closed_trades.append(Trade(pos["symbol"], pos["entry_date"], cur_date, pnl / pos["risk_usd"], pnl))
            elif hit_target:
                exit_px = max(float(bar["open"]), pos["target_px"]) * (1.0 - SLIPPAGE_BPS / 10000.0)
                pnl = (exit_px - pos["entry_px"]) * pos["units"]
                current_nav += pnl
                closed_trades.append(Trade(pos["symbol"], pos["entry_date"], cur_date, pnl / pos["risk_usd"], pnl))
            else:
                retained.append(pos)
        open_positions = retained

        # MTM NAV
        mtm = current_nav
        for pos in open_positions:
            df = data[pos["symbol"]]
            if cur_date in df.index:
                mtm += (float(df.loc[cur_date]["close"]) - pos["entry_px"]) * pos["units"]
        nav_series.append(mtm)

        # Entries
        if len(open_positions) < MAX_CONCURRENT_POSITIONS:
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
                if idx < 205:
                    continue

                cur_bar = df.iloc[idx]
                prev_bar = df.iloc[idx - 1]

                if pd.isna(cur_bar["lower_32s"]):
                    continue

                # Baseline Reclaim
                base_reclaim = (prev_bar["close"] < prev_bar["lower_32s"]) and (cur_bar["close"] > cur_bar["lower_32s"]) and (cur_bar["close"] < cur_bar["ma20"])
                if not base_reclaim:
                    continue

                # Expert 1: Stage / 200 SMA Slope
                pass_stage = True
                if "STAGE" in active_experts:
                    sma200_now = cur_bar["ma200"]
                    sma200_prev20 = df.iloc[idx - 20]["ma200"]
                    pass_stage = (cur_bar["close"] > sma200_now) or (sma200_now >= sma200_prev20)

                # Expert 2: Oliver Velez Reversal Bar Location
                pass_velez = True
                if "VELEZ" in active_experts:
                    rng = cur_bar["high"] - cur_bar["low"]
                    close_location = (cur_bar["close"] - cur_bar["low"]) / (rng + 1e-9)
                    body_size = abs(cur_bar["close"] - cur_bar["open"])
                    pass_velez = (close_location >= 0.55) and (body_size >= 0.6 * cur_bar["atr14"])

                # Expert 3: RSI Momentum Hook
                pass_rsi = True
                if "RSI" in active_experts:
                    pass_rsi = (prev_bar["rsi14"] < 35) and (cur_bar["rsi14"] > prev_bar["rsi14"])

                # Expert 4: Volume Absorption
                pass_vol = True
                if "VOL" in active_experts:
                    pass_vol = cur_bar["volume"] >= (1.15 * cur_bar["vol_ma20"])

                if not (pass_stage and pass_velez and pass_rsi and pass_vol):
                    continue

                entry_px = float(cur_bar["close"]) * (1.0 + SLIPPAGE_BPS / 10000.0)
                stop_px = float(cur_bar["lower_42s"])
                target_px = float(cur_bar["ma20"])
                stop_dist = entry_px - stop_px

                if stop_dist > (entry_px * 0.005):
                    t_risk = mtm * BASE_RISK_PCT
                    units = t_risk / stop_dist
                    open_positions.append({
                        "symbol": sym, "cluster": cluster, "entry_date": cur_date,
                        "entry_px": entry_px, "stop_px": stop_px, "target_px": target_px,
                        "units": units, "risk_usd": t_risk
                    })
                    occupied_clusters.add(cluster)
                    active_symbols.add(sym)

    s = pd.Series(nav_series)
    max_dd = abs(((s - s.cummax()) / s.cummax()).min()) * 100.0 if not s.empty else 0.0
    tot_ret = ((current_nav - INITIAL_NAV) / INITIAL_NAV) * 100.0

    r_vals = [t.r_multiple for t in closed_trades]
    wins = [r for r in r_vals if r > 0]
    losses = [abs(r) for r in r_vals if r < 0]
    wr = (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0
    mean_r = np.mean(r_vals) if r_vals else 0.0
    pf_str = "inf" if len(losses) == 0 and len(wins) > 0 else (f"{(sum(wins) / sum(losses)):.2f}" if losses else "0.00")

    return {
        "trades": len(closed_trades), "win_rate": wr, "mean_r": mean_r,
        "pf": pf_str, "return": tot_ret, "max_dd": max_dd
    }

def main():
    print("=" * 115)
    print(" 🧠 MULTI-EXPERT ENSEMBLE RETEST (14 Alpaca Pairs, 2021-2026)")
    print("=" * 115)

    data = load_data()

    experiments = [
        ("Baseline (-3.2σ Reclaim Only)", []),
        ("+ Stage Expert (Weinstein 200 SMA Slope)", ["STAGE"]),
        ("+ Velez Expert (Elephant/Range Location)", ["VELEZ"]),
        ("+ RSI Expert (Oversold Hook)", ["RSI"]),
        ("+ Volume Expert (1.15x Vol Expansion)", ["VOL"]),
        ("Combo: Velez + RSI", ["VELEZ", "RSI"]),
        ("Combo: Stage + Velez + RSI", ["STAGE", "VELEZ", "RSI"]),
        ("Full Ensemble (All 4 Experts Gated)", ["STAGE", "VELEZ", "RSI", "VOL"])
    ]

    rows = []
    for label, experts in experiments:
        m = simulate_ensemble(data, experts)
        rows.append({
            "Ensemble Configuration": label,
            "Trades": m["trades"],
            "Win Rate": f"{m['win_rate']:.1f}%",
            "Mean Expectancy": f"{m['mean_r']:+.3f}R",
            "Profit Factor": m["pf"],
            "Comp Return": f"{m['return']:+.2f}%",
            "Max Drawdown": f"{m['max_dd']:.2f}%"
        })

    print(pd.DataFrame(rows).to_string(index=False))
    print("=" * 115)

if __name__ == "__main__":
    main()
