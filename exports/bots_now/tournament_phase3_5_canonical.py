#!/usr/bin/env python3
"""
Phase 3.5: Canonical Overlay & Deterministic Fair Contention Harness
Strict Standards:
- Zero dispatch drift: te.EXPERTS keys preserved exclusively
- Correct directional short TP math
- Deterministic index-based rotation (no set hash randomness)
- Exact canonical adapter calls for Velez & Stage 4 overlays
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

def run_canonical_overlay_test(
    expert_names: List[str],
    dfs: Dict[str, pd.DataFrame],
    stage4_per_symbol_veto: bool = False,
    velez_confirmation_mode: Optional[str] = None  # None, "REQUIRE_VELEZ_BUY", "REQUIRE_VELEZ_ACTIVE"
) -> Tuple[dict, List[te.Trade], pd.DataFrame]:
    
    spy_df = dfs[BENCHMARK]
    common_dates = sorted(set.intersection(*[set(dfs[s].index) for s in UNIVERSE + [BENCHMARK]]))
    
    realized_nav = INITIAL_NAV
    open_positions: Dict[str, Tuple[te.Position, str]] = {}
    trades: List[te.Trade] = []
    equity_rows: List[dict] = []
    
    # Deterministic rotation counter
    bar_counter = 0
    
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
            
            # Stage 4 Per-Symbol Veto check
            if stage4_per_symbol_veto:
                s4_res = te.signal_weinstein_stage4_short(df, idx, None)
                if s4_res.get("signal") == "SELL":
                    continue  # Veto all long candidates for this symbol
            
            for exp_name in expert_names:
                fn = te.EXPERTS[exp_name]
                res = fn(df, idx, None, spy_df) if exp_name == "8. Sector Top-Down" else fn(df, idx, None)
                
                # Canonical Velez Permission Overlay on Sector Top-Down
                if exp_name == "8. Sector Top-Down" and res.get("signal") == "BUY":
                    if velez_confirmation_mode in ("REQUIRE_VELEZ_BUY", "REQUIRE_VELEZ_ACTIVE"):
                        v_res = te.signal_velez_reversal(df, idx, None)
                        if v_res.get("signal") != "BUY":
                            continue  # Velez failed to confirm Top-Down entry
                            
                if res.get("signal") in ("BUY", "SELL"):
                    raw_candidates.append((sym, exp_name, res))

        # Deterministic Round-Robin Fair Contention:
        # Rotate starting expert based on static declared order and bar_counter
        n_exp = len(expert_names)
        if n_exp > 0 and raw_candidates:
            rot_offset = bar_counter % n_exp
            rotated_order = expert_names[rot_offset:] + expert_names[:rot_offset]
            
            by_expert = {e: [] for e in rotated_order}
            for sym, exp, res in raw_candidates:
                by_expert[exp].append((sym, exp, res))
                
            interleaved: List[Tuple[str, str, dict]] = []
            max_c = max(len(v) for v in by_expert.values()) if by_expert else 0
            for i in range(max_c):
                for exp in rotated_order:
                    if i < len(by_expert[exp]):
                        interleaved.append(by_expert[exp][i])
        else:
            interleaved = []
            
        bar_counter += 1

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
            tp = None
            if target_r is not None:
                tp = entry_fill + (float(target_r) * risk_per_share) if side == "BUY" else entry_fill - (float(target_r) * risk_per_share)

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
    print(" 🎯 PHASE 3.5: CANONICAL OVERLAY & DETERMINISTIC FAIR CONTENTION SUITE")
    print("=" * 120)

    dfs = te.load_and_prep_data()

    TARGET_CONFIGS = {
        "1. Core Trio (Fair-Rotation Baseline)": (
            ["8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long"], False, None
        ),
        "2. Core Trio + Donchian (Reference A+B+C+E)": (
            ["8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long", "2. G3-B5 Donchian Trend"], False, None
        ),
        "3. ABC+E + Canonical Stage 4 Per-Symbol Veto": (
            ["8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long", "2. G3-B5 Donchian Trend"], True, None
        ),
        "4. ABC+E + Canonical Velez Confirmation": (
            ["8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long", "2. G3-B5 Donchian Trend"], False, "REQUIRE_VELEZ_BUY"
        ),
        "5. ABC+E + Velez Confirmation + Stage 4 Veto": (
            ["8. Sector Top-Down", "5. Momentum Burst", "3. Weinstein Stage 2 Long", "2. G3-B5 Donchian Trend"], True, "REQUIRE_VELEZ_BUY"
        ),
        "6. Sector Champion + Canonical Velez Confirm Alone": (
            ["8. Sector Top-Down"], False, "REQUIRE_VELEZ_BUY"
        )
    }

    results = []
    for label, (experts, s4_veto, v_mode) in TARGET_CONFIGS.items():
        m, trades, eq_df = run_canonical_overlay_test(
            experts, dfs, stage4_per_symbol_veto=s4_veto, velez_confirmation_mode=v_mode
        )
        gate = "🏆 CHAMPION" if (m["pf"] >= 2.15 and m["max_dd"] <= 4.50 and m["return_pct"] >= 20.0) else "✅ PASS" if (m["pf"] >= 1.65 and m["max_dd"] <= 5.00) else "⚠️ REVIEW"
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

    res_df = pd.DataFrame(results)
    print(res_df.to_string(index=False))
    print("=" * 120)
    print("Benchmark Target to Beat: Return >= +21.70% | PF >= 2.20 | Mean R >= +0.511R | MTM Max DD <= 4.91%")
    print("=" * 120)

if __name__ == "__main__":
    main()
