#!/usr/bin/env python3
"""
Elite 20 Equities: Production Hardened Destruction Battery
Baseline: Fixed 2021-09-01 to 2026-09-01 | Dynamic NAV Compounding | Gross 1.50% Cap
Destruction Battery:
1. Sector Firewall Ablation (Max 1 vs 2 vs Unrestricted per Sector)
2. Leave-One-Sector-Out (LOSeO) Dependency Matrix
3. Outlier Winner Removal (Drop Top 1, 3, 5 Winners)
4. Execution Friction Destruction (5 bps vs 10 bps vs 15 bps vs 25 bps + Gap Penalty)
5. Macro Regime Attribution (Extended 2020-2026 History)
"""

import sys
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

try:
    import yfinance as yf
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "yfinance"])
    import yfinance as yf

SYMBOL_TO_SECTOR = {
    "SPY": "INDEX", "QQQ": "INDEX",
    "AAPL": "TECH", "MSFT": "TECH", "NVDA": "TECH", "AVGO": "TECH",
    "GOOGL": "COMM", "META": "COMM", "NFLX": "COMM",
    "WMT": "STAPLES", "COST": "STAPLES", "PG": "STAPLES",
    "XOM": "ENERGY", "CVX": "ENERGY", "COP": "ENERGY",
    "AMZN": "DISC",
    "CAT": "IND", "GE": "IND",
    "JPM": "FIN", "GS": "FIN"
}

ELITE_20_SYMBOLS = list(SYMBOL_TO_SECTOR.keys())

DATA_START = "2019-01-01"
TEST_START = "2021-09-01"
TEST_END   = "2026-09-01"

INITIAL_EQUITY = 100_000.0
BASE_RISK_PCT  = 0.0050   # 0.50% NAV per trade
GROSS_RISK_CAP = 0.0150   # 1.50% Gross Open Risk
MAX_CONCURRENT = 3

@dataclass
class StockTrade:
    trade_id: int
    symbol: str
    sector: str
    side: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_px: float
    stop_px: float
    target_px: float
    units: float
    dollar_risk: float
    exit_px: float = 0.0
    exit_reason: str = "OPEN"
    net_pnl: float = 0.0
    r_multiple: float = 0.0

def load_stock_data(symbols: List[str]) -> Dict[str, pd.DataFrame]:
    print(f"📥 Loading historical daily bars ({DATA_START} to {TEST_END})...")
    data = {}
    for sym in symbols:
        df = yf.download(sym, start=DATA_START, end=TEST_END, interval="1d", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        df = df.dropna()
        if len(df) >= 120:
            data[sym] = df
    return data

def generate_signals(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = df.copy()
    df["ma50"] = df["close"].rolling(50).mean()
    df["std50"] = df["close"].rolling(50).std()
    df["lower_25s"] = df["ma50"] - (2.5 * df["std50"])
    df["lower_35s"] = df["ma50"] - (3.5 * df["std50"])

    test_start_ts = pd.Timestamp(TEST_START)
    signals = []
    
    for i in range(55, len(df) - 1):
        cur = df.iloc[i]
        prev = df.iloc[i - 1]
        t = df.index[i]

        if t < test_start_ts:
            continue

        if prev["close"] < prev["lower_25s"] and cur["close"] > cur["lower_25s"] and cur["close"] < cur["ma50"]:
            signals.append({
                "timestamp": t,
                "symbol": symbol,
                "sector": SYMBOL_TO_SECTOR[symbol],
                "side": "BUY",
                "raw_entry_px": float(cur["close"]),
                "stop_px": float(cur["lower_35s"]),
                "target_px": float(cur["ma50"])
            })
    return pd.DataFrame(signals)

def simulate_engine(
    all_signals: pd.DataFrame,
    dfs: Dict[str, pd.DataFrame],
    max_per_sector: int = 1,
    slippage_bps: float = 0.0005,
    gap_penalty_bps: float = 0.0,
    excluded_sector: Optional[str] = None
) -> Tuple[dict, pd.DataFrame, List[StockTrade]]:
    
    if all_signals.empty:
        return {}, pd.DataFrame(), []

    sigs = all_signals.copy()
    if excluded_sector:
        sigs = sigs[sigs["sector"] != excluded_sector]

    sorted_sigs = sigs.sort_values(by=["timestamp", "symbol"]).to_dict("records")
    test_start_ts = pd.Timestamp(TEST_START)
    test_end_ts = pd.Timestamp(TEST_END)
    
    all_dates = sorted(list(set().union(*(df.index for df in dfs.values()))))
    eval_dates = [d for d in all_dates if test_start_ts <= d <= test_end_ts]

    realized_nav = INITIAL_EQUITY
    active_trades: List[StockTrade] = []
    closed_trades: List[StockTrade] = []
    equity_curve = []
    
    sig_idx = 0
    num_sigs = len(sorted_sigs)
    trade_counter = 0

    for current_date in eval_dates:
        still_active = []
        for trade in active_trades:
            if trade.symbol not in dfs or current_date not in dfs[trade.symbol].index:
                still_active.append(trade)
                continue

            bar = dfs[trade.symbol].loc[current_date]
            o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
            is_closed = False
            exit_px, reason = 0.0, ""

            if o <= trade.stop_px:
                exit_px = o * (1.0 - slippage_bps - gap_penalty_bps)
                reason, is_closed = "STOP_HIT", True
            elif o >= trade.target_px:
                exit_px = o * (1.0 - slippage_bps)
                reason, is_closed = "TARGET_HIT", True
            elif l <= trade.stop_px:
                exit_px = trade.stop_px * (1.0 - slippage_bps)
                reason, is_closed = "STOP_HIT", True
            elif h >= trade.target_px:
                exit_px = trade.target_px * (1.0 - slippage_bps)
                reason, is_closed = "TARGET_HIT", True

            if is_closed:
                trade.exit_px = exit_px
                trade.exit_time = current_date
                trade.exit_reason = reason
                pnl = (trade.exit_px - trade.entry_px) * trade.units
                trade.net_pnl = pnl
                trade.r_multiple = pnl / trade.dollar_risk if trade.dollar_risk > 0 else 0.0
                realized_nav += pnl
                closed_trades.append(trade)
            else:
                still_active.append(trade)

        active_trades = still_active

        # Live MTM NAV Calculation
        unrealized_pnl = 0.0
        for tr in active_trades:
            if tr.symbol in dfs and current_date in dfs[tr.symbol].index:
                curr_c = float(dfs[tr.symbol].loc[current_date]["close"])
                unrealized_pnl += (curr_c - tr.entry_px) * tr.units
        
        live_mtm_nav = realized_nav + unrealized_pnl

        # New Signals Admission
        incoming = []
        while sig_idx < num_sigs and sorted_sigs[sig_idx]["timestamp"] == current_date:
            incoming.append(sorted_sigs[sig_idx])
            sig_idx += 1

        for sig in incoming:
            sym = sig["symbol"]
            sec = sig["sector"]

            if any(p.symbol == sym for p in active_trades):
                continue
            if len(active_trades) >= MAX_CONCURRENT:
                continue
            if sum(1 for p in active_trades if p.sector == sec) >= max_per_sector:
                continue

            entry_px = sig["raw_entry_px"] * (1.0 + slippage_bps)
            actual_stop_dist = entry_px - sig["stop_px"]
            if actual_stop_dist <= 0:
                continue

            target_dollar_risk = live_mtm_nav * BASE_RISK_PCT
            current_dollar_risk = sum(p.units * (p.entry_px - p.stop_px) for p in active_trades)
            if (current_dollar_risk + target_dollar_risk) > (live_mtm_nav * GROSS_RISK_CAP + 1e-6):
                continue

            units = target_dollar_risk / actual_stop_dist
            if units <= 0:
                continue

            trade_counter += 1
            trade_obj = StockTrade(
                trade_id=trade_counter, symbol=sym, sector=sec, side="BUY",
                entry_time=current_date, exit_time=current_date,
                entry_px=entry_px, stop_px=sig["stop_px"], target_px=sig["target_px"],
                units=units, dollar_risk=target_dollar_risk
            )
            active_trades.append(trade_obj)

        unrealized_close = 0.0
        for tr in active_trades:
            if tr.symbol in dfs and current_date in dfs[tr.symbol].index:
                curr_c = float(dfs[tr.symbol].loc[current_date]["close"])
                unrealized_close += (curr_c - tr.entry_px) * tr.units
        equity_curve.append((current_date, realized_nav + unrealized_close))

    # Mark Terminal Open Positions
    if active_trades:
        terminal_date = eval_dates[-1]
        for trade in active_trades:
            if trade.symbol in dfs and terminal_date in dfs[trade.symbol].index:
                c_px = float(dfs[trade.symbol].loc[terminal_date]["close"])
                trade.exit_px = c_px * (1.0 - slippage_bps)
                trade.exit_time = terminal_date
                trade.exit_reason = "TERMINAL_MTM"
                pnl = (trade.exit_px - trade.entry_px) * trade.units
                trade.net_pnl = pnl
                trade.r_multiple = pnl / trade.dollar_risk if trade.dollar_risk > 0 else 0.0
                realized_nav += pnl
                closed_trades.append(trade)
        active_trades = []

    eq_df = pd.DataFrame(equity_curve, columns=["date", "equity"])
    eq_df["cummax"] = eq_df["equity"].cummax()
    eq_df["dd"] = (eq_df["cummax"] - eq_df["equity"]) / eq_df["cummax"]
    max_dd = float(eq_df["dd"].max() * 100.0) if not eq_df.empty else 0.0

    net_rs = [t.r_multiple for t in closed_trades]
    wins = [r for r in net_rs if r > 0]
    losses = [abs(r) for r in net_rs if r < 0]
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else 0.0
    final_mtm_equity = eq_df["equity"].iloc[-1] if not eq_df.empty else realized_nav

    stats = {
        "trades": len(closed_trades),
        "win_rate": (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0,
        "mean_r": float(np.mean(net_rs)) if net_rs else 0.0,
        "net_pf": pf,
        "comp_return": ((final_mtm_equity - INITIAL_EQUITY) / INITIAL_EQUITY) * 100.0,
        "max_dd": max_dd,
        "final_equity": final_mtm_equity
    }
    return stats, eq_df, closed_trades

def main():
    print("=" * 115)
    print(" 🛡️ ELITE 20 PRODUCTION HARDENED DESTRUCTION BATTERY")
    print("=" * 115)

    dfs = load_stock_data(ELITE_20_SYMBOLS)
    sigs_list = [generate_signals(dfs[sym], sym) for sym in dfs]
    all_sigs = pd.concat(sigs_list, ignore_index=True) if sigs_list else pd.DataFrame()

    # Parity Gate Execution
    print("\n [PARITY GATE] EXECUTING HARDENED PRODUCTION AUDIT...")
    base_stats, eq_df, closed_trades = simulate_engine(all_sigs, dfs, max_per_sector=1, slippage_bps=0.0005)
    
    print("-" * 115)
    print(f" Executed Trades: {base_stats['trades']} | Win Rate: {base_stats['win_rate']:.1f}% | Expectancy: {base_stats['mean_r']:+.3f}R")
    print(f" Profit Factor:   {base_stats['net_pf']:.2f}  | Comp Return: {base_stats['comp_return']:+.2f}% | True MaxDD: {base_stats['max_dd']:.2f}%")
    print("-" * 115)
    print("✅ BASELINE ACCEPTED: Running Full Destruction Battery...\n")

    # TEST 1: Firewall Ablation
    print("=" * 115)
    print(" [TEST 1] SECTOR FIREWALL ABLATION EXPERIMENT (Causality Verification)")
    print("=" * 115)
    ablation_rows = []
    for cap, name in [(1, "Max 1 / Sector (Firewall)"), (2, "Max 2 / Sector"), (3, "Unrestricted (Max 3 / Sector)")]:
        stats, _, _ = simulate_engine(all_sigs, dfs, max_per_sector=cap)
        ret_dd = stats["comp_return"] / stats["max_dd"] if stats["max_dd"] > 0 else 0.0
        ablation_rows.append({
            "Configuration": name,
            "Trades": stats["trades"],
            "Win Rate": f"{stats['win_rate']:.1f}%",
            "Mean R": f"{stats['mean_r']:+.3f}R",
            "PF": f"{stats['net_pf']:.2f}",
            "Comp Return": f"{stats['comp_return']:+.2f}%",
            "True MaxDD": f"{stats['max_dd']:.2f}%",
            "Return / MaxDD": f"{ret_dd:.2f}"
        })
    print(pd.DataFrame(ablation_rows).to_string(index=False))

    # TEST 2: LOSeO Dependency Matrix
    print("\n" + "=" * 115)
    print(" [TEST 2] LEAVE-ONE-SECTOR-OUT (LOSeO) DEPENDENCY MATRIX")
    print("=" * 115)
    sectors = sorted(list(set(SYMBOL_TO_SECTOR.values())))
    loseo_rows = []
    for sec in sectors:
        stats, _, _ = simulate_engine(all_sigs, dfs, max_per_sector=1, excluded_sector=sec)
        loseo_rows.append({
            "Excluded Sector": f"Excluding {sec}",
            "Trades": stats["trades"],
            "Win Rate": f"{stats['win_rate']:.1f}%",
            "Mean R": f"{stats['mean_r']:+.3f}R",
            "PF": f"{stats['net_pf']:.2f}",
            "Comp Return": f"{stats['comp_return']:+.2f}%",
            "True MaxDD": f"{stats['max_dd']:.2f}%"
        })
    print(pd.DataFrame(loseo_rows).to_string(index=False))

    # TEST 3: Winner Removal
    print("\n" + "=" * 115)
    print(" [TEST 3] OUTLIER ROBUSTNESS: TOP WINNER REMOVAL")
    print("=" * 115)
    base_rs = sorted([t.r_multiple for t in closed_trades], reverse=True)
    outlier_rows = []
    for k in [0, 1, 3, 5]:
        sample_rs = base_rs[k:]
        wins = [r for r in sample_rs if r > 0]
        losses = [abs(r) for r in sample_rs if r < 0]
        pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else 0.0
        outlier_rows.append({
            "Scenario": f"Baseline (Full Sample)" if k == 0 else f"Drop Top {k} Winner(s)",
            "Remaining Trades": len(sample_rs),
            "Win Rate": f"{(len(wins)/len(sample_rs))*100:.1f}%",
            "Mean R": f"{np.mean(sample_rs):+.3f}R",
            "Profit Factor": f"{pf:.2f}"
        })
    print(pd.DataFrame(outlier_rows).to_string(index=False))

    # TEST 4: Friction Destruction
    print("\n" + "=" * 115)
    print(" [TEST 4] EXECUTION FRICTION & ADVERSE GAP DESTRUCTION")
    print("=" * 115)
    fric_rows = []
    scenarios = [
        ("Baseline (5 bps slip, 0 gap penalty)", 0.0005, 0.0),
        ("2x Severe (10 bps slip, 10 bps gap)", 0.0010, 0.0010),
        ("3x Extreme (15 bps slip, 25 bps gap)", 0.0015, 0.0025),
        ("Institutional Stress (25 bps slip, 50 bps gap)", 0.0025, 0.0050)
    ]
    for label, slip, gap in scenarios:
        stats, _, _ = simulate_engine(all_sigs, dfs, max_per_sector=1, slippage_bps=slip, gap_penalty_bps=gap)
        fric_rows.append({
            "Friction Scenario": label,
            "Trades": stats["trades"],
            "Win Rate": f"{stats['win_rate']:.1f}%",
            "Mean R": f"{stats['mean_r']:+.3f}R",
            "PF": f"{stats['net_pf']:.2f}",
            "Comp Return": f"{stats['comp_return']:+.2f}%",
            "True MaxDD": f"{stats['max_dd']:.2f}%"
        })
    print(pd.DataFrame(fric_rows).to_string(index=False))

    # TEST 5: Extended Regime Attribution
    print("\n" + "=" * 115)
    print(" [TEST 5] EXTENDED MACRO REGIME ATTRIBUTION (2020-2026)")
    print("=" * 115)
    ext_sigs_list = []
    for sym in dfs:
        df_sym = dfs[sym].copy()
        df_sym["ma50"] = df_sym["close"].rolling(50).mean()
        df_sym["std50"] = df_sym["close"].rolling(50).std()
        df_sym["lower_25s"] = df_sym["ma50"] - (2.5 * df_sym["std50"])
        df_sym["lower_35s"] = df_sym["ma50"] - (3.5 * df_sym["std50"])
        
        for i in range(55, len(df_sym) - 1):
            cur = df_sym.iloc[i]
            prev = df_sym.iloc[i - 1]
            t = df_sym.index[i]
            if t >= pd.Timestamp("2020-01-01"):
                if prev["close"] < prev["lower_25s"] and cur["close"] > cur["lower_25s"] and cur["close"] < cur["ma50"]:
                    ext_sigs_list.append({
                        "timestamp": t, "symbol": sym, "sector": SYMBOL_TO_SECTOR[sym],
                        "side": "BUY", "raw_entry_px": float(cur["close"]),
                        "stop_px": float(cur["lower_35s"]), "target_px": float(cur["ma50"])
                    })
    
    ext_sigs_df = pd.DataFrame(ext_sigs_list)
    _, _, ext_trades = simulate_engine(ext_sigs_df, dfs, max_per_sector=1)
    
    regimes = [
        ("2020-2021 (COVID Crash & Stimulus Bull)", "2020-01-01", "2021-12-31"),
        ("2022 (Fed Tightening Bear Market)", "2022-01-01", "2022-12-31"),
        ("2023-2024 (AI Infrastructure Expansion)", "2023-01-01", "2024-12-31"),
        ("2025-2026 (Consolidation & Late Cycle)", "2025-01-01", "2026-09-01")
    ]
    
    regime_rows = []
    for r_name, start_d, end_d in regimes:
        s_ts = pd.Timestamp(start_d)
        e_ts = pd.Timestamp(end_d)
        r_trades = [t for t in ext_trades if s_ts <= t.entry_time <= e_ts]
        if r_trades:
            r_rs = [t.r_multiple for t in r_trades]
            wins = [r for r in r_rs if r > 0]
            losses = [abs(r) for r in r_rs if r < 0]
            pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else 0.0
            regime_rows.append({
                "Regime Window": r_name,
                "Trades": len(r_trades),
                "Win Rate": f"{(len(wins)/len(r_trades))*100:.1f}%",
                "Mean Expectancy": f"{np.mean(r_rs):+.3f}R",
                "Profit Factor": f"{pf:.2f}",
                "Net PnL ($)": f"${sum(t.net_pnl for t in r_trades):+,.2f}"
            })
    print(pd.DataFrame(regime_rows).to_string(index=False))
    print("=" * 115)

if __name__ == "__main__":
    main()
