#!/usr/bin/env python3
"""
Crypto Mean-Reversion: 14-Pair Focused Robustness Battery
Universe: Exact 14 Alpaca Tradable Pairs (BTC, ETH, SOL, AVAX, DOT, UNI, AAVE, CRV, LINK, GRT, LDO, LTC, BCH, XRP)
Tests:
1. Outlier Sensitivity: Drop Top 1, 3, 5 Winners
2. Friction Destruction: Baseline (15/25 bps) vs. 30/50, 50/100, 75/150 bps
3. Parameter Neighborhood Stability: SMA [18, 20, 22] x Sigma [3.0, 3.2, 3.4]
4. Macro Regime Defense: BTC > 200 SMA Gate (Overall + 2025-2026 Cohort)
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
DEFAULT_SLIP_BPS = 15.0
DEFAULT_GAP_BPS = 25.0

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
    dataset = {}
    for sym in CRYPTO_CLUSTERS.keys():
        try:
            df = yf.download(sym, start=start_date, end=end_date, interval="1d", progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
            df = df.dropna(subset=["close", "high", "low", "open"])
            if sym == "UNI-USD":
                df = df[df["close"] > 0.50]
            if len(df) >= 30:
                dataset[sym] = df
        except Exception:
            pass
    return dataset

def simulate(
    data: Dict[str, pd.DataFrame],
    sma_len: int = 20,
    entry_sig: float = 3.2,
    use_btc_macro: bool = False,
    slip_bps: float = DEFAULT_SLIP_BPS,
    gap_bps: float = DEFAULT_GAP_BPS,
    start_eval: str = "2021-09-01",
    end_eval: str = "2026-08-28",
    drop_top_n: int = 0
) -> Tuple[List[Trade], dict]:

    start_dt = pd.to_datetime(start_eval)
    end_dt = pd.to_datetime(end_eval)
    stop_sig = entry_sig + 1.0

    calc_data = {}
    for sym, df in data.items():
        d = df.copy()
        d["ma"] = d["close"].rolling(sma_len).mean()
        d["std"] = d["close"].rolling(sma_len).std()
        d["entry_band"] = d["ma"] - (entry_sig * d["std"])
        d["stop_band"] = d["ma"] - (stop_sig * d["std"])
        if sym == "BTC-USD":
            d["ma200"] = d["close"].rolling(200).mean()
        calc_data[sym] = d

    btc_df = calc_data.get("BTC-USD")
    all_dates = sorted(list(set.union(*[set(df.index) for df in calc_data.values()])))
    sim_dates = [d for d in all_dates if start_dt <= d <= end_dt]

    current_nav = INITIAL_NAV
    open_positions = []
    closed_trades = []
    nav_series = []

    for cur_date in sim_dates:
        btc_bullish = True
        if use_btc_macro and btc_df is not None and cur_date in btc_df.index:
            b_bar = btc_df.loc[cur_date]
            if "ma200" in b_bar and not pd.isna(b_bar["ma200"]):
                btc_bullish = float(b_bar["close"]) > float(b_bar["ma200"])

        retained = []
        for pos in open_positions:
            sym = pos["symbol"]
            df = calc_data[sym]
            if cur_date not in df.index:
                retained.append(pos)
                continue

            bar = df.loc[cur_date]
            hit_stop = float(bar["low"]) <= pos["stop_px"]
            hit_target = float(bar["high"]) >= pos["target_px"]

            if hit_stop and hit_target:
                hit_target = False

            if hit_stop:
                exit_px = min(float(bar["open"]), pos["stop_px"]) * (1.0 - (slip_bps + gap_bps) / 10000.0)
                pnl = (exit_px - pos["entry_px"]) * pos["units"]
                current_nav += pnl
                closed_trades.append(Trade(
                    symbol=sym, cluster=pos["cluster"], entry_date=pos["entry_date"], exit_date=cur_date,
                    entry_px=pos["entry_px"], exit_px=exit_px, stop_px=pos["stop_px"], target_px=pos["target_px"],
                    units=pos["units"], risk_usd=pos["risk_usd"], r_multiple=pnl / pos["risk_usd"], net_pnl=pnl, reason="STOP"
                ))
            elif hit_target:
                exit_px = max(float(bar["open"]), pos["target_px"]) * (1.0 - slip_bps / 10000.0)
                pnl = (exit_px - pos["entry_px"]) * pos["units"]
                current_nav += pnl
                closed_trades.append(Trade(
                    symbol=sym, cluster=pos["cluster"], entry_date=pos["entry_date"], exit_date=cur_date,
                    entry_px=pos["entry_px"], exit_px=exit_px, stop_px=pos["stop_px"], target_px=pos["target_px"],
                    units=pos["units"], risk_usd=pos["risk_usd"], r_multiple=pnl / pos["risk_usd"], net_pnl=pnl, reason="TARGET"
                ))
            else:
                retained.append(pos)
        open_positions = retained

        mtm = current_nav
        for pos in open_positions:
            df = calc_data[pos["symbol"]]
            if cur_date in df.index:
                mtm += (float(df.loc[cur_date]["close"]) - pos["entry_px"]) * pos["units"]
        nav_series.append(mtm)

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

                df = calc_data.get(sym)
                if df is None or cur_date not in df.index:
                    continue

                idx = df.index.get_loc(cur_date)
                if idx < sma_len + 2:
                    continue

                cur_bar = df.iloc[idx]
                prev_bar = df.iloc[idx - 1]

                if pd.isna(cur_bar["entry_band"]):
                    continue

                if (prev_bar["close"] < prev_bar["entry_band"]) and (cur_bar["close"] > cur_bar["entry_band"]) and (cur_bar["close"] < cur_bar["ma"]):
                    entry_px = float(cur_bar["close"]) * (1.0 + slip_bps / 10000.0)
                    stop_px = float(cur_bar["stop_band"])
                    target_px = float(cur_bar["ma"])
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

    if drop_top_n > 0 and closed_trades:
        closed_trades.sort(key=lambda t: t.net_pnl, reverse=True)
        closed_trades = closed_trades[drop_top_n:]

    s = pd.Series(nav_series)
    max_dd = abs(((s - s.cummax()) / s.cummax()).min()) * 100.0 if not s.empty else 0.0
    tot_ret = ((current_nav - INITIAL_NAV) / INITIAL_NAV) * 100.0

    r_vals = [t.r_multiple for t in closed_trades]
    wins = [r for r in r_vals if r > 0]
    losses = [abs(r) for r in r_vals if r < 0]

    wr = (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0
    mean_r = np.mean(r_vals) if r_vals else 0.0
    
    if len(losses) == 0:
        pf_str = "inf" if len(wins) > 0 else "0.00"
    else:
        pf_str = f"{(sum(wins) / sum(losses)):.2f}"

    net_usd = sum(t.net_pnl for t in closed_trades)

    return closed_trades, {
        "trades": len(closed_trades), "win_rate": wr, "mean_r": mean_r,
        "pf": pf_str, "return": tot_ret, "max_dd": max_dd, "net_usd": net_usd
    }

def main():
    print("=" * 115)
    print(" 🔬 14-PAIR CRYPTO MEAN-REVERSION: FOCUSED ROBUSTNESS & DESTRUCTION BATTERY")
    print("=" * 115)

    data = load_data()

    # 1. Outlier Sensitivity
    print("\n [TEST 1] OUTLIER SENSITIVITY: DROP TOP WINNERS (5-Year Baseline: 2021-2026)")
    print("-" * 115)
    outlier_rows = []
    for drop_n in [0, 1, 3, 5]:
        _, m = simulate(data, drop_top_n=drop_n)
        label = "Baseline (Full Sample)" if drop_n == 0 else f"Drop Top {drop_n} Winner(s)"
        outlier_rows.append({
            "Scenario": label, "Trades": m["trades"], "Win Rate": f"{m['win_rate']:.1f}%",
            "Mean R": f"{m['mean_r']:+.3f}R", "Profit Factor": m["pf"]
        })
    print(pd.DataFrame(outlier_rows).to_string(index=False))

    # 2. Friction Destruction
    print("\n [TEST 2] EXECUTION FRICTION DESTRUCTION")
    print("-" * 115)
    fric_scenarios = [
        ("Baseline (15 bps slip, 25 bps gap)", 15.0, 25.0),
        ("Moderate Stress (30 bps slip, 50 bps gap)", 30.0, 50.0),
        ("Severe Stress (50 bps slip, 100 bps gap)", 50.0, 100.0),
        ("Extreme Stress (75 bps slip, 150 bps gap)", 75.0, 150.0)
    ]
    fric_rows = []
    for label, slip, gap in fric_scenarios:
        _, m = simulate(data, slip_bps=slip, gap_bps=gap)
        fric_rows.append({
            "Friction Scenario": label, "Trades": m["trades"], "Win Rate": f"{m['win_rate']:.1f}%",
            "Mean R": f"{m['mean_r']:+.3f}R", "PF": m["pf"], "Comp Return": f"{m['return']:+.2f}%", "MaxDD": f"{m['max_dd']:.2f}%"
        })
    print(pd.DataFrame(fric_rows).to_string(index=False))

    # 3. Parameter Neighborhood Stability
    print("\n [TEST 3] PARAMETER NEIGHBORHOOD STABILITY (SMA: 18, 20, 22 | Sigma: 3.0, 3.2, 3.4)")
    print("-" * 115)
    nbr_rows = []
    for sma in [18, 20, 22]:
        for sig in [3.0, 3.2, 3.4]:
            _, m = simulate(data, sma_len=sma, entry_sig=sig)
            is_base = " (Baseline)" if sma == 20 and sig == 3.2 else ""
            nbr_rows.append({
                "Configuration": f"SMA {sma} x {sig:.1f}σ{is_base}", "Trades": m["trades"],
                "Win Rate": f"{m['win_rate']:.1f}%", "Mean R": f"{m['mean_r']:+.3f}R",
                "PF": m["pf"], "Comp Return": f"{m['return']:+.2f}%", "MaxDD": f"{m['max_dd']:.2f}%"
            })
    print(pd.DataFrame(nbr_rows).to_string(index=False))

    # 4. Macro Regime Defense: BTC > 200 SMA Gate
    print("\n [TEST 4] MACRO REGIME DEFENSE: UNFILTERED VS. BTC > 200 SMA GATE")
    print("-" * 115)
    gate_rows = []
    for gate_label, gate_val in [("Unfiltered (No Gate)", False), ("BTC > 200 SMA Gate", True)]:
        _, m_all = simulate(data, use_btc_macro=gate_val, start_eval="2021-09-01", end_eval="2026-08-28")
        _, m_recent = simulate(data, use_btc_macro=gate_val, start_eval="2025-01-01", end_eval="2026-08-28")
        gate_rows.append({
            "Macro Architecture": gate_label,
            "5Y Trades": m_all["trades"], "5Y Win Rate": f"{m_all['win_rate']:.1f}%",
            "5Y Mean R": f"{m_all['mean_r']:+.3f}R", "5Y PF": m_all["pf"],
            "25-26 Trades": m_recent["trades"], "25-26 Win Rate": f"{m_recent['win_rate']:.1f}%",
            "25-26 Mean R": f"{m_recent['mean_r']:+.3f}R", "25-26 PF": m_recent["pf"]
        })
    print(pd.DataFrame(gate_rows).to_string(index=False))
    print("=" * 115)

if __name__ == "__main__":
    main()
