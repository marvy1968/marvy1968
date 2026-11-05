#!/usr/bin/env python3
"""
Elite20 V3: Pure-Source Parity Replay Engine
---------------------------------------------
Reads immutable local cached bars from data/elite20_v3/.
Executes strict T+1 open fills, overnight gap handling, and same-bar stop-first priority.
Tests Gated (Geometry >= 0.85) vs Ungated side-by-side.
"""

import json
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
import math
import numpy as np
import pandas as pd

BASE_DIR = Path("/home/moonrider1968/bots")
DATA_DIR = BASE_DIR / "data" / "elite20_v3"
OUT_DIR = BASE_DIR / "logs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

INITIAL_NAV = 100_000.0
RISK_PCT = 0.0050           # 0.50% NAV Risk
GROSS_RISK_CAP_PCT = 0.0150 # 1.50% Gross Open Risk Cap
MAX_CONCURRENT = 3
MAX_PER_SECTOR = 1
FRICTION_BPS = 0.0005       # 5 bps

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
SYMBOLS = sorted(list(SYMBOL_TO_SECTOR.keys()))

@dataclass
class Trade:
    id: int
    symbol: str
    sector: str
    signal_date: str
    entry_date: str
    exit_date: str
    entry_px: float
    stop_px: float
    target_px: float
    units: int
    dollar_risk: float
    geometry_ratio: float
    exit_px: float = 0.0
    exit_reason: str = "OPEN"
    net_pnl: float = 0.0
    r_multiple: float = 0.0
    hold_days: int = 0

def load_cached_data() -> Dict[str, pd.DataFrame]:
    dfs = {}
    for sym in SYMBOLS:
        p = DATA_DIR / f"{sym}.csv"
        if not p.exists():
            raise FileNotFoundError(f"Missing cached file: {p}")
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()

        # Compute indicators strictly on completed bars
        df["ma50"] = df["close"].rolling(50).mean()
        df["std50"] = df["close"].rolling(50).std()
        df["lower_25s"] = df["ma50"] - (2.5 * df["std50"])
        df["lower_35s"] = df["ma50"] - (3.5 * df["std50"])
        dfs[sym] = df
    return dfs

def run_simulation(dfs: Dict[str, pd.DataFrame], use_geometry_gate: bool = True) -> Tuple[List[Trade], pd.DataFrame]:
    all_dates = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    active_trades: List[Trade] = []
    closed_trades: List[Trade] = []
    queued_setups: List[dict] = []
    
    realized_cash = INITIAL_NAV
    trade_counter = 0
    equity_curve = []

    for i, current_date in enumerate(all_dates):
        # 1. RESOLVE OVERNIGHT GAPS AT 09:30:00 ET
        surviving_active = []
        for tr in active_trades:
            df = dfs.get(tr.symbol)
            if df is None or current_date not in df.index:
                surviving_active.append(tr)
                continue
            
            bar = df.loc[current_date]
            o = float(bar["open"])
            is_gap_exit = False
            exit_px, reason = 0.0, ""

            if o <= tr.stop_px:
                exit_px, reason, is_gap_exit = o * (1.0 - FRICTION_BPS), "STOP_GAP_OPEN", True
            elif o >= tr.target_px:
                exit_px, reason, is_gap_exit = o * (1.0 - FRICTION_BPS), "TARGET_GAP_OPEN", True

            if is_gap_exit:
                tr.exit_px = exit_px
                tr.exit_date = str(current_date.date())
                tr.exit_reason = reason
                tr.hold_days = (pd.to_datetime(tr.exit_date) - pd.to_datetime(tr.entry_date)).days
                pnl = (tr.exit_px - tr.entry_px) * tr.units
                tr.net_pnl = pnl
                tr.r_multiple = pnl / tr.dollar_risk if tr.dollar_risk > 0 else 0.0
                realized_cash += pnl
                closed_trades.append(tr)
            else:
                surviving_active.append(tr)
        
        active_trades = surviving_active

        # 2. MARK-TO-MARKET ACCOUNT EQUITY SNAPSHOT AT OPEN
        open_unrealized = 0.0
        for tr in active_trades:
            df = dfs.get(tr.symbol)
            if df is not None and current_date in df.index:
                o_px = float(df.loc[current_date]["open"])
                open_unrealized += (o_px - tr.entry_px) * tr.units
        current_mtm_equity = realized_cash + open_unrealized

        # 3. QUEUED ENTRY ADMISSIONS AT 09:30:15 ET
        if queued_setups:
            active_symbols = {t.symbol for t in active_trades}
            active_sectors = {t.sector for t in active_trades}
            current_open_risk = sum(t.dollar_risk for t in active_trades)

            for sig in queued_setups:
                sym = sig["symbol"]
                sec = sig["sector"]

                if sym in active_symbols or sec in active_sectors or len(active_trades) >= MAX_CONCURRENT:
                    continue

                target_risk_usd = current_mtm_equity * RISK_PCT
                if (current_open_risk + target_risk_usd) > (current_mtm_equity * GROSS_RISK_CAP_PCT + 1e-4):
                    continue

                df = dfs.get(sym)
                if df is None or current_date not in df.index:
                    continue

                bar = df.loc[current_date]
                t1_open = float(bar["open"])

                # Post-Gap Boundary Checks
                if t1_open <= sig["stop_px"] or t1_open >= sig["target_px"]:
                    continue

                actual_stop_dist = t1_open - sig["stop_px"]
                actual_target_dist = sig["target_px"] - t1_open

                if actual_stop_dist <= 0.05 or actual_target_dist <= 0.05:
                    continue

                actual_geometry = actual_target_dist / actual_stop_dist

                # Geometry Gate
                if use_geometry_gate and actual_geometry < 0.85:
                    continue

                qty = int(math.floor(target_risk_usd / actual_stop_dist))
                if qty < 1:
                    continue

                entry_fill = t1_open * (1.0 + FRICTION_BPS)
                actual_dollar_risk = (entry_fill - sig["stop_px"]) * qty

                trade_counter += 1
                new_trade = Trade(
                    id=trade_counter,
                    symbol=sym,
                    sector=sec,
                    signal_date=sig["signal_date"],
                    entry_date=str(current_date.date()),
                    exit_date="",
                    entry_px=entry_fill,
                    stop_px=sig["stop_px"],
                    target_px=sig["target_px"],
                    units=qty,
                    dollar_risk=actual_dollar_risk,
                    geometry_ratio=round(actual_geometry, 3)
                )
                active_trades.append(new_trade)
                active_symbols.add(sym)
                active_sectors.add(sec)
                current_open_risk += actual_dollar_risk

            queued_setups = []

        # 4. INTRADAY EVALUATION (STOP-FIRST RESOLUTION)
        still_active = []
        for tr in active_trades:
            df = dfs.get(tr.symbol)
            if df is None or current_date not in df.index:
                still_active.append(tr)
                continue

            bar = df.loc[current_date]
            h = float(bar["high"])
            l = float(bar["low"])

            stop_hit = l <= tr.stop_px
            target_hit = h >= tr.target_px

            # Stop-First Conservative Execution
            if stop_hit:
                tr.exit_px = tr.stop_px * (1.0 - FRICTION_BPS)
                tr.exit_reason = "STOP_INTRADAY"
            elif target_hit:
                tr.exit_px = tr.target_px * (1.0 - FRICTION_BPS)
                tr.exit_reason = "TARGET_INTRADAY"

            if stop_hit or target_hit:
                tr.exit_date = str(current_date.date())
                tr.hold_days = (pd.to_datetime(tr.exit_date) - pd.to_datetime(tr.entry_date)).days
                pnl = (tr.exit_px - tr.entry_px) * tr.units
                tr.net_pnl = pnl
                tr.r_multiple = pnl / tr.dollar_risk if tr.dollar_risk > 0 else 0.0
                realized_cash += pnl
                closed_trades.append(tr)
            else:
                still_active.append(tr)

        active_trades = still_active

        # 5. END OF DAY SIGNAL GENERATION FOR T+1
        for sym in SYMBOLS:
            df = dfs.get(sym)
            if df is None or current_date not in df.index:
                continue
            
            idx_loc = df.index.get_loc(current_date)
            if idx_loc < 51:
                continue

            cur = df.iloc[idx_loc]
            prev = df.iloc[idx_loc - 1]

            if any(pd.isna(cur[x]) for x in ["ma50", "std50", "lower_25s", "lower_35s"]):
                continue

            reclaim = (
                prev["close"] < prev["lower_25s"] and
                cur["close"] > cur["lower_25s"] and
                cur["close"] < cur["ma50"]
            )

            if reclaim:
                stop_px = float(cur["lower_35s"])
                target_px = float(cur["ma50"])
                if (cur["close"] - stop_px) > 0.05:
                    queued_setups.append({
                        "symbol": sym,
                        "sector": SYMBOL_TO_SECTOR[sym],
                        "signal_date": str(current_date.date()),
                        "stop_px": round(stop_px, 2),
                        "target_px": round(target_px, 2)
                    })

        # Record daily equity
        eod_unrealized = sum((float(dfs[t.symbol].loc[current_date]["close"]) - t.entry_px) * t.units for t in active_trades if current_date in dfs[t.symbol].index)
        equity_curve.append({
            "date": current_date,
            "nav": realized_cash + eod_unrealized,
            "cash": realized_cash,
            "open_positions": len(active_trades)
        })

    return closed_trades, pd.DataFrame(equity_curve)

def compute_metrics(trades: List[Trade], eq_df: pd.DataFrame, label: str):
    tdf = pd.DataFrame([asdict(t) for t in trades])
    out_csv = OUT_DIR / f"elite20_v3_{label.lower()}_trades.csv"
    tdf.to_csv(out_csv, index=False)

    total_trades = len(tdf)
    if total_trades == 0:
        print(f"[{label}] No trades generated.")
        return

    wins = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] <= 0]
    win_rate = len(wins) / total_trades
    
    gross_profits = wins["net_pnl"].sum()
    gross_losses = abs(losses["net_pnl"].sum())
    pf = gross_profits / gross_losses if gross_losses > 0 else 999.0

    net_r = tdf["r_multiple"].sum()
    mean_r = tdf["r_multiple"].mean()

    # Drawdown
    eq_df["hwm"] = eq_df["nav"].cummax()
    eq_df["dd"] = (eq_df["hwm"] - eq_df["nav"]) / eq_df["hwm"]
    max_dd = eq_df["dd"].max()
    final_nav = eq_df["nav"].iloc[-1]
    net_roi = (final_nav - INITIAL_NAV) / INITIAL_NAV

    print(f"\n=======================================================")
    print(f"  ELITE20 V3 PURE-SOURCE REPLAY: {label.upper()}")
    print(f"=======================================================")
    print(f"Total Completed Trades: {total_trades}")
    print(f"Win Rate:              {win_rate*100:.2f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Profit Factor (PF):    {pf:.3f}")
    print(f"Net R-Multiple:        {net_r:+.2f}R")
    print(f"Mean Expectancy (R):   {mean_r:+.3f}R / trade")
    print(f"Final NAV ($):         ${final_nav:,.2f} ({net_roi*100:+.2f}%)")
    print(f"Max Portfolio DD:      {max_dd*100:.2f}%")
    print(f"Ledger CSV Written:    {out_csv}")

if __name__ == "__main__":
    dfs = load_cached_data()
    
    # 1. Gated Baseline (Geometry >= 0.85)
    gated_trades, gated_eq = run_simulation(dfs, use_geometry_gate=True)
    compute_metrics(gated_trades, gated_eq, "Gated")

    # 2. Ungated Baseline (Testing the Geometry Hypothesis)
    ungated_trades, ungated_eq = run_simulation(dfs, use_geometry_gate=False)
    compute_metrics(ungated_trades, ungated_eq, "Ungated")
