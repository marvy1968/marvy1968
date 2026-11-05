#!/usr/bin/env python3
"""
Crypto Elite Mean-Reversion: 5-Year Hardened Destruction & Macro Regime Battery (2021-2026)
Architecture:
- Asset Class: 24/7 Continuous Crypto (00:00 UTC Daily Closes)
- Direction: Long-Only Mean Reversion
- Baseline: 50 SMA Centerline, -2.5-Sigma Reclaim Entry
- Stop / Target: Point-in-time frozen -3.5-Sigma hard stop, 50 SMA target
- Sizing: Dynamic 0.50% NAV risk per trade, Fractional sizing
- Risk Governance: Max 1.50% Gross Open Risk (Max 3 concurrent), Cluster Firewall (Max 1/Cluster)
- Data Ingestion: Yahoo Finance / Alpaca Crypto Daily UTC Bars
"""

import sys
import math
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import numpy as np
import pandas as pd
import yfinance as yf

INITIAL_NAV = 50000.0
BASE_RISK_PCT = 0.0050      # 0.50% NAV risk
GROSS_RISK_CAP_PCT = 0.0150 # 1.50% Gross Risk Cap
MAX_CONCURRENT_POSITIONS = 3
DEFAULT_SLIPPAGE_BPS = 15.0 # 15 bps taker fee + slip baseline
DEFAULT_GAP_PENALTY_BPS = 25.0

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

@dataclass
class Trade:
    symbol: str
    cluster: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    units: float
    risk_usd: float
    r_multiple: float
    net_pnl: float
    exit_reason: str

def fetch_crypto_data(start_date: str = "2020-01-01", end_date: str = "2026-09-01") -> Dict[str, pd.DataFrame]:
    print(f"📥 Ingesting daily UTC bars for {len(CRYPTO_CLUSTERS)} crypto pairs ({start_date} to {end_date})...")
    data_dict = {}
    for sym in CRYPTO_CLUSTERS.keys():
        try:
            df = yf.download(sym, start=start_date, end=end_date, interval="1d", progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
            df = df.dropna(subset=["close", "high", "low", "open"])
            if len(df) >= 60:
                df["ma50"] = df["close"].rolling(50).mean()
                df["std50"] = df["close"].rolling(50).std()
                df["lower_25s"] = df["ma50"] - (2.5 * df["std50"])
                df["lower_35s"] = df["ma50"] - (3.5 * df["std50"])
                data_dict[sym] = df
        except Exception as e:
            print(f"⚠️ Warning: Failed to load {sym}: {e}")
    return data_dict

def run_simulation(
    data_dict: Dict[str, pd.DataFrame],
    max_per_cluster: int = 1,
    max_total_pos: int = 3,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    gap_penalty_bps: float = DEFAULT_GAP_PENALTY_BPS,
    start_eval_date: str = "2021-09-01",
    end_eval_date: str = "2026-08-28",
    drop_top_n_winners: int = 0
) -> Tuple[List[Trade], pd.DataFrame, dict]:

    start_dt = pd.to_datetime(start_eval_date)
    end_dt = pd.to_datetime(end_eval_date)

    # Align unique trading dates
    all_dates = sorted(list(set.union(*[set(df.index) for df in data_dict.values()])))
    sim_dates = [d for d in all_dates if start_dt <= d <= end_dt]

    current_nav = INITIAL_NAV
    open_positions = []
    closed_trades = []
    nav_history = []

    for cur_date in sim_dates:
        # 1. Evaluate Open Positions against Intraday High/Low
        retained_positions = []
        for pos in open_positions:
            sym = pos["symbol"]
            df = data_dict[sym]
            if cur_date not in df.index:
                retained_positions.append(pos)
                continue

            bar = df.loc[cur_date]
            high_px = float(bar["high"])
            low_px = float(bar["low"])
            open_px = float(bar["open"])

            hit_stop = low_px <= pos["stop_price"]
            hit_target = high_px >= pos["target_price"]

            if hit_stop and hit_target:
                # Conservative tie-breaker: Stop hits first
                hit_target = False

            if hit_stop:
                # Slippage + Gap penalty on stopout
                exit_px = min(open_px, pos["stop_price"]) * (1.0 - (slippage_bps + gap_penalty_bps) / 10000.0)
                pnl = (exit_px - pos["entry_price"]) * pos["units"]
                r_mult = pnl / pos["risk_usd"]
                current_nav += pnl
                closed_trades.append(Trade(
                    symbol=sym, cluster=pos["cluster"], entry_date=pos["entry_date"], exit_date=cur_date,
                    entry_price=pos["entry_price"], exit_price=exit_px, stop_price=pos["stop_price"],
                    target_price=pos["target_price"], units=pos["units"], risk_usd=pos["risk_usd"],
                    r_multiple=r_mult, net_pnl=pnl, exit_reason="STOP"
                ))
            elif hit_target:
                exit_px = max(open_px, pos["target_price"]) * (1.0 - slippage_bps / 10000.0)
                pnl = (exit_px - pos["entry_price"]) * pos["units"]
                r_mult = pnl / pos["risk_usd"]
                current_nav += pnl
                closed_trades.append(Trade(
                    symbol=sym, cluster=pos["cluster"], entry_date=pos["entry_date"], exit_date=cur_date,
                    entry_price=pos["entry_price"], exit_price=exit_px, stop_price=pos["stop_price"],
                    target_price=pos["target_price"], units=pos["units"], risk_usd=pos["risk_usd"],
                    r_multiple=r_mult, net_pnl=pnl, exit_reason="TARGET"
                ))
            else:
                retained_positions.append(pos)

        open_positions = retained_positions

        # 2. Mark-to-Market Portfolio NAV
        mtm_nav = current_nav
        for pos in open_positions:
            df = data_dict[pos["symbol"]]
            if cur_date in df.index:
                c_px = float(df.loc[cur_date]["close"])
                mtm_nav += (c_px - pos["entry_price"]) * pos["units"]
        nav_history.append({"date": cur_date, "nav": mtm_nav})

        # 3. New Signal Scan & Reclaim Entry Execution
        if len(open_positions) < max_total_pos:
            occupied_clusters = set(p["cluster"] for p in open_positions)
            active_symbols = set(p["symbol"] for p in open_positions)

            for sym, cluster in CRYPTO_CLUSTERS.items():
                if len(open_positions) >= max_total_pos:
                    break
                if sym in active_symbols:
                    continue
                if max_per_cluster > 0 and cluster in occupied_clusters:
                    continue

                df = data_dict.get(sym)
                if df is None or cur_date not in df.index:
                    continue

                idx_loc = df.index.get_loc(cur_date)
                if idx_loc < 52:
                    continue

                cur_bar = df.iloc[idx_loc]
                prev_bar = df.iloc[idx_loc - 1]

                # Reclaim condition: Prior close < -2.5 sigma, Current close > -2.5 sigma and < 50 SMA
                if (prev_bar["close"] < prev_bar["lower_25s"]) and (cur_bar["close"] > cur_bar["lower_25s"]) and (cur_bar["close"] < cur_bar["ma50"]):
                    entry_px = float(cur_bar["close"]) * (1.0 + slippage_bps / 10000.0)
                    stop_px = float(cur_bar["lower_35s"])
                    target_px = float(cur_bar["ma50"])
                    stop_dist = entry_px - stop_px

                    if stop_dist > (entry_px * 0.005): # Minimum 50 bps risk distance
                        target_risk = mtm_nav * BASE_RISK_PCT
                        units = target_risk / stop_dist

                        open_positions.append({
                            "symbol": sym,
                            "cluster": cluster,
                            "entry_date": cur_date,
                            "entry_price": entry_px,
                            "stop_price": stop_px,
                            "target_price": target_px,
                            "units": units,
                            "risk_usd": target_risk
                        })
                        occupied_clusters.add(cluster)
                        active_symbols.add(sym)

    if drop_top_n_winners > 0 and closed_trades:
        closed_trades.sort(key=lambda t: t.net_pnl, reverse=True)
        closed_trades = closed_trades[drop_top_n_winners:]

    # Calculate Performance Analytics
    df_nav = pd.DataFrame(nav_history)
    if not df_nav.empty:
        df_nav["peak"] = df_nav["nav"].cummax()
        df_nav["dd"] = (df_nav["nav"] - df_nav["peak"]) / df_nav["peak"]
        max_dd = abs(df_nav["dd"].min()) * 100.0
        tot_return = ((df_nav["nav"].iloc[-1] - INITIAL_NAV) / INITIAL_NAV) * 100.0
    else:
        max_dd, tot_return = 0.0, 0.0

    r_multiples = [t.r_multiple for t in closed_trades]
    wins = [r for r in r_multiples if r > 0]
    losses = [abs(r) for r in r_multiples if r < 0]

    wr = (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0
    mean_r = np.mean(r_multiples) if r_multiples else 0.0
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else 0.0

    metrics = {
        "trades": len(closed_trades),
        "win_rate": wr,
        "mean_r": mean_r,
        "profit_factor": pf,
        "total_return": tot_return,
        "max_dd": max_dd
    }
    return closed_trades, df_nav, metrics

def main():
    print("=" * 115)
    print(" 🛡️ HARDENED CRYPTO MEAN-REVERSION DESTRUCTION & REGIME AUDIT (2021-2026)")
    print("=" * 115)

    data = fetch_crypto_data(start_date="2020-01-01", end_date="2026-09-01")

    # Baseline 5-Year Execution
    trades_base, nav_base, m_base = run_simulation(data, max_per_cluster=1, max_total_pos=3)

    print("\n [PARITY GATE] 5-YEAR BASELINE METRICS (2021-09-01 to 2026-08-28)")
    print("-" * 115)
    print(f" Executed Trades: {m_base['trades']} | Win Rate: {m_base['win_rate']:.1f}% | Expectancy: {m_base['mean_r']:+.3f}R")
    print(f" Profit Factor:   {m_base['profit_factor']:.2f}  | Comp Return: {m_base['total_return']:+.2f}% | True MaxDD: {m_base['max_dd']:.2f}%")
    print("-" * 115)

    # 1. Cluster Firewall Ablation
    print("\n [TEST 1] CLUSTER FIREWALL ABLATION EXPERIMENT")
    print("=" * 115)
    ablations = [
        ("Max 1 / Cluster (Firewall)", 1, 3),
        ("Max 2 / Cluster", 2, 3),
        ("Unrestricted (Max 3 / Cluster)", 0, 3)
    ]
    ab_rows = []
    for label, m_cluster, m_tot in ablations:
        _, _, m = run_simulation(data, max_per_cluster=m_cluster, max_total_pos=m_tot)
        ret_dd = (m['total_return'] / m['max_dd']) if m['max_dd'] > 0 else 0.0
        ab_rows.append({
            "Configuration": label, "Trades": m['trades'], "Win Rate": f"{m['win_rate']:.1f}%",
            "Mean R": f"{m['mean_r']:+.3f}R", "PF": f"{m['profit_factor']:.2f}",
            "Comp Return": f"{m['total_return']:+.2f}%", "True MaxDD": f"{m['max_dd']:.2f}%",
            "Return / MaxDD": f"{ret_dd:.2f}"
        })
    print(pd.DataFrame(ab_rows).to_string(index=False))

    # 2. Top Winner Outlier Removal
    print("\n [TEST 2] OUTLIER ROBUSTNESS: TOP WINNER REMOVAL")
    print("=" * 115)
    outlier_rows = []
    for drop_n in [0, 1, 3, 5]:
        _, _, m = run_simulation(data, drop_top_n_winners=drop_n)
        label = "Baseline (Full Sample)" if drop_n == 0 else f"Drop Top {drop_n} Winner(s)"
        outlier_rows.append({
            "Scenario": label, "Remaining Trades": m['trades'], "Win Rate": f"{m['win_rate']:.1f}%",
            "Mean R": f"{m['mean_r']:+.3f}R", "Profit Factor": f"{m['profit_factor']:.2f}"
        })
    print(pd.DataFrame(outlier_rows).to_string(index=False))

    # 3. Friction & Adverse Slip Stress
    print("\n [TEST 3] EXECUTION FRICTION & ADVERSE GAP DESTRUCTION")
    print("=" * 115)
    friction_scenarios = [
        ("Baseline (15 bps fee/slip, 25 bps gap)", 15.0, 25.0),
        ("2x Severe (30 bps fee/slip, 50 bps gap)", 30.0, 50.0),
        ("3x Extreme (50 bps fee/slip, 100 bps gap)", 50.0, 100.0),
        ("Institutional Stress (75 bps fee/slip, 150 bps gap)", 75.0, 150.0)
    ]
    fric_rows = []
    for label, slip, gap in friction_scenarios:
        _, _, m = run_simulation(data, slippage_bps=slip, gap_penalty_bps=gap)
        fric_rows.append({
            "Friction Scenario": label, "Trades": m['trades'], "Win Rate": f"{m['win_rate']:.1f}%",
            "Mean R": f"{m['mean_r']:+.3f}R", "PF": f"{m['profit_factor']:.2f}",
            "Comp Return": f"{m['total_return']:+.2f}%", "True MaxDD": f"{m['max_dd']:.2f}%"
        })
    print(pd.DataFrame(fric_rows).to_string(index=False))

    # 4. Crypto Macro Regime Attribution (2020-2026)
    print("\n [TEST 4] CRYPTO ENTRY-COHORT MACRO REGIME ATTRIBUTION (2020-2026)")
    print("=" * 115)
    regimes = [
        ("2020 (COVID Liquidity Shock & DeFi Summer)", "2020-01-01", "2020-12-31"),
        ("2021 (Retail Euphoria & Double Top)", "2021-01-01", "2021-12-31"),
        ("2022 (LUNA/FTX Liquidity Unwind & Bear Market)", "2022-01-01", "2022-12-31"),
        ("2023 (Deleveraging & Early Recovery)", "2023-01-01", "2023-12-31"),
        ("2024 (Spot ETF Approvals & Halving Cycle)", "2024-01-01", "2024-12-31"),
        ("2025-2026 (Institutional Integration & Late Cycle)", "2025-01-01", "2026-08-28")
    ]
    regime_rows = []
    for r_name, r_start, r_end in regimes:
        r_trades, _, r_m = run_simulation(data, start_eval_date=r_start, end_eval_date=r_end)
        net_usd = sum(t.net_pnl for t in r_trades)
        regime_rows.append({
            "Regime Window": r_name,
            "Trades": r_m['trades'],
            "Win Rate": f"{r_m['win_rate']:.1f}%",
            "Mean Expectancy": f"{r_m['mean_r']:+.3f}R",
            "Profit Factor": f"{r_m['profit_factor']:.2f}",
            "Net PnL ($)": f"${net_usd:+,.2f}"
        })
    print(pd.DataFrame(regime_rows).to_string(index=False))
    print("=" * 115)

if __name__ == "__main__":
    main()
