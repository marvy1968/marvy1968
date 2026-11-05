#!/usr/bin/env python3
"""
Continuous OOS Comparative Audit: Static Beta vs. Adaptive Closed-Loop Beta
Evaluates whether dynamic risk throttling and symbol penalty boxes improve
drawdown smoothing and capital efficiency across the 329-day OOS window.
"""

import os
import json
import numpy as np
import pandas as pd
import run_stitched_oos_engine as eng
from adaptive_learning_engine_v2 import AdaptiveClosedLoopManager, DATA_DIR, BASE_RISK_PER_TRADE

COMP_LEDGER = os.path.join(DATA_DIR, "COMP_adaptive_ledger.json")
COMP_POLICY = os.path.join(DATA_DIR, "COMP_adaptive_policy.json")

for p in [COMP_LEDGER, COMP_POLICY]:
    if os.path.exists(p):
        os.remove(p)

def run_simulation(manifest, dfs, oos_dates, use_adaptive=False):
    if use_adaptive:
        manager = AdaptiveClosedLoopManager(ledger_path=COMP_LEDGER, policy_path=COMP_POLICY)
    
    btc_df = dfs["BTC/USD"]
    cash = eng.INITIAL_CAPITAL
    positions = {}
    trade_logs = []
    equity_curve = []
    
    fee_rate = 0.0010
    slip_rate = 0.0010

    for i in range(1, len(oos_dates)):
        dt = oos_dates[i]
        prev_dt = oos_dates[i - 1]
        dt_str = dt.strftime("%Y-%m-%d")

        if prev_dt not in btc_df.index or dt not in btc_df.index:
            continue

        btc_prev = btc_df.loc[prev_dt]
        if pd.isna(btc_prev["ema20"]) or pd.isna(btc_prev["sma50"]):
            continue

        macro_bull = bool(btc_prev["close"] > btc_prev["ema20"] > btc_prev["sma50"])

        # 1. Open Exits
        open_closed = []
        for sym, pos in list(positions.items()):
            df = dfs[sym]
            if dt not in df.index:
                continue
            row = df.loc[dt]
            open_px = float(row["open"])

            exit_px = None
            reason = None

            if not macro_bull:
                exit_px = open_px * (1.0 - slip_rate)
                reason = "macro_exit"
            elif open_px <= pos["stop_px"]:
                exit_px = open_px * (1.0 - slip_rate)
                reason = "gap_stop"

            if exit_px is not None:
                gross = pos["qty"] * exit_px
                fee = gross * fee_rate
                net = gross - fee
                cash += net
                pnl = net - (pos["entry_notional"] + pos["entry_fee"])
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                record = {
                    "symbol": sym, "timestamp": pos["entry_dt"].strftime("%Y-%m-%d"),
                    "entry_px": pos["entry_px"], "exit_dt": dt_str,
                    "exit_px": exit_px, "realized_pnl": pnl, "r_multiple": r_mult,
                    "win": pnl > 0, "btc_momentum_bucket": pos["btc_bucket"],
                    "volatility_bucket": pos["vol_bucket"]
                }
                trade_logs.append(record)
                if use_adaptive:
                    manager.record_completed_trade(record)
                open_closed.append(sym)

        for sym in open_closed:
            del positions[sym]

        nav_at_open = cash
        for sym, pos in positions.items():
            df = dfs[sym]
            px = float(df.loc[dt, "open"]) if dt in df.index else pos["last_px"]
            nav_at_open += pos["qty"] * px

        # 2. Open Entries
        if macro_bull and len(positions) < 3:
            candidates = []
            for sym in manifest["symbol"]:
                if sym in positions:
                    continue
                if use_adaptive and not manager.is_symbol_eligible(sym, dt_str):
                    continue
                df = dfs[sym]
                if prev_dt not in df.index or dt not in df.index:
                    continue
                prev = df.loc[prev_dt]
                today = df.loc[dt]

                req = [prev.get("med_vol_30d"), prev.get("donch_high_30"), prev.get("atr14")]
                if any(pd.isna(x) for x in req) or float(prev["med_vol_30d"]) < 250_000:
                    continue

                breakout_level = float(prev["donch_high_30"])
                if float(prev["close"]) > breakout_level:
                    strength = (float(prev["close"]) / breakout_level) - 1.0
                    candidates.append((strength, sym, prev, today))

            candidates.sort(key=lambda x: x[0], reverse=True)

            for strength, sym, prev, today in candidates:
                if len(positions) >= 3:
                    break

                raw_entry = float(today["open"])
                entry_px = raw_entry * (1.0 + slip_rate)
                atr_val = float(prev["atr14"])
                init_risk_prs = 2.0 * atr_val
                if init_risk_prs <= 0 or entry_px <= init_risk_prs:
                    continue

                stop_px = entry_px - init_risk_prs
                
                # Classify regime buckets for telemetry & dynamic sizing
                btc_slope = (btc_prev["close"] / btc_prev["sma50"]) - 1.0
                btc_bucket = "STRONG_BULL" if btc_slope > 0.05 else "WEAK_BULL"
                vol_bucket = "HIGH_VOL" if (atr_val / float(prev["close"])) > 0.08 else "NORM_VOL"
                
                telemetry = {"btc_momentum_bucket": btc_bucket, "volatility_bucket": vol_bucket}
                risk_pct = manager.get_effective_risk_pct(telemetry) if use_adaptive else BASE_RISK_PER_TRADE
                
                dollar_risk = nav_at_open * risk_pct
                qty = min(dollar_risk / init_risk_prs, (nav_at_open * 0.40) / entry_px, (cash / (1.0 + fee_rate)) / entry_px)
                if qty <= 0:
                    continue

                entry_notional = qty * entry_px
                entry_fee = entry_notional * fee_rate
                if (entry_notional + entry_fee) > cash:
                    continue

                cash -= (entry_notional + entry_fee)
                positions[sym] = {
                    "entry_dt": dt, "entry_px": entry_px, "entry_notional": entry_notional,
                    "entry_fee": entry_fee, "qty": qty, "initial_risk_prs": init_risk_prs,
                    "stop_px": stop_px, "last_px": float(today["open"]),
                    "btc_bucket": btc_bucket, "vol_bucket": vol_bucket
                }

        # 3. Intraday Stops
        intraday_closed = []
        for sym, pos in list(positions.items()):
            df = dfs[sym]
            if dt not in df.index:
                continue
            row = df.loc[dt]
            if float(row["low"]) <= pos["stop_px"]:
                exit_px = pos["stop_px"] * (1.0 - slip_rate)
                gross = pos["qty"] * exit_px
                fee = gross * fee_rate
                net = gross - fee
                cash += net
                pnl = net - (pos["entry_notional"] + pos["entry_fee"])
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                record = {
                    "symbol": sym, "timestamp": pos["entry_dt"].strftime("%Y-%m-%d"),
                    "entry_px": pos["entry_px"], "exit_dt": dt_str,
                    "exit_px": exit_px, "realized_pnl": pnl, "r_multiple": r_mult,
                    "win": pnl > 0, "btc_momentum_bucket": pos["btc_bucket"],
                    "volatility_bucket": pos["vol_bucket"]
                }
                trade_logs.append(record)
                if use_adaptive:
                    manager.record_completed_trade(record)
                intraday_closed.append(sym)

        for sym in intraday_closed:
            del positions[sym]

        # 4. Close MTM
        nav_at_close = cash
        for sym, pos in positions.items():
            df = dfs[sym]
            if dt in df.index:
                row = df.loc[dt]
                close_px = float(row["close"])
                pos["last_px"] = close_px
                atr_now = row["atr14"]
                if pd.notna(atr_now):
                    cand_stop = close_px - (2.0 * float(atr_now))
                    if cand_stop > pos["stop_px"]:
                        pos["stop_px"] = cand_stop
                nav_at_close += pos["qty"] * close_px
            else:
                nav_at_close += pos["qty"] * pos["last_px"]

        equity_curve.append((dt, nav_at_close))

    # Terminal
    terminal_dt = equity_curve[-1][0]
    for sym in list(positions.keys()):
        pos = positions[sym]
        exit_px = pos["last_px"] * (1.0 - slip_rate)
        gross = pos["qty"] * exit_px
        net = gross - (gross * fee_rate)
        cash += net
        pnl = net - (pos["entry_notional"] + pos["entry_fee"])
        init_risk = pos["qty"] * pos["initial_risk_prs"]
        trade_logs.append({
            "symbol": sym, "timestamp": pos["entry_dt"].strftime("%Y-%m-%d"),
            "entry_px": pos["entry_px"], "exit_dt": terminal_dt.strftime("%Y-%m-%d"),
            "exit_px": exit_px, "realized_pnl": pnl, "r_multiple": r_mult,
            "win": pnl > 0, "btc_momentum_bucket": pos["btc_bucket"],
            "volatility_bucket": pos["vol_bucket"]
        })

    positions.clear()
    equity_curve[-1] = (terminal_dt, cash)

    eq = pd.Series([x[1] for x in equity_curve], index=[x[0] for x in equity_curve], dtype=float)
    peak = eq.cummax()
    max_dd = abs(float(((eq / peak) - 1.0).min())) * 100.0
    elapsed_days = (eq.index[-1] - eq.index[0]).total_seconds() / 86400.0
    cagr = (((float(eq.iloc[-1]) / eng.INITIAL_CAPITAL) ** (365.25 / elapsed_days)) - 1.0) * 100.0
    mar = cagr / max_dd if max_dd > 0 else 0.0

    df_t = pd.DataFrame(trade_logs)
    wins = df_t[df_t["win"]] if not df_t.empty else pd.DataFrame()
    losses = df_t[~df_t["win"]] if not df_t.empty else pd.DataFrame()
    pf = (wins["realized_pnl"].sum() / abs(losses["realized_pnl"].sum())) if (not losses.empty and abs(losses["realized_pnl"].sum()) > 0) else np.inf
    exp_r = df_t["r_multiple"].mean() if not df_t.empty else 0.0

    return {
        "terminal_nav": float(eq.iloc[-1]),
        "net_return": ((float(eq.iloc[-1]) / eng.INITIAL_CAPITAL) - 1.0) * 100.0,
        "cagr": cagr,
        "max_dd": max_dd,
        "mar": mar,
        "pf": pf,
        "exp_r": exp_r,
        "trades": len(df_t)
    }

def main():
    print("=" * 105)
    print(" ⚔️ COMPARATIVE AUDIT: STATIC BETA (0.40% FIXED) vs. ADAPTIVE BETA (0.25%-0.50% BOUNDED)")
    print("=" * 105)

    manifest, dfs = eng.load_data()
    btc_df = dfs["BTC/USD"]
    all_dates = btc_df.index[60:]
    oos_dates = all_dates[int(len(all_dates) * 0.50):]

    cohorts = [
        ("CORE_N27 (Ex-HYPE/PUMP/VVV)", manifest[~manifest["symbol"].isin(["HYPE/USD", "PUMP/USD", "VVV/USD"])]),
        ("MATURE_N24 (719-bar Ceiling)", manifest[manifest["hit_720_ceiling"]])
    ]

    rows = []
    for c_name, c_manifest in cohorts:
        res_static = run_simulation(c_manifest, dfs, oos_dates, use_adaptive=False)
        res_adapt = run_simulation(c_manifest, dfs, oos_dates, use_adaptive=True)

        rows.append({
            "Cohort": c_name,
            "Mode": "Static Beta (0.40%)",
            "Net Return": f"{res_static['net_return']:+.2f}%",
            "CAGR": f"{res_static['cagr']:.2f}%",
            "Max DD": f"{res_static['max_dd']:.2f}%",
            "MAR": f"{res_static['mar']:.3f}",
            "PF": f"{res_static['pf']:.2f}",
            "Exp (R)": f"{res_static['exp_r']:+.2f}R",
            "Trades": res_static["trades"]
        })
        rows.append({
            "Cohort": c_name,
            "Mode": "Adaptive Beta (v2)",
            "Net Return": f"{res_adapt['net_return']:+.2f}%",
            "CAGR": f"{res_adapt['cagr']:.2f}%",
            "Max DD": f"{res_adapt['max_dd']:.2f}%",
            "MAR": f"{res_adapt['mar']:.3f}",
            "PF": f"{res_adapt['pf']:.2f}",
            "Exp (R)": f"{res_adapt['exp_r']:+.2f}R",
            "Trades": res_adapt["trades"]
        })

    df_res = pd.DataFrame(rows)
    print(df_res.to_string(index=False))

if __name__ == "__main__":
    main()
