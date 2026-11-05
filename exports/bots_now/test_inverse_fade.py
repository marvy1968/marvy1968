import pandas as pd
import numpy as np
from backtest_pyramid_variants import load_data, print_metrics, TradeRecord, EVAL_START, EVAL_END, FRICTION_BPS, SYMBOLS

def run_inverse_simulation(dfs):
    all_dates = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    eval_dates = [d for d in all_dates if pd.Timestamp(EVAL_START) <= d <= pd.Timestamp(EVAL_END)]

    completed_trades = []
    active_positions = {}
    
    cash = 100_000.0
    equity_curve = []

    for dt in eval_dates:
        dt_str = str(dt.date())

        # 1. Evaluate open trades
        closed_syms = []
        for sym, pos in active_positions.items():
            df = dfs[sym]
            if dt not in df.index:
                continue

            bar = df.loc[dt]
            o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
            r_dist = pos["r_dist"]

            # INVERSE SCALE: Add 0.5x if price dips to -1.0R (DCA into pullbacks)
            if not pos["scaled_in"] and l <= (pos["entry_px"] - r_dist):
                add_units = int(pos["initial_units"] * 0.5)
                pos["units_lot2"] = add_units
                pos["entry_px_lot2"] = pos["entry_px"] - r_dist
                pos["scaled_in"] = True

            # INVERSE HARVEST: Take quick profit at +1.0R or SMA50
            profit_target = pos["entry_px"] + r_dist
            hit_target = h >= profit_target

            # Disaster Stop: Flatten if trade falls through -2.5R from original entry
            disaster_stop = pos["entry_px"] - (2.5 * r_dist)
            hit_stop = l <= disaster_stop

            if hit_target or hit_stop:
                exit_px = profit_target * (1.0 - FRICTION_BPS) if hit_target else disaster_stop * (1.0 - FRICTION_BPS)
                reason = "TAKE_PROFIT_QUICK" if hit_target else "STOP_DISASTER"

                pnl_lot1 = (exit_px - pos["entry_px"]) * pos["units_lot1"]
                pnl_lot2 = (exit_px - pos["entry_px_lot2"]) * pos["units_lot2"] if pos["units_lot2"] > 0 else 0.0
                total_pnl = pnl_lot1 + pnl_lot2

                cash += total_pnl
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

        # 2. Entries: Mean-reversion pullbacks (oversold dip entries)
        for sym in SYMBOLS:
            if sym in active_positions or len(active_positions) >= 3:
                continue
            df = dfs.get(sym)
            if df is None or dt not in df.index:
                continue

            idx = df.index.get_loc(dt)
            if idx < 50:
                continue

            cur_bar = df.iloc[idx]
            prev_bar = df.iloc[idx - 1]
            
            # Simple oversold condition: 3-day pullback into rising SMA50
            sma50 = df["close"].iloc[idx-50:idx].mean()
            if cur_bar["close"] > sma50 and cur_bar["close"] < prev_bar["close"] * 0.98:
                entry_fill = float(cur_bar["open"]) * (1.0 + FRICTION_BPS)
                atr = float(cur_bar["atr14"])
                if pd.isna(atr) or atr <= 0:
                    continue

                r_dist = 1.5 * atr
                dollar_risk = 500.0  # 0.50% NAV
                units = int(dollar_risk / r_dist)
                if units < 1:
                    continue

                active_positions[sym] = {
                    "entry_date": dt_str,
                    "entry_px": entry_fill,
                    "r_dist": r_dist,
                    "initial_units": units,
                    "units_lot1": units,
                    "units_lot2": 0,
                    "entry_px_lot2": 0.0,
                    "dollar_risk": dollar_risk,
                    "scaled_in": False
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
    trades, eq = run_inverse_simulation(dfs)
    print_metrics(trades, eq, "INVERSE ENGINE (DCA on Dip / Quick Scale-Out)")
