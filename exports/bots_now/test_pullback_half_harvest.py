import pandas as pd
import numpy as np
from backtest_pyramid_variants import load_data, print_metrics, TradeRecord, EVAL_START, EVAL_END, FRICTION_BPS, SYMBOLS

def run_half_harvest_simulation(dfs):
    all_dates = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    eval_dates = [d for d in all_dates if pd.Timestamp(EVAL_START) <= d <= pd.Timestamp(EVAL_END)]

    completed_trades = []
    active_positions = {}
    pending_orders = {}
    
    cash = 100_000.0
    equity_curve = []

    for dt in eval_dates:
        dt_str = str(dt.date())

        # ---------------------------------------------------------
        # 1. EVALUATE ACTIVE POSITIONS
        # ---------------------------------------------------------
        closed_syms = []
        for sym, pos in active_positions.items():
            df = dfs[sym]
            if dt not in df.index:
                continue

            bar = df.loc[dt]
            o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
            low_20 = float(bar["low_20"])

            # A. Check for Half-Harvest at +1.5R
            if not pos["harvested"]:
                if h >= pos["tp_px"]:
                    half_units = pos["units"] // 2
                    if half_units > 0:
                        harvest_px = pos["tp_px"] * (1.0 - FRICTION_BPS)
                        pnl_harvest = (harvest_px - pos["entry_px"]) * half_units
                        cash += pnl_harvest
                        pos["harvested_pnl"] += pnl_harvest
                        pos["units"] -= half_units
                        pos["harvested"] = True
                        # Ratchet remaining stop to Breakeven
                        pos["sl_px"] = max(pos["sl_px"], pos["entry_px"] * (1.0 + FRICTION_BPS))

            # B. Check Exits for Active Units
            # Hard stop check
            stop_hit = l <= pos["sl_px"]
            # 20-day Channel Exit check (for remaining runner)
            ch_exit = (pos["harvested"] and c < low_20 and not stop_hit)

            if stop_hit or ch_exit:
                exit_px = pos["sl_px"] * (1.0 - FRICTION_BPS) if stop_hit else c * (1.0 - FRICTION_BPS)
                reason = "STOP_OUT" if stop_hit else "CH_EXIT_20D"

                remaining_pnl = (exit_px - pos["entry_px"]) * pos["units"]
                total_pnl = remaining_pnl + pos["harvested_pnl"]
                cash += remaining_pnl
                r_mult = total_pnl / pos["dollar_risk"]

                completed_trades.append(TradeRecord(
                    symbol=sym,
                    entry_date=pos["entry_date"],
                    exit_date=dt_str,
                    lots_added=1 if pos["harvested"] else 0,
                    net_pnl=total_pnl,
                    r_multiple=r_mult,
                    exit_reason=reason
                ))
                closed_syms.append(sym)

        for s in closed_syms:
            del active_positions[s]

        # ---------------------------------------------------------
        # 2. EVALUATE PENDING LIMIT ORDERS (-1.0R Dip Fill)
        # ---------------------------------------------------------
        filled_syms = []
        for sym, order in pending_orders.items():
            if sym in active_positions or len(active_positions) >= 3:
                continue
            df = dfs[sym]
            if dt not in df.index:
                continue

            bar = df.loc[dt]
            l, o = float(bar["low"]), float(bar["open"])

            if l <= order["limit_px"]:
                fill_px = min(o, order["limit_px"]) * (1.0 + FRICTION_BPS)
                units = int(order["dollar_risk"] / order["r_dist"])
                if units >= 2:  # Must have at least 2 shares to split
                    active_positions[sym] = {
                        "entry_date": dt_str,
                        "entry_px": fill_px,
                        "tp_px": fill_px + (1.5 * order["r_dist"]),
                        "sl_px": fill_px - order["r_dist"],
                        "r_dist": order["r_dist"],
                        "units": units,
                        "dollar_risk": order["dollar_risk"],
                        "harvested": False,
                        "harvested_pnl": 0.0
                    }
                    filled_syms.append(sym)

        for s in filled_syms:
            del pending_orders[s]

        expired = [s for s, o in pending_orders.items() if (dt - pd.to_datetime(o["created_dt"])).days > 15]
        for s in expired:
            del pending_orders[s]

        # ---------------------------------------------------------
        # 3. IDENTIFY NEW STRONG TREND SETUPS
        # ---------------------------------------------------------
        for sym in SYMBOLS:
            if sym in active_positions or sym in pending_orders or len(active_positions) >= 3:
                continue
            df = dfs.get(sym)
            if df is None or dt not in df.index:
                continue

            idx = df.index.get_loc(dt)
            if idx < 50:
                continue

            cur_bar = df.iloc[idx]
            close = float(cur_bar["close"])
            high = float(cur_bar["high"])

            ema20 = float(df["close"].iloc[idx-20:idx].ewm(span=20).mean().iloc[-1])
            sma50_series = df["close"].iloc[idx-55:idx].rolling(50).mean()
            sma50 = float(sma50_series.iloc[-1])
            sma50_prev = float(sma50_series.iloc[-5])
            high_20d = float(df["high"].iloc[idx-20:idx].max())

            strong_trend = (close > ema20) and (ema20 > sma50) and (sma50 > sma50_prev)

            if strong_trend and high >= high_20d:
                atr = float(cur_bar["atr14"])
                if pd.isna(atr) or atr <= 0:
                    continue

                r_dist = 1.5 * atr
                buy_limit = high - r_dist

                if buy_limit > sma50:
                    pending_orders[sym] = {
                        "created_dt": dt_str,
                        "limit_px": buy_limit,
                        "r_dist": r_dist,
                        "dollar_risk": 500.0
                    }

        # Daily NAV Snapshot
        mtm_unrealized = 0.0
        for s, pos in active_positions.items():
            s_df = dfs[s]
            cur_px = float(s_df.loc[dt]["close"]) if dt in s_df.index else pos["entry_px"]
            mtm_unrealized += (cur_px - pos["entry_px"]) * pos["units"]

        equity_curve.append({"date": dt_str, "nav": cash + mtm_unrealized})

    return completed_trades, pd.DataFrame(equity_curve)

if __name__ == "__main__":
    dfs = load_data()
    trades, eq = run_half_harvest_simulation(dfs)
    print_metrics(trades, eq, "OPTIMIZED TREND PULLBACK: HARVEST 50% AT +1.5R + TRAIL RUNNER")
