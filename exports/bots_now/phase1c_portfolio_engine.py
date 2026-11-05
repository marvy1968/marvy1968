#!/usr/bin/env python3
"""
PHASE 1C — UNIFIED MULTI-EXPERT PORTFOLIO ALLOCATOR
Runs a shared-capital portfolio pooling the 5 certified strategy engines.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
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

OUTPUT_DIR = Path("/home/moonrider1968/bots/phase1c_output")
MAX_PORTFOLIO_POSITIONS = 5  # Max concurrent positions across all experts

# Priority ranking based on validated historical expectancy (R)
EXPERT_PRIORITY = {
    "Velez-Confluence Dip": 5,      # +0.599R
    "Momentum Burst": 4,            # +0.528R
    "Sector Top-Down": 3,           # +0.520R
    "Weinstein Stage 2 Long": 2,    # +0.493R
    "Multi-Layer Confluence": 1,     # +0.310R
}

# =============================================================================
# STRATEGY ADAPTERS
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
# UNIFIED MULTI-EXPERT ENGINE
# =============================================================================

def run_phase1c_portfolio():
    print("=" * 110)
    print("PHASE 1C — UNIFIED MULTI-EXPERT PORTFOLIO RUNNER")
    print(f"Capital: ${INITIAL_NAV:,.2f} | Max Concurrent Positions: {MAX_PORTFOLIO_POSITIONS} | Risk: {RISK_PER_TRADE_PCT*100}%/trade")
    print("=" * 110)

    dfs = load_and_prep_data()
    spy_df = dfs[BENCHMARK]
    common_dates = sorted(set.intersection(*[set(dfs[s].index) for s in UNIVERSE + [BENCHMARK]]))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    realized_nav = INITIAL_NAV
    open_positions: Dict[str, Tuple[str, Position]] = {}  # symbol -> (expert_name, Position)
    trades: List[Trade] = []
    equity_rows: List[dict] = []

    for t_idx in range(199, len(common_dates) - 1):
        curr_date = common_dates[t_idx]
        next_date = common_dates[t_idx + 1]

        # ---------------------------------------------------------------------
        # 1. EVALUATION AT BAR t CLOSE: EXITS & PRIORITY ENTRIES
        # ---------------------------------------------------------------------
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

        # Gather prospective entry intents across all 5 engines
        candidate_entries: Dict[str, Tuple[str, dict, int]] = {}  # sym -> (expert, signal_res, priority)
        
        for sym in UNIVERSE:
            if sym in open_positions:
                continue
            df = dfs[sym]
            idx = df.index.get_loc(curr_date)

            experts_to_check = [
                ("Velez-Confluence Dip", signal_velez_confluence(df, idx, None)),
                ("Momentum Burst", signal_momentum_burst(df, idx, None)),
                ("Sector Top-Down", signal_sector_topdown(df, idx, None, spy_df)),
                ("Weinstein Stage 2 Long", signal_weinstein_stage2(df, idx, None)),
                ("Multi-Layer Confluence", signal_multi_confluence(df, idx, None)),
            ]

            for exp_name, res in experts_to_check:
                if res.get("signal") in ("BUY", "SELL"):
                    prio = EXPERT_PRIORITY[exp_name]
                    if sym not in candidate_entries or prio > candidate_entries[sym][2]:
                        candidate_entries[sym] = (exp_name, res, prio)

        # Sort entry intents by priority descending
        sorted_entries = sorted(candidate_entries.items(), key=lambda x: x[1][2], reverse=True)

        # ---------------------------------------------------------------------
        # 2. SESSION t+1 OPEN: GAP PROTECTION & INTENT EXITS
        # ---------------------------------------------------------------------
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

        # ---------------------------------------------------------------------
        # 3. SESSION t+1 OPEN: EXECUTE NEW ENSEMBLE ENTRIES
        # ---------------------------------------------------------------------
        for sym, (exp_name, res, _) in sorted_entries:
            if len(open_positions) >= MAX_PORTFOLIO_POSITIONS or sym in open_positions:
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
            risk_dollars = nav_open * RISK_PER_TRADE_PCT
            qty = int(risk_dollars / risk_per_share)
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

        # ---------------------------------------------------------------------
        # 4. SESSION t+1 INTRADAY: CONSERVATIVE STOP-FIRST
        # ---------------------------------------------------------------------
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

        # ---------------------------------------------------------------------
        # 5. MTM CLOSE
        # ---------------------------------------------------------------------
        pos_dict_only = {s: p for s, (_, p) in open_positions.items()}
        mtm = calculate_mtm_nav(realized_nav, pos_dict_only, dfs, next_date, price_col="close")
        equity_rows.append({"date": next_date, "realized_nav": realized_nav, "mtm_nav": mtm, "positions": len(open_positions)})

    # Final Liquidation
    final_date = common_dates[-1]
    for sym in list(open_positions.keys()):
        exp_name, pos = open_positions[sym]
        raw_close = float(dfs[sym].loc[final_date, "close"])
        px = raw_close * (1.0 - SLIPPAGE_PCT) if pos.side == "BUY" else raw_close * (1.0 + SLIPPAGE_PCT)
        t, nav_chg = close_trade(exp_name, pos, final_date, px, "END_OF_TEST")
        realized_nav += nav_chg
        trades.append(t)

    equity_df = pd.DataFrame(equity_rows).drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)

    # -------------------------------------------------------------------------
    # COMPREHENSIVE PERFORMANCE REPORT
    # -------------------------------------------------------------------------
    trade_df = pd.DataFrame([t.__dict__ for t in trades])
    trade_df.to_csv(OUTPUT_DIR / "phase1c_ensemble_trades.csv", index=False)
    equity_df.to_csv(OUTPUT_DIR / "phase1c_ensemble_equity.csv", index=False)

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

    print("\n" + "=" * 80)
    print("PHASE 1C ENSEMBLE PORTFOLIO SUMMARY")
    print("=" * 80)
    print(f"Total Completed Trades:    {len(trades)}")
    print(f"Win Rate:                  {win_rate:.1f}%")
    print(f"Profit Factor:             {pf:.2f}")
    print(f"Expectancy (R):            {exp_r:+.3f}R")
    print(f"Median R:                  {med_r:+.3f}R")
    print(f"Net Realized PnL:          ${total_net:+,.2f}")
    print(f"Portfolio Return:          {total_ret:+.2f}%")
    print(f"Max MTM Drawdown:          {max_dd:.2f}%")
    print("=" * 80)

    # Breakdown by Expert Sub-Engine
    print("\nSUB-ENGINE ATTRIBUTION:")
    breakdown = []
    for exp_name in EXPERT_PRIORITY.keys():
        sub_trades = [t for t in trades if t.expert == exp_name]
        if not sub_trades:
            continue
        s_pnls = np.array([t.pnl for t in sub_trades])
        s_rvals = np.array([t.r_mult for t in sub_trades])
        s_wins = s_pnls[s_pnls > 0].sum()
        s_losses = -s_pnls[s_pnls < 0].sum()
        s_pf = (s_wins / s_losses) if s_losses > 0 else 0.0
        breakdown.append({
            "Expert": exp_name,
            "Trades": len(sub_trades),
            "Win Rate": f"{(s_pnls > 0).mean()*100:.1f}%",
            "PF": f"{s_pf:.2f}",
            "Exp (R)": f"{s_rvals.mean():+.3f}R",
            "Net PnL": f"${s_pnls.sum():+,.2f}",
        })
    print(pd.DataFrame(breakdown).to_string(index=False))
    print("=" * 80)
    print(f"Artifacts saved to: {OUTPUT_DIR}\n")


if __name__ == "__main__":
    run_phase1c_portfolio()
