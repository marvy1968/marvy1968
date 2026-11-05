#!/usr/bin/env python3
"""
G3-B5 v3 Shadow Historical Replay Engine (Phase 1 Chronology Aligned).
Executes exact Canonical Phase 1A (Open Exits) -> Phase 1C (Open Entries) -> Phase 2 (Intraday Stops) -> Phase 3 (Close MTM / Ratchet).
"""
import os
import datetime
import pandas as pd
import numpy as np

import kraken_g3b5_shadow_v3 as kmod
# dynamic client alias
from adaptive_learning_engine_v3 import AdaptiveLearningEngineV3
import run_stitched_oos_engine as eng

DATA_DIR = "/home/moonrider1968/bots/crypto_data"
MANIFEST_PATH = os.path.join(DATA_DIR, "universe_manifest.csv")

FEE_RATE = 0.0010
SLIPPAGE_RATE = 0.0010
MAX_POSITIONS = 3
INITIAL_CAPITAL = 100_000.0
MAX_SINGLE_POS_NAV_PCT = 0.40
LOOKBACK_DONCHIAN = 30
LIQUIDITY_FLOOR = 250_000.0
ATR_MULTIPLIER = 2.0

def run_shadow_replay(c_name, cohort_manifest, dfs, oos_dates, force_fixed_risk=None, disable_penalty=False):
    client_cls = getattr(kmod, 'KrakenAPIClient', None)
    client = client_cls(key='', secret='') if client_cls else None
    learner = AdaptiveLearningEngineV3()
    btc_df = dfs["BTC/USD"]

    cash = INITIAL_CAPITAL
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

        # Phase 1A: Open Exits (Macro exit & Gap stops)
        open_closed = []
        for sym, pos in list(positions.items()):
            df = dfs[sym]
            if dt not in df.index:
                continue
            row = df.loc[dt]
            open_px = float(row["open"])

            exit_reason = None
            if not macro_bull:
                exit_reason = "macro_exit"
            elif open_px <= pos["stop_px"]:
                exit_reason = "gap_stop"

            if exit_reason:
                exit_px = open_px * (1.0 - SLIPPAGE_RATE)
                gross = pos["qty"] * exit_px
                fee = gross * FEE_RATE
                net = gross - fee
                cash += net
                pnl = net - (pos["entry_notional"] + pos["entry_fee"])
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                record = {
                    "symbol": sym, "entry_dt": pos["entry_dt"], "exit_dt": dt,
                    "entry_px": pos["entry_px"], "exit_px": exit_px, "pnl": pnl,
                    "r": r_mult, "win": pnl > 0, "reason": exit_reason,
                    "btc_momentum_bucket": pos["btc_bucket"], "volatility_bucket": pos["vol_bucket"],
                }
                trade_logs.append(record)
                learner.record_completed_trade({
                    "symbol": sym, "timestamp": pos["entry_dt"].strftime("%Y-%m-%d"),
                    "entry_px": pos["entry_px"], "exit_dt": dt_str, "exit_px": exit_px,
                    "realized_pnl": pnl, "r_multiple": r_mult, "win": pnl > 0,
                    "btc_momentum_bucket": pos["btc_bucket"], "volatility_bucket": pos["vol_bucket"],
                })
                open_closed.append(sym)

        for sym in open_closed:
            del positions[sym]

        nav_at_open = cash
        for sym, pos in positions.items():
            df = dfs[sym]
            px = float(df.loc[dt, "open"]) if dt in df.index else pos["last_px"]
            nav_at_open += pos["qty"] * px

        # Phase 1C: Open Entries
        if macro_bull and len(positions) < MAX_POSITIONS:
            candidates = []
            for sym in cohort_manifest["symbol"]:
                if sym in positions:
                    continue
                if not disable_penalty and not learner.is_symbol_eligible(sym, dt_str):
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
                if len(positions) >= MAX_POSITIONS:
                    break

                raw_entry = float(today["open"])
                entry_px = raw_entry * (1.0 + SLIPPAGE_RATE)
                atr_val = float(prev["atr14"])
                init_risk_prs = ATR_MULTIPLIER * atr_val
                if init_risk_prs <= 0 or entry_px <= init_risk_prs:
                    continue

                btc_slope = (float(btc_prev["close"]) / float(btc_prev["sma50"])) - 1.0
                btc_bucket = "STRONG_BULL" if btc_slope > 0.05 else "WEAK_BULL"
                vol_bucket = "HIGH_VOL" if (atr_val / float(prev["close"])) > 0.08 else "NORM_VOL"
                telemetry = {"btc_momentum_bucket": btc_bucket, "volatility_bucket": vol_bucket}

                risk_pct = force_fixed_risk if force_fixed_risk is not None else learner.get_effective_risk_pct(telemetry)
                dollar_risk = nav_at_open * risk_pct

                qty_raw = min(
                    dollar_risk / init_risk_prs,
                    (nav_at_open * MAX_SINGLE_POS_NAV_PCT) / entry_px,
                    (cash / (1.0 + FEE_RATE)) / entry_px
                )
                if force_fixed_risk is not None:
                    qty = qty_raw
                else:
                    qty = client.format_order_qty(sym, qty_raw, entry_px)

                if qty <= 0:
                    continue

                entry_notional = qty * entry_px
                entry_fee = entry_notional * FEE_RATE
                if (entry_notional + entry_fee) > cash:
                    continue

                cash -= (entry_notional + entry_fee)
                positions[sym] = {
                    "entry_dt": dt,
                    "entry_px": entry_px,
                    "entry_notional": entry_notional,
                    "entry_fee": entry_fee,
                    "qty": qty,
                    "initial_risk_prs": init_risk_prs,
                    "stop_px": entry_px - init_risk_prs,
                    "last_px": float(today["open"]),
                    "btc_bucket": btc_bucket,
                    "vol_bucket": vol_bucket,
                }

        # Phase 2: Intraday Low Stops (All positions, including new entries)
        intraday_closed = []
        for sym, pos in list(positions.items()):
            df = dfs[sym]
            if dt not in df.index:
                continue
            row = df.loc[dt]
            if float(row["low"]) <= pos["stop_px"]:
                exit_px = pos["stop_px"] * (1.0 - SLIPPAGE_RATE)
                gross = pos["qty"] * exit_px
                fee = gross * FEE_RATE
                net = gross - fee
                cash += net
                pnl = net - (pos["entry_notional"] + pos["entry_fee"])
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                record = {
                    "symbol": sym, "entry_dt": pos["entry_dt"], "exit_dt": dt,
                    "entry_px": pos["entry_px"], "exit_px": exit_px, "pnl": pnl,
                    "r": r_mult, "win": pnl > 0, "reason": "atr_stop",
                    "btc_momentum_bucket": pos["btc_bucket"], "volatility_bucket": pos["vol_bucket"],
                }
                trade_logs.append(record)
                learner.record_completed_trade({
                    "symbol": sym, "timestamp": pos["entry_dt"].strftime("%Y-%m-%d"),
                    "entry_px": pos["entry_px"], "exit_dt": dt_str, "exit_px": exit_px,
                    "realized_pnl": pnl, "r_multiple": r_mult, "win": pnl > 0,
                    "btc_momentum_bucket": pos["btc_bucket"], "volatility_bucket": pos["vol_bucket"],
                })
                intraday_closed.append(sym)

        for sym in intraday_closed:
            del positions[sym]

        # Phase 3: Close MTM & Stop Ratchet
        nav_at_close = cash
        for sym, pos in positions.items():
            df = dfs[sym]
            if dt in df.index:
                row = df.loc[dt]
                close_px = float(row["close"])
                pos["last_px"] = close_px
                atr_now = row["atr14"]
                if pd.notna(atr_now):
                    cand_stop = close_px - (ATR_MULTIPLIER * float(atr_now))
                    if cand_stop > pos["stop_px"]:
                        pos["stop_px"] = cand_stop
                nav_at_close += pos["qty"] * close_px
            else:
                nav_at_close += pos["qty"] * pos["last_px"]

        equity_curve.append((dt, nav_at_close))

    # Phase 4: Terminal Mark
    terminal_dt = equity_curve[-1][0]
    for sym in list(positions.keys()):
        pos = positions[sym]
        exit_px = pos["last_px"] * (1.0 - SLIPPAGE_RATE)
        gross = pos["qty"] * exit_px
        net = gross - (gross * FEE_RATE)
        cash += net
        pnl = net - (pos["entry_notional"] + pos["entry_fee"])
        init_risk = pos["qty"] * pos["initial_risk_prs"]
        r_mult = pnl / init_risk if init_risk > 0 else 0.0

        trade_logs.append({
            "symbol": sym, "entry_dt": pos["entry_dt"], "exit_dt": terminal_dt,
            "entry_px": pos["entry_px"], "exit_px": exit_px, "pnl": pnl,
            "r": r_mult, "win": pnl > 0, "reason": "terminal",
            "btc_momentum_bucket": pos["btc_bucket"], "volatility_bucket": pos["vol_bucket"],
        })

    positions.clear()
    equity_curve[-1] = (terminal_dt, cash)

    df_t = pd.DataFrame(trade_logs)
    eq = pd.Series([x[1] for x in equity_curve], index=[x[0] for x in equity_curve], dtype=float)
    peak = eq.cummax()
    max_dd = abs(float(((eq / peak) - 1.0).min())) * 100.0 if not eq.empty else 0.0

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
