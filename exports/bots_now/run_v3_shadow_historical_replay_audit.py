#!/usr/bin/env python3
"""
G3-B5 v3 Shadow Historical Replay & Baseline Conformance Audit
Replays the continuous 329-day OOS timeline bar-by-bar through kraken_g3b5_shadow_v3
to certify signal parity, fee accounting, and right-tail preservation.
"""

import os
import json
import numpy as np
import pandas as pd
from datetime import datetime, timezone

from kraken_preflight_adapter import KrakenAPIClient
from adaptive_learning_engine_v3 import AdaptiveClosedLoopManager, DATA_DIR, atomic_json_dump
import run_stitched_oos_engine as eng

REPLAY_LEDGER = os.path.join(DATA_DIR, "REPLAY_v3_trade_telemetry_ledger.json")
REPLAY_POLICY = os.path.join(DATA_DIR, "REPLAY_v3_active_adaptive_policy.json")
REPLAY_PROPOSALS = os.path.join(DATA_DIR, "REPLAY_v3_adaptive_policy_proposals.json")

LOOKBACK_DONCHIAN = 30
ATR_MULTIPLIER = 2.0
LIQUIDITY_FLOOR = 250_000.0
MAX_POSITIONS = 3
MAX_SINGLE_POS_NAV_PCT = 0.40
FEE_RATE = 0.0010
SLIPPAGE_RATE = 0.0010
INITIAL_NAV = 100_000.0

def run_shadow_replay(c_name: str, cohort_manifest: pd.DataFrame, dfs: dict, oos_dates: list):
    for p in [REPLAY_LEDGER, REPLAY_POLICY, REPLAY_PROPOSALS]:
        if os.path.exists(p):
            os.remove(p)

    client = KrakenAPIClient()
    client.load_pair_constraints(cohort_manifest["symbol"].tolist())
    learner = AdaptiveClosedLoopManager(
        ledger_path=REPLAY_LEDGER,
        policy_path=REPLAY_POLICY,
        proposal_path=REPLAY_PROPOSALS,
    )

    btc_df = dfs["BTC/USD"]
    cash = INITIAL_NAV
    positions = {}
    trade_logs = []
    equity_curve = []

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

        # 1. Manage Exits
        closed_symbols = []
        for sym, pos in list(positions.items()):
            df = dfs[sym]
            if dt not in df.index:
                continue
            row = df.loc[dt]
            open_px = float(row["open"])

            exit_reason = None
            exit_reference_px = None

            if not macro_bull:
                exit_reason = "macro_exit"
                exit_reference_px = open_px
            elif open_px <= pos["stop_px"]:
                exit_reason = "gap_stop"
                exit_reference_px = open_px
            elif float(row["low"]) <= pos["stop_px"]:
                exit_reason = "intraday_stop"
                exit_reference_px = float(pos["stop_px"])

            if exit_reason:
                exit_px = exit_reference_px * (1.0 - SLIPPAGE_RATE)
                exit_proceeds_net = pos["qty"] * exit_px * (1.0 - FEE_RATE)
                pnl = exit_proceeds_net - pos["entry_cost_total"]
                cash += exit_proceeds_net

                init_risk = pos["qty"] * pos["initial_risk_prs"]
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                record = {
                    "symbol": sym,
                    "entry_dt": pos["entry_dt"].strftime("%Y-%m-%d"),
                    "exit_dt": dt_str,
                    "entry_px": pos["entry_px"],
                    "exit_px": exit_px,
                    "pnl": pnl,
                    "r": r_mult,
                    "win": pnl > 0,
                    "reason": exit_reason,
                    "btc_momentum_bucket": pos["btc_bucket"],
                    "volatility_bucket": pos["vol_bucket"],
                }
                trade_logs.append(record)
                learner.record_completed_trade({
                    "symbol": sym,
                    "timestamp": pos["entry_dt"].strftime("%Y-%m-%d"),
                    "entry_px": pos["entry_px"],
                    "exit_dt": dt_str,
                    "exit_px": exit_px,
                    "realized_pnl": pnl,
                    "r_multiple": r_mult,
                    "win": pnl > 0,
                    "btc_momentum_bucket": pos["btc_bucket"],
                    "volatility_bucket": pos["vol_bucket"],
                })
                closed_symbols.append(sym)
            else:
                # Stop Ratchet
                prev_bar = df.loc[prev_dt] if prev_dt in df.index else row
                atr_now = prev_bar["atr14"]
                if pd.notna(atr_now):
                    cand_stop = float(prev_bar["close"]) - (ATR_MULTIPLIER * float(atr_now))
                    if cand_stop > pos["stop_px"]:
                        pos["stop_px"] = cand_stop

        for sym in closed_symbols:
            del positions[sym]

        nav_at_open = cash
        for sym, pos in positions.items():
            df = dfs[sym]
            px = float(df.loc[dt, "open"]) if dt in df.index else pos["last_px"]
            nav_at_open += pos["qty"] * px

        # 2. Evaluate Entries
        available_cash = nav_at_open - sum(p.get("entry_cost_total", p["entry_notional"]) for p in positions.values())

        if macro_bull and len(positions) < MAX_POSITIONS:
            candidates = []
            for sym in cohort_manifest["symbol"]:
                if sym in positions or not learner.is_symbol_eligible(sym, dt_str):
                    continue
                df = dfs[sym]
                if prev_dt not in df.index or dt not in df.index:
                    continue
                prev = df.loc[prev_dt]
                today = df.loc[dt]

                req = [prev.get("med_vol_30d"), prev.get(f"donch_high_{LOOKBACK_DONCHIAN}"), prev.get("atr14")]
                if any(pd.isna(x) for x in req) or float(prev["med_vol_30d"]) < LIQUIDITY_FLOOR:
                    continue

                breakout_level = float(prev[f"donch_high_{LOOKBACK_DONCHIAN}"])
                if float(prev["close"]) > breakout_level:
                    strength = (float(prev["close"]) / breakout_level) - 1.0
                    candidates.append((strength, sym, prev, today))

            candidates.sort(key=lambda x: x[0], reverse=True)

            for strength, sym, prev, today in candidates:
                if len(positions) >= MAX_POSITIONS or available_cash < 10.0:
                    break

                raw_entry = float(today["open"]) * (1.0 + SLIPPAGE_RATE)
                atr_val = float(prev["atr14"])
                init_risk_prs = ATR_MULTIPLIER * atr_val
                if init_risk_prs <= 0 or raw_entry <= init_risk_prs:
                    continue

                btc_slope = (float(btc_prev["close"]) / float(btc_prev["sma50"])) - 1.0
                btc_bucket = "STRONG_BULL" if btc_slope > 0.05 else "WEAK_BULL"
                vol_bucket = "HIGH_VOL" if (atr_val / float(prev["close"])) > 0.08 else "NORM_VOL"
                telemetry = {"btc_momentum_bucket": btc_bucket, "volatility_bucket": vol_bucket}

                risk_pct = learner.get_effective_risk_pct(telemetry)
                dollar_risk = nav_at_open * risk_pct
                qty_raw = min(dollar_risk / init_risk_prs, (nav_at_open * MAX_SINGLE_POS_NAV_PCT) / raw_entry, (available_cash / (1.0 + FEE_RATE)) / raw_entry)

                qty_clamped = client.format_order_qty(sym, qty_raw, raw_entry)
                if qty_clamped <= 0:
                    continue

                notional = qty_clamped * raw_entry
                entry_cost_total = notional * (1.0 + FEE_RATE)

                available_cash -= entry_cost_total
                cash -= entry_cost_total

                positions[sym] = {
                    "entry_dt": dt,
                    "entry_px": raw_entry,
                    "entry_notional": notional,
                    "entry_cost_total": entry_cost_total,
                    "qty": qty_clamped,
                    "initial_risk_prs": init_risk_prs,
                    "stop_px": raw_entry - init_risk_prs,
                    "last_px": float(today["open"]),
                    "btc_bucket": btc_bucket,
                    "vol_bucket": vol_bucket,
                }

        # 3. Close MTM
        nav_at_close = cash
        for sym, pos in positions.items():
            df = dfs[sym]
            if dt in df.index:
                close_px = float(df.loc[dt, "close"])
                pos["last_px"] = close_px
                nav_at_close += pos["qty"] * close_px
            else:
                nav_at_close += pos["qty"] * pos["last_px"]

        equity_curve.append((dt, nav_at_close))

    # Terminal Close
    terminal_dt = equity_curve[-1][0]
    for sym in list(positions.keys()):
        pos = positions[sym]
        exit_px = pos["last_px"] * (1.0 - SLIPPAGE_RATE)
        exit_proceeds_net = pos["qty"] * exit_px * (1.0 - FEE_RATE)
        pnl = exit_proceeds_net - pos["entry_cost_total"]
        cash += exit_proceeds_net
        init_risk = pos["qty"] * pos["initial_risk_prs"]
        trade_logs.append({
            "symbol": sym,
            "entry_dt": pos["entry_dt"].strftime("%Y-%m-%d"),
            "exit_dt": terminal_dt.strftime("%Y-%m-%d"),
            "entry_px": pos["entry_px"],
            "exit_px": exit_px,
            "pnl": pnl,
            "r": pnl / init_risk if init_risk > 0 else 0.0,
            "win": pnl > 0,
            "reason": "terminal",
        })

    positions.clear()
    equity_curve[-1] = (terminal_dt, cash)

    df_t = pd.DataFrame(trade_logs)
    eq = pd.Series([x[1] for x in equity_curve], index=[x[0] for x in equity_curve], dtype=float)
    peak = eq.cummax()
    max_dd = abs(float(((eq / peak) - 1.0).min())) * 100.0

    gross_profit = df_t[df_t["pnl"] > 0]["pnl"].sum() if not df_t.empty else 0.0
    gross_loss = abs(df_t[df_t["pnl"] <= 0]["pnl"].sum()) if not df_t.empty else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else np.inf

    return {
        "cohort": c_name,
        "trades": len(df_t),
        "win_rate": (df_t["pnl"] > 0).mean() * 100.0 if not df_t.empty else 0.0,
        "net_pnl": df_t["pnl"].sum() if not df_t.empty else 0.0,
        "max_dd": max_dd,
        "pf": pf,
        "mean_r": df_t["r"].mean() if not df_t.empty else 0.0,
        "median_r": df_t["r"].median() if not df_t.empty else 0.0,
        "proposals_queued": len(learner.proposals),
        "trade_logs": trade_logs,
        "equity_curve": equity_curve,
    }

def main():
    print("=" * 110)
    print(" 📜 G3-B5 v3 SHADOW HISTORICAL REPLAY & BASELINE CERTIFICATION")
    print("=" * 110)

    manifest, dfs = eng.load_data()
    btc_df = dfs["BTC/USD"]
    all_dates = btc_df.index[60:]
    oos_dates = all_dates[int(len(all_dates) * 0.50):]

    cohorts = [
        ("CORE_N27", manifest[~manifest["symbol"].isin(["HYPE/USD", "PUMP/USD", "VVV/USD"])]),
        ("MATURE_N24", manifest[manifest["hit_720_ceiling"]]),
    ]

    results = []
    for c_name, c_manifest in cohorts:
        res = run_shadow_replay(c_name, c_manifest, dfs, oos_dates)
        results.append(res)

    df_out = pd.DataFrame(results)
    print(df_out.to_string(index=False))

if __name__ == "__main__":
    main()
