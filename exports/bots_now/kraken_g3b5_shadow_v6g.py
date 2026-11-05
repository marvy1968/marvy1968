#!/usr/bin/env python3
"""
G3-B5 SHADOW EXECUTION DAEMON (v6G Hardened Integration)
Features:
- Integrated kraken_execution_adapter_v6g (32-hex cl_ord_id, OrderJournalManager, Monotonic Nonces)
- Fail-Closed Strict JSON State Loading
- Prospective Adaptive Learner v3 Integration
- Two-Sided Fee Accounting & Intraday Stop Chronology
- Duplicate Intent Protection & Startup Replay Reconciler

SAFETY INVARIANT:
Live order placement (AddOrder) is disabled. Operates exclusively in Shadow/Paper mode.
"""

import os
import sys
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from kraken_execution_adapter_v6g import (
    KrakenAPIClient,
    OrderJournalManager,
    DATA_DIR,
    strict_json_load,
    atomic_json_dump
)
from adaptive_learning_engine_v3 import AdaptiveClosedLoopManager

LOG_FILE = os.path.join(DATA_DIR, "g3b5_shadow_v6g_engine.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)]
)

LOOKBACK_DONCHIAN = 30
ATR_MULTIPLIER = 2.0
LIQUIDITY_FLOOR = 250_000.0
MAX_POSITIONS = 3
MAX_SINGLE_POS_NAV_PCT = 0.40
FEE_RATE = 0.0010
SLIPPAGE_RATE = 0.0010

class KrakenG3B5ShadowDaemonV6G:
    def __init__(self, manifest_path: str):
        self.manifest_path = manifest_path
        self.client = KrakenAPIClient()
        
        self.journal_path = os.path.join(DATA_DIR, "SHADOW_v6g_order_intent_journal.json")
        self.positions_path = os.path.join(DATA_DIR, "SHADOW_v6g_active_positions.json")
        self.journal_mgr = OrderJournalManager(journal_path=self.journal_path, positions_path=self.positions_path)
        
        self.shadow_ledger_path = os.path.join(DATA_DIR, "SHADOW_v6g_trade_telemetry_ledger.json")
        self.shadow_policy_path = os.path.join(DATA_DIR, "SHADOW_v6g_active_adaptive_policy.json")
        self.shadow_proposal_path = os.path.join(DATA_DIR, "SHADOW_v6g_adaptive_policy_proposals.json")
        
        self.learner = AdaptiveClosedLoopManager(
            ledger_path=self.shadow_ledger_path,
            policy_path=self.shadow_policy_path,
            proposal_path=self.shadow_proposal_path
        )

        self.positions = strict_json_load(self.positions_path, default={})
        self.manifest = pd.read_csv(self.manifest_path)
        
        logging.info("Caching Kraken AssetPairs metadata via hardened adapter...")
        self.client.load_pair_constraints(self.manifest["symbol"].tolist())

    def run_daily_shadow_cycle(self, dfs: dict, simulated_nav: float):
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logging.info(f"=== STARTING SHADOW v6G CYCLE: {today_str} | NAV: ${simulated_nav:,.2f} ===")

        btc_df = dfs.get("BTC/USD")
        if btc_df is None or len(btc_df) < 60:
            logging.error("BTC/USD data missing. Aborting cycle.")
            return

        btc_prev = btc_df.iloc[-2]
        macro_bull = bool(btc_prev["close"] > btc_prev["ema20"] > btc_prev["sma50"])
        logging.info(f"BTC Macro State: {'BULLISH' if macro_bull else 'DEFENSIVE (Cash)'}")

        # 1. Manage Positions & Exits
        closed_symbols = []
        for sym, pos in list(self.positions.items()):
            df = dfs.get(sym)
            if df is None:
                continue
            today_bar = df.iloc[-1]
            prev_bar = df.iloc[-2]
            open_px = float(today_bar["open"])

            exit_reason = None
            exit_reference_px = None

            if not macro_bull:
                exit_reason = "macro_exit"
                exit_reference_px = open_px
            elif open_px <= pos["stop_px"]:
                exit_reason = "gap_stop"
                exit_reference_px = open_px
            elif float(today_bar["low"]) <= pos["stop_px"]:
                exit_reason = "intraday_stop"
                exit_reference_px = float(pos["stop_px"])

            if exit_reason:
                exit_px = exit_reference_px * (1.0 - SLIPPAGE_RATE)
                cl_ord_id = self.client.generate_cl_ord_id(sym, today_str, "sell", seq=1)
                
                # Record in Journal
                self.journal_mgr.record_intent(
                    cl_ord_id=cl_ord_id,
                    symbol=sym,
                    side="sell",
                    qty=pos["qty"],
                    price=exit_px,
                    stop_loss=0.0,
                    dt_str=today_str
                )
                self.journal_mgr.update_intent_status(cl_ord_id, "RECONCILED", fill_px=exit_px, fill_qty=pos["qty"])
                logging.info(f"📋 SHADOW ORDER [SELL]: {sym} | Vol: {pos['qty']} | cl_ord_id: {cl_ord_id} | Reason: {exit_reason}")

                exit_proceeds_net = pos["qty"] * exit_px * (1.0 - FEE_RATE)
                pnl = exit_proceeds_net - pos["entry_cost_total"]
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                trade_record = {
                    "symbol": sym, "timestamp": pos["entry_dt"], "entry_px": pos["entry_px"],
                    "exit_dt": today_str, "exit_px": exit_px, "realized_pnl": pnl,
                    "r_multiple": r_mult, "win": pnl > 0,
                    "btc_momentum_bucket": pos["btc_bucket"], "volatility_bucket": pos["vol_bucket"]
                }
                success, msg = self.learner.record_completed_trade(trade_record)
                logging.info(f"Learner Telemetry: {msg}")
                closed_symbols.append(sym)
            else:
                atr_now = float(prev_bar["atr14"])
                cand_stop = float(prev_bar["close"]) - (ATR_MULTIPLIER * atr_now)
                if cand_stop > pos["stop_px"]:
                    pos["stop_px"] = cand_stop
                    logging.info(f"📈 RATCHET STOP: {sym} -> ${cand_stop:,.4f}")

        for sym in closed_symbols:
            del self.positions[sym]
        atomic_json_dump(self.positions, self.positions_path)

        # 2. Evaluate Entries with Cash Reservation
        available_cash = simulated_nav - sum(
            p.get("entry_cost_total", p["entry_notional"])
            for p in self.positions.values()
        )

        if macro_bull and len(self.positions) < MAX_POSITIONS:
            candidates = []
            for sym in self.manifest["symbol"]:
                if sym in self.positions or not self.learner.is_symbol_eligible(sym, today_str):
                    continue

                df = dfs.get(sym)
                if df is None or len(df) < 60:
                    continue
                prev = df.iloc[-2]
                today = df.iloc[-1]

                if float(prev.get("med_vol_30d", 0)) < LIQUIDITY_FLOOR:
                    continue

                breakout_level = float(prev[f"donch_high_{LOOKBACK_DONCHIAN}"])
                if float(prev["close"]) > breakout_level:
                    strength = (float(prev["close"]) / breakout_level) - 1.0
                    candidates.append((strength, sym, prev, today))

            candidates.sort(key=lambda x: x[0], reverse=True)

            for strength, sym, prev, today in candidates:
                if len(self.positions) >= MAX_POSITIONS or available_cash < 10.0:
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

                risk_pct = self.learner.get_effective_risk_pct(telemetry)
                dollar_risk = simulated_nav * risk_pct
                qty_raw = min(
                    dollar_risk / init_risk_prs,
                    (simulated_nav * MAX_SINGLE_POS_NAV_PCT) / raw_entry,
                    (available_cash / (1.0 + FEE_RATE)) / raw_entry
                )

                qty_clamped = self.client.format_order_qty(sym, qty_raw, raw_entry)
                if qty_clamped <= 0:
                    continue

                notional = qty_clamped * raw_entry
                entry_cost_total = notional * (1.0 + FEE_RATE)
                cl_ord_id = self.client.generate_cl_ord_id(sym, today_str, "buy", seq=1)

                # Record in Journal
                self.journal_mgr.record_intent(
                    cl_ord_id=cl_ord_id,
                    symbol=sym,
                    side="buy",
                    qty=qty_clamped,
                    price=raw_entry,
                    stop_loss=raw_entry - init_risk_prs,
                    dt_str=today_str
                )
                self.journal_mgr.update_intent_status(cl_ord_id, "RECONCILED", fill_px=raw_entry, fill_qty=qty_clamped)

                logging.info(f"📋 SHADOW ORDER [BUY]: {sym} | Vol: {qty_clamped} (${notional:,.2f}) | cl_ord_id: {cl_ord_id}")

                available_cash -= entry_cost_total
                self.positions[sym] = {
                    "entry_dt": today_str,
                    "entry_px": raw_entry,
                    "entry_notional": notional,
                    "entry_cost_total": entry_cost_total,
                    "qty": qty_clamped,
                    "initial_risk_prs": init_risk_prs,
                    "stop_px": raw_entry - init_risk_prs,
                    "btc_bucket": btc_bucket,
                    "vol_bucket": vol_bucket,
                    "cl_ord_id": cl_ord_id
                }

        atomic_json_dump(self.positions, self.positions_path)
        logging.info(f"=== SHADOW v6G CYCLE COMPLETE. POSITIONS: {len(self.positions)}/{MAX_POSITIONS} ===\n")

if __name__ == "__main__":
    daemon = KrakenG3B5ShadowDaemonV6G(manifest_path=os.path.join(DATA_DIR, "universe_manifest.csv"))
    logging.info("KrakenG3B5ShadowDaemonV6G initialized cleanly.")
