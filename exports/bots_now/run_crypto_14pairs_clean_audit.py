#!/usr/bin/env python3
"""
Crypto Mean-Reversion: 14 Verified Alpaca Pairs Clean Audit (2021-2026)
Parameters: 20 SMA | -3.2s Reclaim | -4.2s Stop | 1-per-Cluster Firewall | Max 3 Pos
Assets: BTC, ETH, SOL, AVAX, DOT, UNI, AAVE, CRV, LINK, GRT, LDO, LTC, BCH, XRP
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

# Exact 14 Alpaca Tradable Pairs
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
class TradeRecord:
    symbol: str
    cluster: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_px: float
    exit_px: float
    stop_px: float
    target_px: float
    units: float
    risk_usd: float
    r_multiple: float
    net_pnl: float
    reason: str

def load_data(start_date="2020-01-01", end_date="2026-09-01") -> Dict[str, pd.DataFrame]:
    print(f"📥 Loading daily UTC bars for {len(CRYPTO_CLUSTERS)} verified Alpaca crypto pairs...")
    dataset = {}
    for sym in CRYPTO_CLUSTERS.keys():
        try:
            df = yf.download(sym, start=start_date, end=end_date, interval="1d", progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
            df = df.dropna(subset=["close", "high", "low", "open"])
            
            # Data Integrity Guard: Remove any corrupt ticks (e.g. UNI < $0.10)
            if sym == "UNI-USD":
                df = df[df["close"] > 0.50]

            if len(df) >= 30:
                df["ma20"] = df["close"].rolling(20).mean()
                df["std20"] = df["close"].rolling(20).std()
                df["lower_32s"] = df["ma20"] - (3.2 * df["std20"])
                df["lower_42s"] = df["ma20"] - (4.2 * df["std20"])
                dataset[sym] = df
        except Exception as e:
            print(f"Error on {sym}: {e}")
    return dataset

def simulate_window(data: Dict[str, pd.DataFrame], start_eval: str, end_eval: str) -> Tuple[List[TradeRecord], dict]:
    start_dt = pd.to_datetime(start_eval)
    end_dt = pd.to_datetime(end_eval)

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
            sym = pos["symbol"]
            df = data[sym]
            if cur_date not in df.index:
                retained.append(pos)
                continue

            bar = df.loc[cur_date]
            high_px = float(bar["high"])
            low_px = float(bar["low"])
            open_px = float(bar["open"])

            hit_stop = low_px <= pos["stop_px"]
            hit_target = high_px >= pos["target_px"]

            if hit_stop and hit_target:
                hit_target = False

            if hit_stop:
                exit_px = min(open_px, pos["stop_px"]) * (1.0 - (SLIPPAGE_BPS + GAP_PENALTY_BPS) / 10000.0)
                pnl = (exit_px - pos["entry_px"]) * pos["units"]
                r_mult = pnl / pos["risk_usd"]
                current_nav += pnl
                closed_trades.append(TradeRecord(
                    symbol=sym, cluster=pos["cluster"], entry_date=pos["entry_date"], exit_date=cur_date,
                    entry_px=pos["entry_px"], exit_px=exit_px, stop_px=pos["stop_px"],
                    target_px=pos["target_px"], units=pos["units"], risk_usd=pos["risk_usd"],
                    r_multiple=r_mult, net_pnl=pnl, reason="STOP"
                ))
            elif hit_target:
                exit_px = max(open_px, pos["target_price"]) * (1.0 - SLIPPAGE_BPS / 10000.0)
                pnl = (exit_px - pos["entry_px"]) * pos["units"]
                r_mult = pnl / pos["risk_usd"]
                current_nav += pnl
                closed_trades.append(TradeRecord(
                    symbol=sym, cluster=pos["cluster"], entry_date=pos["entry_date"], exit_date=cur_date,
                    entry_px=pos["entry_px"], exit_px=exit_px, stop_px=pos["stop_px"],
                    target_px=pos["target_px"], units=pos["units"], risk_usd=pos["risk_usd"],
                    r_multiple=r_mult, net_pnl=pnl, reason="TARGET"
                ))
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
                if idx < 22:
                    continue

                cur_bar = df.iloc[idx]
                prev_bar = df.iloc[idx - 1]

                if pd.isna(cur_bar["lower_32s"]):
                    continue

                # Reclaim condition: Prior close < -3.2s, Current close > -3.2s and < 20 SMA
                if (prev_bar["close"] < prev_bar["lower_32s"]) and (cur_bar["close"] > cur_bar["lower_32s"]) and (cur_bar["close"] < cur_bar["ma20"]):
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
                            "target_price": target_px, "units": units, "risk_usd": t_risk
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
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else 0.0
    net_usd = sum(t.net_pnl for t in closed_trades)

    metrics = {
        "trades": len(closed_trades),
        "win_rate": wr,
        "mean_r": mean_r,
        "pf": pf,
        "return": tot_ret,
        "max_dd": max_dd,
        "net_usd": net_usd
    }
    return closed_trades, metrics

def main():
    print("=" * 125)
    print(" 🛡️ 14 VERIFIED ALPACA PAIRS CLEAN AUDIT: 20 SMA / -3.2σ (2021-2026)")
    print("=" * 125)

    data = load_data()

    regimes = [
        ("Full Sample (5-Year Baseline)", "2021-09-01", "2026-08-28"),
        ("2020: COVID Crash & DeFi Summer", "2020-01-01", "2020-12-31"),
        ("2021: Retail Euphoria & Double Top", "2021-01-01", "2021-12-31"),
        ("2022: LUNA / FTX Liquidity Unwind", "2022-01-01", "2022-12-31"),
        ("2023: Structural Deleveraging & Recovery", "2023-01-01", "2023-12-31"),
        ("2024: Spot ETF Inflows & Halving Cycle", "2024-01-01", "2024-12-31"),
        ("2025-2026: Institutional Integration", "2025-01-01", "2026-08-28")
    ]

    rows = []
    for name, start_d, end_d in regimes:
        trades, m = simulate_window(data, start_d, end_d)
        rows.append({
            "Macro Regime": name,
            "Trades": m["trades"],
            "Win Rate": f"{m['win_rate']:.1f}%",
            "Mean Expectancy": f"{m['mean_r']:+.3f}R",
            "Profit Factor": f"{m['pf']:.2f}",
            "Period Return": f"{m['return']:+.2f}%",
            "Max Drawdown": f"{m['max_dd']:.2f}%",
            "Net PnL ($)": f"${m['net_usd']:+,.2f}"
        })

    print(pd.DataFrame(rows).to_string(index=False))
    print("=" * 125)

if __name__ == "__main__":
    main()
