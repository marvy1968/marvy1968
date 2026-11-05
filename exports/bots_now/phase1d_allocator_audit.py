#!/usr/bin/env python3
"""
PHASE 1D — ALLOCATOR ARCHITECTURE & SLEEVE DIVERSIFICATION AUDIT

Standardized Comparison of Allocator Mechanics & Sub-Engine Combinations
Strict 3-Slot Shared Risk | Dual Risk+Notional Caps | Reconciled MTM Equity
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from tournament_engine import (
    load_and_prep_data,
    Trade,
    Position,
    close_trade,
    check_open_gap,
    check_intraday_ohlc,
    calculate_mtm_nav,
    INITIAL_NAV,
    RISK_PER_TRADE_PCT,
    SLIPPAGE_PCT,
    sell_regulatory_fees,
    UNIVERSE,
    BENCHMARK,
)

OUTPUT_DIR = Path("/home/moonrider1968/bots/phase1d_output")

# Production Governors
MAX_PORTFOLIO_POSITIONS = 3
MAX_SINGLE_POS_NOTIONAL_PCT = 0.333  # 33.3% max NAV in single name
MAX_PORTFOLIO_GROSS_PCT = 1.000      # 100.0% max gross exposure

HISTORICAL_EXPECTANCY_PRIORITY = {
    "Velez-Confluence Dip": 5,
    "Momentum Burst": 4,
    "Sector Top-Down": 3,
    "Weinstein Stage 2 Long": 2,
    "Multi-Layer Confluence": 1,
}

# =============================================================================
# FROZEN STRATEGY SIGNALS
# =============================================================================

def signal_sector_topdown(df: pd.DataFrame, idx: int, pos: Optional[Position], spy_df: pd.DataFrame) -> dict:
    row = df.iloc[idx]
    date = df.index[idx]
    if date not in spy_df.index or idx < 20 or pd.isna(row["ema20"]):
        return {"signal": "HOLD"}

    stock_base = float(df["close"].iloc[idx - 20])
    spy_hist = spy_df.loc[:date]
    if len(spy_hist) < 21:
        return {"signal": "HOLD"}

    spy_now = float(spy_hist["close"].iloc[-1])
    spy_base = float(spy_hist["close"].iloc[-21])
    if stock_base <= 0 or spy_base <= 0:
        return {"signal": "HOLD"}

    stock_perf = (float(row["close"]) / stock_base) - 1.0
    spy_perf = (spy_now / spy_base) - 1.0

    if pos is None:
        if stock_perf > (spy_perf + 0.03) and row["close"] > row["ema20"] > row["sma50"]:
            return {"signal": "BUY", "stop": float(row["ema20"]) - float(row["atr14"]), "target_r": None}
    else:
        if row["close"] < row["ema20"]:
            return {"signal": "EXIT", "reason": "TOPDOWN_EMA20_BREAK"}
    return {"signal": "HOLD"}


def signal_momentum_burst(df: pd.DataFrame, idx: int, pos: Optional[Position]) -> dict:
    row = df.iloc[idx]
    if pd.isna(row["roc5"]) or pd.isna(row["vol_surge"]) or pd.isna(row["ema20"]):
        return {"signal": "HOLD"}

    if pos is None:
        if row["roc5"] > 0.04 and row["vol_surge"] > 1.80 and row["close"] > row["ema20"] and row["atr_expansion"] > 1.10:
            return {"signal": "BUY", "stop": float(row["close"]) - 1.5 * float(row["atr14"]), "target_r": 2.0}
    return {"signal": "HOLD"}


def signal_weinstein_stage2(df: pd.DataFrame, idx: int, pos: Optional[Position]) -> dict:
    row = df.iloc[idx]
    if pd.isna(row["sma200"]) or pd.isna(row["sma50"]) or pd.isna(row["donch_high_30"]):
        return {"signal": "HOLD"}

    if pos is None:
        if row["close"] > row["sma50"] > row["sma200"] and row["close"] > row["donch_high_30"] and row["vol_surge"] > 1.25:
            return {"signal": "BUY", "stop": float(row["sma50"]) - 0.5 * float(row["atr14"]), "target_r": None}
    else:
        if row["close"] < row["sma50"]:
            return {"signal": "EXIT", "reason": "STAGE2_BREAKDOWN"}
    return {"signal": "HOLD"}


def signal_multi_confluence(df: pd.DataFrame, idx: int, pos: Optional[Position]) -> dict:
    row = df.iloc[idx]
    if pd.isna(row["ema20"]) or pd.isna(row["bb_lower"]) or pd.isna(row["rsi2"]):
        return {"signal": "HOLD"}

    if pos is None:
        score = sum([
            row["close"] > row["ema20"],
            row["close"] > row["sma50"],
            row["low"] <= row["bb_lower"] * 1.01,
            row["rsi2"] < 25.0,
            row["vol_surge"] > 1.0,
        ])
        if score >= 4:
            return {"signal": "BUY", "stop": float(row["low"]) - 0.75 * float(row["atr14"]), "target_r": 2.5}
    return {"signal": "HOLD"}


def signal_velez_confluence(df: pd.DataFrame, idx: int, pos: Optional[Position]) -> dict:
    row = df.iloc[idx]
    if pd.isna(row["sma200"]) or pd.isna(row["ema20"]) or pd.isna(row["rsi2"]):
        return {"signal": "HOLD"}

    if pos is None:
        if (
            row["close"] > row["sma200"]
            and row["close"] > row["ema20"]
            and abs(row["low"] - row["ema20"]) / row["ema20"] < 0.02
            and row["close"] > row["open"]
            and row["rsi2"] < 35.0
            and row["atr_ratio"] <= 0.055
        ):
            return {"signal": "BUY", "stop": float(row["low"]) - 0.5 * float(row["atr14"]), "target_r": 3.0}
    return {"signal": "HOLD"}


# =============================================================================
# ALLOCATOR SIMULATION ENGINE
# =============================================================================

def run_allocator_backtest(
    config_name: str,
    enabled_experts: List[str],
    allocator_mode: str,
    dfs: Dict[str, pd.DataFrame],
) -> Tuple[dict, List[Trade], pd.DataFrame]:
    
    spy_df = dfs[BENCHMARK]
    common_dates = sorted(set.intersection(*[set(dfs[s].index) for s in UNIVERSE + [BENCHMARK]]))

    realized_nav = INITIAL_NAV
    open_positions: Dict[str, Tuple[str, Position]] = {}
    trades: List[Trade] = []
    equity_rows: List[dict] = []
    
    # State for round-robin allocation
    round_robin_idx = 0

    for t_idx in range(199, len(common_dates) - 1):
        curr_date = common_dates[t_idx]
        next_date = common_dates[t_idx + 1]

        # 1. Evaluate Bar t Close: Exits
        exit_intents: Dict[str, str] = {}
        for sym, (exp_name, pos) in list(open_positions.items()):
            df = dfs[sym]
            idx = df.index.get_loc(curr_date)
            res = {"signal": "HOLD"}
            if exp_name == "Sector Top-Down":
                res = signal_sector_topdown(df, idx, pos, spy_df)
            elif exp_name == "Weinstein Stage 2 Long":
                res = signal_weinstein_stage2(df, idx, pos)
            if res.get("signal") == "EXIT":
                exit_intents[sym] = res.get("reason", "SIGNAL_EXIT")

        # 2. Evaluate Bar t Close: Entry Candidates
        candidates: List[Tuple[str, str, dict]] = []  # (symbol, expert_name, signal_res)
        for sym in UNIVERSE:
            if sym in open_positions:
                continue
            df = dfs[sym]
            idx = df.index.get_loc(curr_date)

            for exp_name in enabled_experts:
                res = {"signal": "HOLD"}
                if exp_name == "Sector Top-Down":
                    res = signal_sector_topdown(df, idx, None, spy_df)
                elif exp_name == "Momentum Burst":
                    res = signal_momentum_burst(df, idx, None)
                elif exp_name == "Weinstein Stage 2 Long":
                    res = signal_weinstein_stage2(df, idx, None)
                elif exp_name == "Multi-Layer Confluence":
                    res = signal_multi_confluence(df, idx, None)
                elif exp_name == "Velez-Confluence Dip":
                    res = signal_velez_confluence(df, idx, None)

                if res.get("signal") in ("BUY", "SELL"):
                    candidates.append((sym, exp_name, res))

        # 3. Allocator Mode Ordering & Filtering
        if allocator_mode == "equal_priority":
            # Natural symbol order (first valid signal per symbol)
            ordered_entries = []
            seen = set()
            for sym, exp, res in candidates:
                if sym not in seen:
                    ordered_entries.append((sym, exp, res))
                    seen.add(sym)

        elif allocator_mode == "expectancy_priority":
            # Sort by historical expectancy rank descending
            sorted_c = sorted(candidates, key=lambda x: HISTORICAL_EXPECTANCY_PRIORITY.get(x[1], 0), reverse=True)
            ordered_entries = []
            seen = set()
            for sym, exp, res in sorted_c:
                if sym not in seen:
                    ordered_entries.append((sym, exp, res))
                    seen.add(sym)

        elif allocator_mode == "round_robin":
            # Cycle sequentially through expert sleeves
            ordered_entries = []
            seen = set()
            if enabled_experts:
                sorted_by_rr = sorted(candidates, key=lambda x: (enabled_experts.index(x[1]) - round_robin_idx) % len(enabled_experts))
                for sym, exp, res in sorted_by_rr:
                    if sym not in seen:
                        ordered_entries.append((sym, exp, res))
                        seen.add(sym)

        elif allocator_mode == "max_1_per_expert":
            # Hard limit of 1 position per expert sleeve
            ordered_entries = []
            seen = set()
            active_experts = {exp for _, (exp, _) in open_positions.items()}
            for sym, exp, res in candidates:
                if sym not in seen and exp not in active_experts:
                    ordered_entries.append((sym, exp, res))
                    seen.add(sym)
                    active_experts.add(exp)
        else:
            ordered_entries = candidates

        # 4. Session t+1 Open: Gap Exits & Signal Exits
        for sym in list(open_positions.keys()):
            exp_name, pos = open_positions[sym]
            row = dfs[sym].loc[next_date]
            gap_event = check_open_gap(pos, row)
            if gap_event:
                px, reason = gap_event
                t, nav_chg = close_trade(exp_name, pos, next_date, px, reason)
                realized_nav += nav_chg
                trades.append(t)
                del open_positions[sym]

        for sym, reason in exit_intents.items():
            if sym in open_positions:
                exp_name, pos = open_positions[sym]
                row = dfs[sym].loc[next_date]
                raw_open = float(row["open"])
                px = raw_open * (1.0 - SLIPPAGE_PCT) if pos.side == "BUY" else raw_open * (1.0 + SLIPPAGE_PCT)
                t, nav_chg = close_trade(exp_name, pos, next_date, px, reason)
                realized_nav += nav_chg
                trades.append(t)
                del open_positions[sym]

        # 5. Session t+1 Open: Execute Qualified Entries (Dual Risk + Notional Caps)
        for sym, exp_name, res in ordered_entries:
            if len(open_positions) >= MAX_PORTFOLIO_POSITIONS or sym in open_positions:
                continue

            if allocator_mode == "max_1_per_expert":
                if any(exp == exp_name for _, (exp, _) in open_positions.items()):
                    continue

            row = dfs[sym].loc[next_date]
            side = res["signal"]
            raw_open = float(row["open"])
            entry_fill = raw_open * (1.0 + SLIPPAGE_PCT) if side == "BUY" else raw_open * (1.0 - SLIPPAGE_PCT)
            initial_stop = float(res["stop"])

            if (side == "BUY" and initial_stop >= entry_fill) or (side == "SELL" and initial_stop <= entry_fill):
                continue

            risk_per_share = abs(entry_fill - initial_stop)
            if not np.isfinite(risk_per_share) or risk_per_share <= 0:
                continue

            pos_dict_only = {s: p for s, (_, p) in open_positions.items()}
            nav_open = calculate_mtm_nav(realized_nav, pos_dict_only, dfs, next_date, price_col="open")

            # Risk-based sizing
            risk_dollars = nav_open * RISK_PER_TRADE_PCT
            qty_risk = int(risk_dollars / risk_per_share)

            # Notional sizing caps
            max_pos_notional = nav_open * MAX_SINGLE_POS_NOTIONAL_PCT
            qty_single_cap = int(max_pos_notional / entry_fill)

            current_gross = sum(p.entry_price * p.qty for p in pos_dict_only.values())
            remaining_gross = max(0.0, (nav_open * MAX_PORTFOLIO_GROSS_PCT) - current_gross)
            qty_gross_cap = int(remaining_gross / entry_fill)

            # Final constrained quantity
            qty = min(qty_risk, qty_single_cap, qty_gross_cap)
            if qty <= 0:
                continue

            entry_fees = sell_regulatory_fees(entry_fill, qty) if side == "SELL" else 0.0
            realized_nav -= entry_fees

            target_r = res.get("target_r")
            tp = None
            if target_r is not None:
                tp = entry_fill + (float(target_r) * risk_per_share) if side == "BUY" else entry_fill - (float(target_r) * risk_per_share)

            new_pos = Position(
                symbol=sym,
                side=side,
                entry_date=next_date,
                entry_price=entry_fill,
                initial_stop=initial_stop,
                current_stop=initial_stop,
                initial_risk_per_share=risk_per_share,
                qty=qty,
                tp=tp,
                entry_fees=entry_fees,
            )
            open_positions[sym] = (exp_name, new_pos)
            round_robin_idx = (round_robin_idx + 1) % max(1, len(enabled_experts))

        # 6. Session t+1 Intraday: Stop-First Execution
        for sym in list(open_positions.keys()):
            exp_name, pos = open_positions[sym]
            row = dfs[sym].loc[next_date]
            event = check_intraday_ohlc(pos, row)
            if event:
                px, reason = event
                t, nav_chg = close_trade(exp_name, pos, next_date, px, reason)
                realized_nav += nav_chg
                trades.append(t)
                del open_positions[sym]

        # 7. MTM Close Tracking
        pos_dict_only = {s: p for s, (_, p) in open_positions.items()}
        mtm = calculate_mtm_nav(realized_nav, pos_dict_only, dfs, next_date, price_col="close")
        equity_rows.append({"date": next_date, "realized_nav": realized_nav, "mtm_nav": mtm, "positions": len(open_positions)})

    # Final Liquidation & Direct Reconciliation
    final_date = common_dates[-1]
    for sym in list(open_positions.keys()):
        exp_name, pos = open_positions[sym]
        raw_close = float(dfs[sym].loc[final_date, "close"])
        px = raw_close * (1.0 - SLIPPAGE_PCT) if pos.side == "BUY" else raw_close * (1.0 + SLIPPAGE_PCT)
        t, nav_chg = close_trade(exp_name, pos, final_date, px, "END_OF_TEST")
        realized_nav += nav_chg
        trades.append(t)

    open_positions.clear()
    equity_rows.append({"date": final_date, "realized_nav": realized_nav, "mtm_nav": realized_nav, "positions": 0})

    equity_df = pd.DataFrame(equity_rows).drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)

    # Metrics
    pnls = np.array([t.pnl for t in trades], dtype=float)
    rvals = np.array([t.r_mult for t in trades], dtype=float)
    wins = pnls[pnls > 0].sum()
    losses = -pnls[pnls < 0].sum()
    pf = (wins / losses) if losses > 0 else (99.0 if wins > 0 else 0.0)
    win_rate = float((pnls > 0).mean() * 100.0) if len(pnls) else 0.0
    exp_r = float(np.mean(rvals)) if len(rvals) else 0.0
    med_r = float(np.median(rvals)) if len(rvals) else 0.0

    eq = equity_df["mtm_nav"].to_numpy()
    peaks = np.maximum.accumulate(eq)
    drawdowns = (peaks - eq) / np.where(peaks != 0, peaks, np.nan)
    max_dd = float(np.nanmax(drawdowns) * 100.0) if len(eq) else 0.0

    total_net = realized_nav - INITIAL_NAV
    total_ret = (realized_nav / INITIAL_NAV - 1.0) * 100.0
    calmar = (total_ret / max_dd) if max_dd > 0 else 0.0

    metrics = {
        "trades": len(trades),
        "win_rate": win_rate,
        "profit_factor": pf,
        "expectancy_r": exp_r,
        "median_r": med_r,
        "net_pnl": total_net,
        "return_pct": total_ret,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "final_nav": realized_nav,
    }

    return metrics, trades, equity_df


# =============================================================================
# SUITE RUNNER
# =============================================================================

ALL_5_EXPERTS = [
    "Sector Top-Down",
    "Velez-Confluence Dip",
    "Momentum Burst",
    "Weinstein Stage 2 Long",
    "Multi-Layer Confluence",
]

CONFIGURATIONS = [
    ("A. 3-Slot Equal Priority (All 5)", ALL_5_EXPERTS, "equal_priority"),
    ("B. 3-Slot Round-Robin (All 5)", ALL_5_EXPERTS, "round_robin"),
    ("C. 3-Slot Max-1/Expert (All 5)", ALL_5_EXPERTS, "max_1_per_expert"),
    ("D. 3-Slot Expectancy-Prio (All 5)", ALL_5_EXPERTS, "expectancy_priority"),
    ("E. Sector Top-Down Alone", ["Sector Top-Down"], "equal_priority"),
    ("F. Sector + Velez-Confluence", ["Sector Top-Down", "Velez-Confluence Dip"], "equal_priority"),
    ("G. Sector + Momentum Burst", ["Sector Top-Down", "Momentum Burst"], "equal_priority"),
    ("H. Sector + Velez + Momentum", ["Sector Top-Down", "Velez-Confluence Dip", "Momentum Burst"], "equal_priority"),
]


def main():
    print("=" * 115)
    print("PHASE 1D — ALLOCATOR ARCHITECTURE & SLEEVE COMBINATION AUDIT")
    print("Constraint Matrix: Max 3 Positions | 0.35% NAV Risk | 33.3% Single Notional Cap | 100% Gross Cap")
    print("=" * 115)

    dfs = load_and_prep_data()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_rows = []

    for name, enabled, mode in CONFIGURATIONS:
        metrics, trades, equity = run_allocator_backtest(name, enabled, mode, dfs)
        slug = name.lower().replace(" ", "_").replace("/", "_").replace(".", "").replace("(", "").replace(")", "").replace("-", "_")

        pd.DataFrame([t.__dict__ for t in trades]).to_csv(OUTPUT_DIR / f"{slug}_trades.csv", index=False)
        equity.to_csv(OUTPUT_DIR / f"{slug}_equity.csv", index=False)

        summary_rows.append({
            "Configuration": name,
            "Trades": metrics["trades"],
            "Win Rate": f"{metrics['win_rate']:.1f}%",
            "PF": f"{metrics['profit_factor']:.2f}",
            "Exp (R)": f"{metrics['expectancy_r']:+.3f}R",
            "Return": f"{metrics['return_pct']:+.2f}%",
            "Max DD": f"{metrics['max_drawdown']:.2f}%",
            "Calmar": f"{metrics['calmar']:.2f}",
            "Net PnL": f"${metrics['net_pnl']:+,.2f}",
        })

    df_summary = pd.DataFrame(summary_rows)
    print(df_summary.to_string(index=False))
    print("=" * 115)
    df_summary.to_csv(OUTPUT_DIR / "phase1d_audit_summary.csv", index=False)
    print(f"Artifacts saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
