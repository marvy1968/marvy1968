import pandas as pd
import numpy as np
from backtest_pyramid_variants import load_data, print_metrics, TradeRecord, EVAL_START, EVAL_END, FRICTION_BPS, SYMBOLS

def run_pullback_simulation(dfs, tp_multiple=1.0):
    all_dates = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    eval_dates = [d for d in all_dates if pd.Timestamp(EVAL_START) <= d <= pd.Timestamp(EVAL_END)]

    completed_trades = []
    active_positions = {}
    pending_orders = {}  # sym -> {limit_px, stop_px, r_dist, dollar_risk, expiry}
    
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

            # Check Target vs Stop
            hit_tp = h >= pos["tp_px"]
            hit_sl = l <= pos["sl_px"]

            if hit_tp or hit_sl:
                # Conservative intra-bar fill priority
                if hit_tp and not hit_sl:
                    exit_px = pos["tp_px"] * (1.0 - FRICTION_BPS)
                    reason = "TAKE_PROFIT"
                elif hit_sl and not hit_tp:
                    exit_px = pos["sl_px"] * (1.0 - FRICTION_BPS)
                    reason = "STOP_LOSS"
                else:
                    # Both crossed in same bar: assume stop hit first
                    exit_px = pos["sl_px"] * (1.0 - FRICTION_BPS)
                    reason = "STOP_LOSS_CONCURRENT"

                pnl = (exit_px - pos["entry_px"]) * pos["units"]
                cash += pnl
                r_mult = pnl / pos["dollar_risk"]

                completed_trades.append(TradeRecord(
                    symbol=sym,
                    entry_date=pos["entry_date"],
                    exit_date=dt_str,
                    lots_added=0,
                    net_pnl=pnl,
                    r_multiple=r_mult,
                    exit_reason=reason
                ))
                closed_syms.append(sym)

        for s in closed_syms:
            del active_positions[s]

        # ---------------------------------------------------------
        # 2. EVALUATE PENDING LIMIT ORDERS (Dip to -1.0R)
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

            # Price drops into the -1.0R buy limit
            if l <= order["limit_px"]:
                fill_px = min(o, order["limit_px"]) * (1.0 + FRICTION_BPS)
                units = int(order["dollar_risk"] / order["r_dist"])
                if units >= 1:
                    active_positions[sym] = {
                        "entry_date": dt_str,
                        "entry_px": fill_px,
                        "tp_px": fill_px + (tp_multiple * order["r_dist"]),
                        "sl_px": fill_px - order["r_dist"],
                        "r_dist": order["r_dist"],
                        "units": units,
                        "dollar_risk": order["dollar_risk"]
                    }
                    filled_syms.append(sym)

        for s in filled_syms:
            del pending_orders[s]

        # Expire stale limit orders older than 10 sessions
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

            # Indicators
            ema20 = float(df["close"].iloc[idx-20:idx].ewm(span=20).mean().iloc[-1])
            sma50_series = df["close"].iloc[idx-55:idx].rolling(50).mean()
            sma50 = float(sma50_series.iloc[-1])
            sma50_prev = float(sma50_series.iloc[-5])
            high_20d = float(df["high"].iloc[idx-20:idx].max())

            # 1. Strong Trend Criteria: Price > EMA20 > SMA50 AND SMA50 sloping up
            strong_trend = (close > ema20) and (ema20 > sma50) and (sma50 > sma50_prev)

            # 2. Fresh high established
            if strong_trend and high >= high_20d:
                atr = float(cur_bar["atr14"])
                if pd.isna(atr) or atr <= 0:
                    continue

                r_dist = 1.5 * atr
                buy_limit = high - r_dist  # Buy at -1.0R from the high

                # Ensure limit price sits above current 50 SMA (pullback remains inside trend)
                if buy_limit > sma50:
                    pending_orders[sym] = {
                        "created_dt": dt_str,
                        "limit_px": buy_limit,
                        "r_dist": r_dist,
                        "dollar_risk": 500.0  # 0.50% NAV
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

    for tp in [1.0, 1.5, 2.0]:
        trades, eq = run_pullback_simulation(dfs, tp_multiple=tp)
        print_metrics(trades, eq, f"STRONG TREND PULLBACK (Buy at -1.0R Dip | TP: +{tp:.1f}R)")
