#!/usr/bin/env python3
"""
Phase 3 Canonical Ensemble & Overlay Harness
Exact Execution Equivalence with tournament_engine.py:
- Strict t -> t+1 Open Fill
- Immediate Day 1 Intraday Stop-First Check
- Sizing based on MTM NAV at Open (price_col="open")
- Sell regulatory fees on entry for shorts, exit for longs
- Fair Multi-Expert Contention (Round-Robin Symbol & Expert Allocation)
- Full final-bar liquidation settlement
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import tournament_engine as te

UNIVERSE = te.UNIVERSE
BENCHMARK = te.BENCHMARK
INITIAL_NAV = te.INITIAL_NAV
RISK_PER_TRADE_PCT = te.RISK_PER_TRADE_PCT
SLIPPAGE_PCT = te.SLIPPAGE_PCT
MAX_PORTFOLIO_POSITIONS = 3

def run_governed_ensemble(
    expert_names: List[str],
    dfs: Dict[str, pd.DataFrame],
    max_slots: int = 3
) -> Tuple[dict, List[te.Trade], pd.DataFrame]:
    
    spy_df = dfs[BENCHMARK]
    common_dates = sorted(set.intersection(*[set(dfs[s].index) for s in UNIVERSE + [BENCHMARK]]))
    
    realized_nav = INITIAL_NAV
    open_positions: Dict[str, Tuple[te.Position, str]] = {}  # sym -> (Position, expert_name)
    trades: List[te.Trade] = []
    equity_rows: List[dict] = []
    
    for t_idx in range(199, len(common_dates) - 1):
        curr_date = common_dates[t_idx]
        next_date = common_dates[t_idx + 1]
        
        # 1. Signals at bar t close for open positions
        exit_intents: Dict[str, str] = {}
        for sym, (pos, exp_name) in list(open_positions.items()):
            df = dfs[sym]
            idx = df.index.get_loc(curr_date)
            fn = te.EXPERTS[exp_name]
            res = fn(df, idx, pos, spy_df) if exp_name == "8. Sector Top-Down" else fn(df, idx, pos)
            if res.get("signal") == "EXIT":
                exit_intents[sym] = res.get("reason", "SIGNAL_EXIT")

        # Signals at bar t close for candidate entries
        raw_candidates: List[Tuple[str, str, dict]] = []
        for sym in UNIVERSE:
            if sym in open_positions:
                continue
            df = dfs[sym]
            idx = df.index.get_loc(curr_date)
            
            for exp_name in expert_names:
                fn = te.EXPERTS[exp_name]
                res = fn(df, idx, None, spy_df) if exp_name == "8. Sector Top-Down" else fn(df, idx, None)
                if res.get("signal") in ("BUY", "SELL"):
                    raw_candidates.append((sym, exp_name, res))

        # Deterministic fair contention allocation:
        # Interleave candidates evenly across competing experts
        by_expert = {e: [] for e in expert_names}
        for sym, exp, res in raw_candidates:
            by_expert[exp].append((sym, exp, res))
        
        interleaved_candidates: List[Tuple[str, str, dict]] = []
        max_cands = max((len(v) for v in by_expert.values()), default=0)
        for i in range(max_cands):
            for exp in expert_names:
                if i < len(by_expert[exp]):
                    interleaved_candidates.append(by_expert[exp][i])
                    
        seen_syms = set()
        unique_entry_intents: List[Tuple[str, str, dict]] = []
        for sym, exp, res in interleaved_candidates:
            if sym not in seen_syms and sym not in open_positions:
                seen_syms.add(sym)
                unique_entry_intents.append((sym, exp, res))

        # 2. t+1 Open: Gap exits
        for sym in list(open_positions.keys()):
            pos, exp_name = open_positions[sym]
            row = dfs[sym].loc[next_date]
            gap_event = te.check_open_gap(pos, row)
            if gap_event:
                px, reason = gap_event
                t, nav_chg = te.close_trade(exp_name, pos, next_date, px, reason)
                realized_nav += nav_chg
                trades.append(t)
                del open_positions[sym]

        # 3. t+1 Open: Signal exits
        for sym, reason in exit_intents.items():
            if sym in open_positions:
                pos, exp_name = open_positions[sym]
                row = dfs[sym].loc[next_date]
                raw_open = float(row["open"])
                px = raw_open * (1.0 - SLIPPAGE_PCT) if pos.side == "BUY" else raw_open * (1.0 + SLIPPAGE_PCT)
                t, nav_chg = te.close_trade(exp_name, pos, next_date, px, reason)
                realized_nav += nav_chg
                trades.append(t)
                del open_positions[sym]

        # 4. t+1 Open: Entries
        raw_pos_map = {s: p for s, (p, _) in open_positions.items()}
        for sym, exp_name, res in unique_entry_intents:
            if len(open_positions) >= max_slots or sym in open_positions:
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

            nav_open = te.calculate_mtm_nav(realized_nav, raw_pos_map, dfs, next_date, price_col="open")
            risk_dollars = nav_open * RISK_PER_TRADE_PCT
            qty = int(risk_dollars / risk_per_share)
            if qty <= 0:
                continue

            entry_fees = te.sell_regulatory_fees(entry_fill, qty) if side == "SELL" else 0.0
            realized_nav -= entry_fees

            target_r = res.get("target_r")
            tp = None
            if target_r is not None:
                tp = entry_fill + (float(target_r) * risk_per_share) if side == "BUY" else entry_fill - (float(target_r) * risk_per_share)

            pos = te.Position(
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
            open_positions[sym] = (pos, exp_name)
            raw_pos_map[sym] = pos

        # 5. t+1 Intraday: Stop vs TP (Stop First)
        for sym in list(open_positions.keys()):
            pos, exp_name = open_positions[sym]
            row = dfs[sym].loc[next_date]
            event = te.check_intraday_ohlc(pos, row)
            if event:
                px, reason = event
                t, nav_chg = te.close_trade(exp_name, pos, next_date, px, reason)
                realized_nav += nav_chg
                trades.append(t)
                del open_positions[sym]

        # 6. Mark-to-market at close
        raw_pos_map = {s: p for s, (p, _) in open_positions.items()}
        mtm = te.calculate_mtm_nav(realized_nav, raw_pos_map, dfs, next_date, price_col="close")
        equity_rows.append({
            "date": next_date,
            "realized_nav": realized_nav,
            "mtm_nav": mtm,
            "positions": len(open_positions)
        })

    # Final Close Liquidation Settle
    final_date = common_dates[-1]
    for sym in list(open_positions.keys()):
        pos, exp_name = open_positions[sym]
        raw_close = float(dfs[sym].loc[final_date, "close"])
        px = raw_close * (1.0 - SLIPPAGE_PCT) if pos.side == "BUY" else raw_close * (1.0 + SLIPPAGE_PCT)
        t, nav_chg = te.close_trade(exp_name, pos, final_date, px, "END_OF_BACKTEST_SETTLE")
        realized_nav += nav_chg
        trades.append(t)
        del open_positions[sym]

    eq_df = pd.DataFrame(equity_rows)
    mtm_series = eq_df["mtm_nav"].to_numpy(dtype=float)
    peaks = np.maximum.accumulate(mtm_series)
    dds = (peaks - mtm_series) / np.maximum(peaks, 1e-12)
    max_dd = float(np.max(dds) * 100.0) if len(dds) > 0 else 0.0

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (99.0 if wins else 0.0)

    metrics = {
        "trades": len(trades),
        "win_rate": (len(wins) / len(pnls) * 100) if pnls else 0.0,
        "pf": pf,
        "mean_r": float(np.mean([t.r_mult for t in trades])) if trades else 0.0,
        "return_pct": (realized_nav / INITIAL_NAV - 1.0) * 100.0,
        "max_dd": max_dd,
        "net_pnl": sum(pnls)
    }
    return metrics, trades, eq_df

def main():
    print("=" * 122)
    print(" 🔬 PHASE 3: CANONICAL MULTI-SLEEVE & ENSEMBLE CERTIFICATION HARNESS")
    print("=" * 122)

    dfs = te.load_and_prep_data()

    configs = {
        # 1. Baseline Exact Reproduction Check
        "A. Sector Top-Down (Solo)": ["8. Sector Top-Down"],
        "B. Momentum Burst (Solo)": ["5. Momentum Burst"],
        "C. Weinstein Stage 2 (Solo)": ["3. Weinstein Stage 2 Long"],

        # 2. Core Certified Portfolio
        "A+B+C (Core Robustness Trio)": [
            "8. Sector Top-Down",
            "5. Momentum Burst",
            "3. Weinstein Stage 2 Long"
        ],

        # 3. Champion + Failed Candidate Pairwise
        "A + D (TopDown + Velez)": ["8. Sector Top-Down", "1. Velez 20/200 Reversal"],
        "A + E (TopDown + Donchian)": ["8. Sector Top-Down", "2. G3-B5 Donchian Trend"],
        "A + F (TopDown + Connors)": ["8. Sector Top-Down", "6. Connors Mean-Rev"],
        "A + G (TopDown + Stage 4 Short)": ["8. Sector Top-Down", "4. Weinstein Stage 4 Short"],
        "A + H (TopDown + Confluence)": ["8. Sector Top-Down", "7. Multi-Layer Confluence"],

        # 4. Core Trio + Failed Candidate Overlays
        "A+B+C + D (Core + Velez)": [
            "8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long",
            "1. Velez 20/200 Reversal"
        ],
        "A+B+C + E (Core + Donchian)": [
            "8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long",
            "2. G3-B5 Donchian Trend"
        ],
        "A+B+C + F (Core + Connors)": [
            "8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long",
            "6. Connors Mean-Rev"
        ],
        "A+B+C + G (Core + Stage 4 Short)": [
            "8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long",
            "4. Weinstein Stage 4 Short"
        ],
        "A+B+C + H (Core + Confluence)": [
            "8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long",
            "7. Multi-Layer Confluence"
        ],

        # 5. Full Composite
        "Full Universe (All 8 Engines)": list(te.EXPERTS.keys())
    }

    results = []
    for label, experts in configs.items():
        m, trades, eq_df = run_governed_ensemble(experts, dfs, max_slots=3)
        gate = "✅ CERTIFIED" if (m["pf"] >= 1.65 and m["max_dd"] <= 3.50 and m["return_pct"] >= 15.0) else "⚠️ PASSABLE" if m["pf"] >= 1.40 else "❌ REJECT"
        results.append({
            "Configuration": label,
            "Trades": m["trades"],
            "Win Rate": f"{m['win_rate']:.1f}%",
            "PF": f"{m['pf']:.2f}",
            "Mean R": f"{m['mean_r']:+.3f}R",
            "Net PnL": f"${m['net_pnl']:+,.2f}",
            "Return": f"{m['return_pct']:+.2f}%",
            "MTM MaxDD": f"{m['max_dd']:.2f}%",
            "Gate": gate
        })

    res_df = pd.DataFrame(results)
    print(res_df.to_string(index=False))
    print("=" * 122)
    print("Phase 3 Governance Gate: PF >= 1.65 | MTM Max DD <= 3.50% | Return >= +15.00% | Full Settle")
    print("=" * 122)

if __name__ == "__main__":
    main()
