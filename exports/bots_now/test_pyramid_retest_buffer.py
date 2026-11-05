import pandas as pd
from backtest_pyramid_variants import load_data, print_metrics, TradeRecord, EVAL_START, EVAL_END, FRICTION_BPS, SYMBOLS

def run_simulation_buffered(dfs):
    all_dates = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    eval_dates = [d for d in all_dates if pd.Timestamp(EVAL_START) <= d <= pd.Timestamp(EVAL_END)]

    completed_trades = []
    active_positions = {}
    
    cash = 100_000.0
    equity_curve = []

    for dt in eval_dates:
        dt_str = str(dt.date())

        # 1. Update active
        closed_syms = []
        for sym, pos in active_positions.items():
            df = dfs[sym]
            if dt not in df.index:
                continue

            bar = df.loc[dt]
            o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
            r_dist = pos["r_dist"]
            current_r = (h - pos["entry_px"]) / r_dist

            # Add at +1.0R, but ratchet stop to Entry - 0.5R (Buffer against noise)
            if not pos["added_10"] and current_r >= 1.0:
                pos["units_lot2"] = pos["initial_units"]
                pos["entry_px_lot2"] = pos["entry_px"] + r_dist
                pos["stop_px"] = pos["entry_px"] - (0.5 * r_dist)  # Retest buffer
                pos["added_10"] = True

            stop_hit = l <= pos["stop_px"]
            ch_exit = c < bar["low_20"] and not stop_hit

            if stop_hit or ch_exit:
                exit_px = pos["stop_px"] * (1.0 - FRICTION_BPS) if stop_hit else c * (1.0 - FRICTION_BPS)
                reason = "STOP_BUFFERED" if stop_hit else "CH_EXIT_20D"

                pnl_lot1 = (exit_px - pos["entry_px"]) * pos["units_lot1"]
                pnl_lot2 = (exit_px - pos["entry_px_lot2"]) * pos["units_lot2"] if pos["units_lot2"] > 0 else 0.0
                pnl = pnl_lot1 + pnl_lot2

                cash += pnl
                r_mult = pnl / pos["dollar_risk"]

                completed_trades.append(TradeRecord(
                    symbol=sym,
                    entry_date=pos["entry_date"],
                    exit_date=dt_str,
                    lots_added=1 if pos["added_10"] else 0,
                    net_pnl=pnl,
                    r_multiple=r_mult,
                    exit_reason=reason
                ))
                closed_syms.append(sym)

        for s in closed_syms:
            del active_positions[s]

        # 2. Entries
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
                dollar_risk = 500.0
                units = int(dollar_risk / r_dist)
                if units < 1:
                    continue

                active_positions[sym] = {
                    "entry_date": dt_str,
                    "entry_px": entry_fill,
                    "r_dist": r_dist,
                    "initial_units": units,
                    "units_lot1": units,
                    "stop_px": entry_fill - r_dist,
                    "units_lot2": 0,
                    "entry_px_lot2": 0.0,
                    "dollar_risk": dollar_risk,
                    "added_10": False
                }

        # NAV Snapshot
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
    trades, eq = run_simulation_buffered(dfs)
    print_metrics(trades, eq, "4. BUFFERED PYRAMID (+1.0R Add, Stop at Entry - 0.5R)")
