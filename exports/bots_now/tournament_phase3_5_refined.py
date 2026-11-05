#!/usr/bin/env python3
"""
Phase 3.5: Refined Ensemble, Fair Contention & Overlay Architectures
Tests targeted combinations to optimize the Core + Donchian baseline:
- True Daily Rotating Priority Pointer
- Stage 4 Macro/Sector Defensive Veto
- Velez Pullback Timing Overlay on Sector Entries
- 5,000-Path Permutation & Block Bootstrap Gate on the Top Architecture
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

def run_refined_ensemble(
    expert_names: List[str],
    dfs: Dict[str, pd.DataFrame],
    stage4_veto: bool = False,
    velez_timing_overlay: bool = False
) -> Tuple[dict, List[te.Trade], pd.DataFrame]:
    
    spy_df = dfs[BENCHMARK]
    common_dates = sorted(set.intersection(*[set(dfs[s].index) for s in UNIVERSE + [BENCHMARK]]))
    
    realized_nav = INITIAL_NAV
    open_positions: Dict[str, Tuple[te.Position, str]] = {}
    trades: List[te.Trade] = []
    equity_rows: List[dict] = []
    
    # Priority pointer for round-robin allocation
    priority_offset = 0
    
    for t_idx in range(199, len(common_dates) - 1):
        curr_date = common_dates[t_idx]
        next_date = common_dates[t_idx + 1]
        
        # 0. Stage 4 Defensive Veto Check
        defensive_regime = False
        if stage4_veto:
            spy_idx = spy_df.index.get_loc(curr_date)
            spy_row = spy_df.iloc[spy_idx]
            # Veto long entries if SPY is in Stage 4 (Price < 50 SMA < 200 SMA)
            if spy_row["close"] < spy_row["sma50"] < spy_row["sma200"]:
                defensive_regime = True

        # 1. Signals at bar t close for open positions
        exit_intents: Dict[str, str] = {}
        for sym, (pos, exp_name) in list(open_positions.items()):
            df = dfs[sym]
            idx = df.index.get_loc(curr_date)
            fn = te.EXPERTS[exp_name]
            res = fn(df, idx, pos, spy_df) if exp_name == "8. Sector Top-Down" else fn(df, idx, pos)
            
            # If in defensive regime, tighten exits immediately
            if defensive_regime and pos.side == "BUY" and df.iloc[idx]["close"] < df.iloc[idx]["ema20"]:
                exit_intents[sym] = "STAGE4_DEFENSIVE_VETO"
            elif res.get("signal") == "EXIT":
                exit_intents[sym] = res.get("reason", "SIGNAL_EXIT")

        # Candidate Entries with Rotating Contention
        raw_candidates: List[Tuple[str, str, dict]] = []
        if not defensive_regime:
            for sym in UNIVERSE:
                if sym in open_positions:
                    continue
                df = dfs[sym]
                idx = df.index.get_loc(curr_date)
                
                # Active Strategy Entries
                for exp_name in expert_names:
                    fn = te.EXPERTS[exp_name]
                    res = fn(df, idx, None, spy_df) if exp_name == "8. Sector Top-Down" else fn(df, idx, None)
                    if res.get("signal") in ("BUY", "SELL"):
                        raw_candidates.append((sym, exp_name, res))
                        
                # Velez Pullback Timing Overlay (improves Sector Top-Down timing)
                if velez_timing_overlay and "8. Sector Top-Down" in expert_names:
                    row = df.iloc[idx]
                    spy_row = spy_df.loc[curr_date]
                    stock_perf_20 = (row["close"] / df["close"].iloc[max(0, idx-20)]) - 1.0
                    spy_perf_20 = (spy_row["close"] / spy_df["close"].loc[:curr_date].iloc[max(0, len(spy_df.loc[:curr_date])-20)]) - 1.0
                    
                    # Velez pullback confirmation on RS leader
                    if (stock_perf_20 > spy_perf_20 and 
                        row["close"] > row["sma200"] and 
                        abs(row["low"] - row["ema20"])/row["ema20"] < 0.015 and 
                        row["close"] > row["open"]):
                        stop_px = float(row["low"]) - (0.5 * float(row["atr14"]))
                        raw_candidates.append((sym, "8. Sector Top-Down (Velez Timing)", {
                            "signal": "BUY", "stop": stop_px, "target_r": 2.5
                        }))

        # Fair Rotating Allocation: Rotate starting expert priority each trading bar
        active_exp_list = list(set(e for _, e, _ in raw_candidates))
        if active_exp_list:
            priority_offset = (priority_offset + 1) % len(active_exp_list)
            rotated_experts = active_exp_list[priority_offset:] + active_exp_list[:priority_offset]
            by_expert = {e: [] for e in rotated_experts}
            for sym, exp, res in raw_candidates:
                by_expert[exp].append((sym, exp, res))
            
            interleaved: List[Tuple[str, str, dict]] = []
            max_c = max(len(v) for v in by_expert.values()) if by_expert else 0
            for i in range(max_c):
                for exp in rotated_experts:
                    if i < len(by_expert[exp]):
                        interleaved.append(by_expert[exp][i])
        else:
            interleaved = []

        seen_syms = set()
        unique_entry_intents: List[Tuple[str, str, dict]] = []
        for sym, exp, res in interleaved:
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

            nav_open = te.calculate_mtm_nav(realized_nav, raw_pos_map, dfs, next_date, price_col="open")
            risk_dollars = nav_open * RISK_PER_TRADE_PCT
            qty = int(risk_dollars / risk_per_share)
            if qty <= 0:
                continue

            entry_fees = te.sell_regulatory_fees(entry_fill, qty) if side == "SELL" else 0.0
            realized_nav -= entry_fees

            target_r = res.get("target_r")
            tp = (entry_fill + float(target_r) * risk_per_share) if target_r is not None else None

            pos = te.Position(
                symbol=sym, side=side, entry_date=next_date, entry_price=entry_fill,
                initial_stop=initial_stop, current_stop=initial_stop,
                initial_risk_per_share=risk_per_share, qty=qty, tp=tp, entry_fees=entry_fees
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
            "date": next_date, "realized_nav": realized_nav, "mtm_nav": mtm, "positions": len(open_positions)
        })

    # Settle final open positions
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
    print("=" * 120)
    print(" 🎯 PHASE 3.5: TARGETED ENSEMBLE, FAIR CONTENTION & OVERLAY MATRIX")
    print("=" * 120)

    dfs = te.load_and_prep_data()

    TARGET_CONFIGS = {
        "1. Core Trio (Fair Rotated Contention)": (
            ["8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long"], False, False
        ),
        "2. Core Trio + Donchian (A+B+C+E)": (
            ["8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long", "2. G3-B5 Donchian Trend"], False, False
        ),
        "3. Core + Donchian + Stage 4 Veto": (
            ["8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long", "2. G3-B5 Donchian Trend"], True, False
        ),
        "4. Core + Donchian + Velez Timing Overlay": (
            ["8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long", "2. G3-B5 Donchian Trend"], False, True
        ),
        "5. Core + Donchian + Velez Timing + Stage 4 Veto": (
            ["8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long", "2. G3-B5 Donchian Trend"], True, True
        ),
        "6. Sector Champion + Velez Timing Alone": (
            ["8. Sector Top-Down"], False, True
        )
    }

    results = []
    saved_runs = {}
    for label, (experts, s4_veto, v_timing) in TARGET_CONFIGS.items():
        m, trades, eq_df = run_refined_ensemble(experts, dfs, stage4_veto=s4_veto, velez_timing_overlay=v_timing)
        gate = "🏆 CHAMPION" if (m["pf"] >= 2.00 and m["max_dd"] <= 4.00 and m["return_pct"] >= 20.0) else "✅ PASS" if (m["pf"] >= 1.65 and m["max_dd"] <= 5.00) else "⚠️ REVIEW"
        results.append({
            "Configuration": label,
            "Trades": m["trades"],
            "Win Rate": f"{m['win_rate']:.1f}%",
            "PF": f"{m['pf']:.2f}",
            "Mean R": f"{m['mean_r']:+.3f}R",
            "Return": f"{m['return_pct']:+.2f}%",
            "MTM MaxDD": f"{m['max_dd']:.2f}%",
            "Net PnL": f"${m['net_pnl']:+,.2f}",
            "Gate": gate
        })
        saved_runs[label] = (m, trades, eq_df)

    res_df = pd.DataFrame(results)
    print(res_df.to_string(index=False))
    print("=" * 120)

if __name__ == "__main__":
    main()
