import pandas as pd
import numpy as np
from backtest_pyramid_variants import load_data, print_metrics, TradeRecord, EVAL_START, EVAL_END, FRICTION_BPS, SYMBOLS

def run_simulation_outside_noise(dfs):
    all_dates = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    eval_dates = [d for d in all_dates if pd.Timestamp(EVAL_START) <= d <= pd.Timestamp(EVAL_END)]

    completed_trades = []
    active_positions = {}
    
    cash = 100_000.0
    equity_curve = []

    for dt in eval_dates:
        dt_str = str(dt.date())

        closed_syms = []
        for sym, pos in active_positions.items():
            df = dfs[sym]
            if dt not in df.index:
                continue

            bar = df.loc[dt]
            o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
            r_dist = pos["r_dist"]

            # Update Peak High
            if h > pos["peak_px"]:
                pos["peak_px"] = h

            current_mfe_r = (pos["peak_px"] - pos["entry_px"]) / r_dist

            # -------------------------------------------------------------
            # 1. OUTSIDE NOISE SCALE-IN (+1.5R = 2.25 ATR)
            # -------------------------------------------------------------
            if not pos["scaled_in"] and current_mfe_r >= 1.5:
                # Add 0.5x original units
                add_units = int(pos["initial_units"] * 0.5)
                pos["units_lot2"] = add_units
                pos["entry_px_lot2"] = pos["entry_px"] + (1.5 * r_dist)
                
                # Ratchet combined stop to +0.5R (Guarantees zero net risk)
                pos["stop_px"] = pos["entry_px"] + (0.5 * r_dist)
                pos["scaled_in"] = True

            # -------------------------------------------------------------
            # 2. OUTSIDE NOISE SCALE-OUT (+3.0R = 4.50 ATR)
            # -------------------------------------------------------------
            if pos["scaled_in"] and not pos["scaled_out"] and current_mfe_r >= 3.0:
                tot_units = pos["units_lot1"] + pos["units_lot2"]
                harvest_qty = tot_units // 2
                harvest_px = (pos["entry_px"] + 3.0 * r_dist) * (1.0 - FRICTION_BPS)
                
                # Bank cash from harvested half
                pnl_harvested = (harvest_px - pos["entry_px"]) * (harvest_qty - pos["units_lot2"]) + \
                                (harvest_px - pos["entry_px_lot2"]) * pos["units_lot2"]
                cash += harvest_qty * harvest_px
                pos["harvested_pnl"] += pnl_harvested
                
                # Remaining units run with the trend
                pos["units_lot1"] = tot_units - harvest_qty
                pos["units_lot2"] = 0
                pos["stop_px"] = max(pos["stop_px"], pos["entry_px"] + (1.5 * r_dist))
                pos["scaled_out"] = True

            # -------------------------------------------------------------
            # 3. EXITS (Hard Stop vs. 20-Day Donchian Channel Low)
            # -------------------------------------------------------------
            stop_hit = l <= pos["stop_px"]
            ch_exit = c < bar["low_20"] and not stop_hit

            if stop_hit or ch_exit:
                exit_px = pos["stop_px"] * (1.0 - FRICTION_BPS) if stop_hit else c * (1.0 - FRICTION_BPS)
                reason = "STOP_TRAIL" if stop_hit else "CH_EXIT_20D"

                pnl_lot1 = (exit_px - pos["entry_px"]) * pos["units_lot1"]
                pnl_lot2 = (exit_px - pos["entry_px_lot2"]) * pos["units_lot2"] if pos["units_lot2"] > 0 else 0.0
                total_pnl = pnl_lot1 + pnl_lot2 + pos["harvested_pnl"]

                cash += (pos["units_lot1"] + pos["units_lot2"]) * exit_px
                r_mult = total_pnl / pos["dollar_risk"]

                completed_trades.append(TradeRecord(
                    symbol=sym,
                    entry_date=pos["entry_date"],
                    exit_date=dt_str,
                    lots_added=1 if pos["scaled_in"] else 0,
                    net_pnl=total_pnl,
                    r_multiple=r_mult,
                    exit_reason=reason
                ))
                closed_syms.append(sym)

        for s in closed_syms:
            del active_positions[s]

        # -------------------------------------------------------------
        # 4. NEW ENTRIES (Donchian 55, Max 3 Concurrent)
        # -------------------------------------------------------------
        for sym in SYMBOLS:
            if sym in active_positions or len(active_positions) >= 3:
                continue
            df = dfs.get(sym)
            if df is None or dt not in df.index:
                continue

            idx = df.index.get_loc(dt)
            if idx < 1:
                continue

            cur_bar = df.iloc[idx]
            prev_bar = df.iloc[idx - 1]

            if cur_bar["close"] > cur_bar["high_55"] and prev_bar["close"] <= prev_bar["high_55"]:
                entry_fill = float(cur_bar["open"]) * (1.0 + FRICTION_BPS)
                atr = float(cur_bar["atr14"])
                if pd.isna(atr) or atr <= 0:
                    continue

                r_dist = 1.5 * atr
                dollar_risk = 500.0  # 0.50% NAV
                units = int(dollar_risk / r_dist)
                if units < 1:
                    continue

                cash -= units * entry_fill
                active_positions[sym] = {
                    "entry_date": dt_str,
                    "entry_px": entry_fill,
                    "peak_px": entry_fill,
                    "stop_px": entry_fill - r_dist,
                    "r_dist": r_dist,
                    "initial_units": units,
                    "units_lot1": units,
                    "units_lot2": 0,
                    "entry_px_lot2": 0.0,
                    "dollar_risk": dollar_risk,
                    "scaled_in": False,
                    "scaled_out": False,
                    "harvested_pnl": 0.0
                }

        # Daily NAV Snapshot
        mtm_unrealized = 0.0
        for s, pos in active_positions.items():
            s_df = dfs[s]
            cur_px = float(s_df.loc[dt]["close"]) if dt in s_df.index else pos["entry_px"]
            mtm_unrealized += (cur_px - pos["entry_px"]) * pos["units_lot1"]
            if pos["units_lot2"] > 0:
                mtm_unrealized += (cur_px - pos["entry_px_lot2"]) * pos["units_lot2"]

        equity_curve.append({"date": dt_str, "nav": cash + mtm_unrealized})

    return completed_trades, pd.DataFrame(equity_curve)

if __name__ == "__main__":
    dfs = load_data()
    trades, eq = run_simulation_outside_noise(dfs)
    print_metrics(trades, eq, "ENGINEERED OUTSIDE-NOISE (Add at +1.5R / Half-Out at +3.0R)")
