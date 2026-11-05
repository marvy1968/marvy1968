#!/usr/bin/env python3
"""
Elite 20 Equities: Parity Diagnostic & Entry-Cohort Macro Regime Engine (v2)
- Reconciles Legacy Prototype vs Hardened Production Accounting
- Evaluates strict mathematical parity against frozen baseline fingerprint
- Performs independent Entry-Cohort Macro Regime Attribution (2020 - 2026)
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

# Fixed Completed-Bar Dates (Excludes incomplete current sessions)
DATA_START = "2019-01-01"
DATA_END   = "2026-08-29"   # Exclusive end in yfinance (Captures through 2026-08-28)
EVAL_START_5Y = "2021-09-01"
EVAL_END_5Y   = "2026-08-28"

INITIAL_EQUITY = 100_000.0
BASE_RISK_PCT  = 0.0050
GROSS_RISK_CAP = 0.0150
MAX_CONCURRENT = 3
MAX_PER_SECTOR = 1

REFERENCE = {
    "trades": 73,
    "win_rate": 53.4,
    "mean_r": 0.493,
    "net_pf": 2.02,
    "comp_return": 19.22,
    "max_dd": 5.80
}

TOL = {
    "win_rate": 0.20,
    "mean_r": 0.005,
    "net_pf": 0.03,
    "comp_return": 0.15,
    "max_dd": 0.15
}

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

def load_data(symbols: List[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
    data = {}
    for sym in symbols:
        df = yf.download(sym, start=start_date, end=end_date, interval="1d", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        df = df.dropna()
        if len(df) >= 120:
            data[sym] = df
    return data

def generate_signals(df: pd.DataFrame, symbol: str, start_eval: pd.Timestamp) -> pd.DataFrame:
    df = df.copy()
    df["ma50"] = df["close"].rolling(50).mean()
    df["std50"] = df["close"].rolling(50).std()
    df["lower_25s"] = df["ma50"] - (2.5 * df["std50"])
    df["lower_35s"] = df["ma50"] - (3.5 * df["std50"])

    signals = []
    for i in range(55, len(df) - 1):
        cur = df.iloc[i]
        prev = df.iloc[i - 1]
        t = df.index[i]

        if t < start_eval:
            continue

        if prev["close"] < prev["lower_25s"] and cur["close"] > cur["lower_25s"] and cur["close"] < cur["ma50"]:
            stop_dist = cur["close"] - cur["lower_35s"]
            if stop_dist > 0:
                signals.append({
                    "timestamp": t,
                    "symbol": symbol,
                    "sector": SYMBOL_TO_SECTOR[symbol],
                    "side": "BUY",
                    "raw_entry_px": float(cur["close"]),
                    "stop_px": float(cur["lower_35s"]),
                    "target_px": float(cur["ma50"]),
                    "stop_dist": stop_dist
                })
    return pd.DataFrame(signals)

def run_simulation(
    all_signals: pd.DataFrame,
    dfs: Dict[str, pd.DataFrame],
    eval_start: pd.Timestamp,
    eval_end: pd.Timestamp,
    sizing_mode: str = "hardened"
) -> Tuple[dict, pd.DataFrame, List[StockTrade]]:

    if all_signals.empty:
        return {}, pd.DataFrame(), []

    sorted_sigs = all_signals.sort_values(by=["timestamp", "symbol"]).to_dict("records")
    all_dates = sorted(list(set().union(*(df.index for df in dfs.values()))))
    eval_dates = [d for d in all_dates if eval_start <= d <= eval_end]

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
                exit_px, reason, is_closed = o * (1.0 - 0.0005), "STOP_HIT", True
            elif o >= trade.target_px:
                exit_px, reason, is_closed = o * (1.0 - 0.0005), "TARGET_HIT", True
            elif l <= trade.stop_px:
                exit_px, reason, is_closed = trade.stop_px * (1.0 - 0.0005), "STOP_HIT", True
            elif h >= trade.target_px:
                exit_px, reason, is_closed = trade.target_px * (1.0 - 0.0005), "TARGET_HIT", True

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

        # Unrealized calculation
        unrealized = sum((float(dfs[tr.symbol].loc[current_date]["close"]) - tr.entry_px) * tr.units 
                         for tr in active_trades if current_date in dfs[tr.symbol].index)
        live_nav = (realized_nav + unrealized) if sizing_mode == "hardened" else realized_nav

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
            if sum(1 for p in active_trades if p.sector == sec) >= MAX_PER_SECTOR:
                continue

            entry_px = sig["raw_entry_px"] * (1.0 + 0.0005)
            stop_dist = (entry_px - sig["stop_px"]) if sizing_mode == "hardened" else sig["stop_dist"]
            if stop_dist <= 0:
                continue

            dollar_risk = live_nav * BASE_RISK_PCT
            
            if sizing_mode == "hardened":
                curr_risk = sum(p.units * (p.entry_px - p.stop_px) for p in active_trades)
                if (curr_risk + dollar_risk) > (live_nav * GROSS_RISK_CAP + 1e-6):
                    continue

            units = dollar_risk / stop_dist
            if units <= 0:
                continue

            trade_counter += 1
            trade_obj = StockTrade(
                trade_id=trade_counter, symbol=sym, sector=sec, side="BUY",
                entry_time=current_date, exit_time=current_date,
                entry_px=entry_px, stop_px=sig["stop_px"], target_px=sig["target_px"],
                units=units, dollar_risk=dollar_risk
            )
            active_trades.append(trade_obj)

        unrealized_close = sum((float(dfs[tr.symbol].loc[current_date]["close"]) - tr.entry_px) * tr.units 
                               for tr in active_trades if current_date in dfs[tr.symbol].index)
        equity_curve.append((current_date, realized_nav + unrealized_close))

    # Mark-to-market Terminal Open Positions with liquidation friction
    if active_trades:
        terminal_d = eval_dates[-1]
        for tr in active_trades:
            if tr.symbol in dfs and terminal_d in dfs[tr.symbol].index:
                c_px = float(dfs[tr.symbol].loc[terminal_d]["close"]) * (1.0 - 0.0005)
                tr.exit_px = c_px
                tr.exit_time = terminal_d
                tr.exit_reason = "TERMINAL_MTM"
                pnl = (tr.exit_px - tr.entry_px) * tr.units
                tr.net_pnl = pnl
                tr.r_multiple = pnl / tr.dollar_risk if tr.dollar_risk > 0 else 0.0
                realized_nav += pnl
                closed_trades.append(tr)
        active_trades = []

    eq_df = pd.DataFrame(equity_curve, columns=["date", "equity"])
    if not eq_df.empty:
        # Reconcile exact terminal equity after friction
        eq_df.loc[eq_df.index[-1], "equity"] = realized_nav
        eq_df["cummax"] = eq_df["equity"].cummax()
        eq_df["dd"] = (eq_df["cummax"] - eq_df["equity"]) / eq_df["cummax"]
        max_dd = float(eq_df["dd"].max() * 100.0)
    else:
        max_dd = 0.0

    net_rs = [t.r_multiple for t in closed_trades]
    wins = [r for r in net_rs if r > 0]
    losses = [abs(r) for r in net_rs if r < 0]
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else 0.0
    final_eq = realized_nav

    stats = {
        "trades": len(closed_trades),
        "win_rate": (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0,
        "mean_r": float(np.mean(net_rs)) if net_rs else 0.0,
        "net_pf": pf,
        "comp_return": ((final_eq - INITIAL_EQUITY) / INITIAL_EQUITY) * 100.0,
        "max_dd": max_dd,
        "final_equity": final_eq
    }
    return stats, eq_df, closed_trades

def main():
    print("=" * 115)
    print(" 🔬 ELITE 20: AUDIT RECONCILIATION & MACRO REGIME CERTIFICATION ENGINE (V2)")
    print("=" * 115)

    # ---------------------------------------------------------
    # PART 1: PARITY RECONCILIATION GATE (2021-09-01 to 2026-08-28)
    # ---------------------------------------------------------
    print(f"\n [PART 1] 5-YEAR BASELINE RECONCILIATION ({EVAL_START_5Y} to {EVAL_END_5Y})")
    print("-" * 115)
    
    dfs_all = load_data(ELITE_20_SYMBOLS, DATA_START, DATA_END)
    sigs_5y_list = [generate_signals(dfs_all[s], s, pd.Timestamp(EVAL_START_5Y)) for s in dfs_all]
    sigs_5y = pd.concat(sigs_5y_list, ignore_index=True) if sigs_5y_list else pd.DataFrame()

    legacy_stats, _, _ = run_simulation(sigs_5y, dfs_all, pd.Timestamp(EVAL_START_5Y), pd.Timestamp(EVAL_END_5Y), sizing_mode="legacy")
    hardened_stats, _, _ = run_simulation(sigs_5y, dfs_all, pd.Timestamp(EVAL_START_5Y), pd.Timestamp(EVAL_END_5Y), sizing_mode="hardened")

    rec_rows = [
        {"Accounting Engine": "Legacy Prototype (Realized NAV + Fixed Risk)", "Trades": legacy_stats["trades"], "Win Rate": f"{legacy_stats['win_rate']:.1f}%", "Mean R": f"{legacy_stats['mean_r']:+.3f}R", "PF": f"{legacy_stats['net_pf']:.2f}", "Return": f"{legacy_stats['comp_return']:+.2f}%", "MaxDD": f"{legacy_stats['max_dd']:.2f}%"},
        {"Accounting Engine": "Hardened Production (Live MTM NAV + Dynamic Risk Cap)", "Trades": hardened_stats["trades"], "Win Rate": f"{hardened_stats['win_rate']:.1f}%", "Mean R": f"{hardened_stats['mean_r']:+.3f}R", "PF": f"{hardened_stats['net_pf']:.2f}", "Return": f"{hardened_stats['comp_return']:+.2f}%", "MaxDD": f"{hardened_stats['max_dd']:.2f}%"}
    ]
    print(pd.DataFrame(rec_rows).to_string(index=False))

    # Explicit Strict Diagnostic Check
    trade_match = legacy_stats["trades"] == REFERENCE["trades"]
    parity_ok = trade_match
    for k in ["win_rate", "mean_r", "net_pf", "comp_return", "max_dd"]:
        parity_ok &= (abs(legacy_stats[k] - REFERENCE[k]) <= TOL[k])

    print("-" * 115)
    if parity_ok:
        print("✅ STRICT LEGACY BASELINE PARITY: PASS")
        print("   Legacy simulation matches the frozen reference fingerprint.")
    else:
        print("❌ STRICT LEGACY BASELINE PARITY: FAIL")
        print("   Discrepancy detected between Legacy simulation and frozen fingerprint.")
        print(f"   Delta vs Reference -> Trades: {legacy_stats['trades'] - REFERENCE['trades']} | WR: {legacy_stats['win_rate'] - REFERENCE['win_rate']:+.2f}% | PF: {legacy_stats['net_pf'] - REFERENCE['net_pf']:+.2f} | Return: {legacy_stats['comp_return'] - REFERENCE['comp_return']:+.2f}% | MaxDD: {legacy_stats['max_dd'] - REFERENCE['max_dd']:+.2f}%")
        print("   Destruction/regime certification is diagnostic only until reconciled.")
    print("-" * 115)

    # ---------------------------------------------------------
    # PART 2: ENTRY-COHORT MACRO REGIME ATTRIBUTION (2020-2026)
    # ---------------------------------------------------------
    print("\n" + "=" * 115)
    print(" [PART 2] ENTRY-COHORT MACRO REGIME ATTRIBUTION (2020-01-01 to 2026-08-28)")
    print("=" * 115)
    
    sigs_ext_list = [generate_signals(dfs_all[s], s, pd.Timestamp("2020-01-01")) for s in dfs_all]
    sigs_ext = pd.concat(sigs_ext_list, ignore_index=True) if sigs_ext_list else pd.DataFrame()
    
    ext_stats, _, ext_trades = run_simulation(sigs_ext, dfs_all, pd.Timestamp("2020-01-01"), pd.Timestamp(EVAL_END_5Y), sizing_mode="hardened")
    
    print(f" Extended Sample: {ext_stats['trades']} Trades | Overall WR: {ext_stats['win_rate']:.1f}% | Overall PF: {ext_stats['net_pf']:.2f} | 6.7Y Return: {ext_stats['comp_return']:+.2f}%\n")

    regimes = [
        ("2020 (COVID Crash & V-Bottom Recovery)", "2020-01-01", "2020-12-31"),
        ("2021 (Stimulus & Low-Rate Tech Bull)", "2021-01-01", "2021-12-31"),
        ("2022 (Fed Tightening Bear Market)", "2022-01-01", "2022-12-31"),
        ("2023-2024 (AI Infrastructure Expansion)", "2023-01-01", "2024-12-31"),
        ("2025-2026 (Consolidation & Late Cycle)", "2025-01-01", "2026-08-28")
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
                "Entry-Cohort Regime Window": r_name,
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
