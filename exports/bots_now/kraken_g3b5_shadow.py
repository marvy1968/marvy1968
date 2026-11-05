#!/usr/bin/env python3
"""
G3-B5 PROSPECTIVE SHADOW EXECUTION DAEMON (KRAKEN SPOT)
Runs daily bar evaluation, checks Kraken lot precision constraints, logs
deterministic order payloads, and feeds the prospective adaptive learner.
"""

import os
import sys
import json
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from kraken_preflight_adapter import KrakenAPIClient
from adaptive_learning_engine_v2 import AdaptiveClosedLoopManager, DATA_DIR, atomic_json_dump

LOG_FILE = os.path.join(DATA_DIR, "g3b5_shadow_engine.log")
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

class KrakenG3B5ShadowDaemon:
    def __init__(self, manifest_path: str):
        self.manifest_path = manifest_path
        self.client = KrakenAPIClient()
        self.shadow_positions_path = os.path.join(DATA_DIR, "shadow_active_positions.json")
        self.shadow_orders_log = os.path.join(DATA_DIR, "shadow_order_intents.json")
        
        self.shadow_ledger_path = os.path.join(DATA_DIR, "SHADOW_trade_telemetry_ledger.json")
        self.shadow_policy_path = os.path.join(DATA_DIR, "SHADOW_active_adaptive_policy.json")
        self.learner = AdaptiveClosedLoopManager(ledger_path=self.shadow_ledger_path, policy_path=self.shadow_policy_path)

        self.positions = self._load_positions()
        self.manifest = pd.read_csv(self.manifest_path)
        
        logging.info("Caching Kraken AssetPairs metadata...")
        self.client.load_pair_constraints(self.manifest["symbol"].tolist())

    def _load_positions(self) -> dict:
        if os.path.exists(self.shadow_positions_path):
            try:
                with open(self.shadow_positions_path, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def log_order_intent(self, order_payload: dict):
        orders = []
        if os.path.exists(self.shadow_orders_log):
            try:
                with open(self.shadow_orders_log, "r") as f:
                    orders = json.load(f)
            except Exception:
                pass
        orders.append(order_payload)
        atomic_json_dump(orders, self.shadow_orders_log)

    def run_daily_shadow_cycle(self, dfs: dict, simulated_nav: float):
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logging.info(f"=== STARTING SHADOW CYCLE: {today_str} | NAV: ${simulated_nav:,.2f} ===")

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
            if not macro_bull:
                exit_reason = "macro_exit"
            elif open_px <= pos["stop_px"]:
                exit_reason = "gap_stop"

            if exit_reason:
                exit_px = open_px * (1.0 - SLIPPAGE_RATE)
                userref = self.client.generate_userref(sym, today_str, "sell")
                
                order_intent = {
                    "timestamp": today_str, "pair": sym, "kraken_pair": self.client.pair_cache[sym]["kraken_pair"],
                    "type": "sell", "ordertype": "market", "volume": pos["qty"],
                    "userref": userref, "reason": exit_reason, "estimated_px": exit_px
                }
                self.log_order_intent(order_intent)
                logging.info(f"📋 SHADOW ORDER [SELL]: {sym} | Vol: {pos['qty']} | Userref: {userref} | Reason: {exit_reason}")

                pnl = (pos["qty"] * exit_px * (1.0 - FEE_RATE)) - pos["entry_notional"]
                init_risk = pos["qty"] * pos["initial_risk_prs"]
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                trade_record = {
                    "symbol": sym, "timestamp": pos["entry_dt"], "entry_px": pos["entry_px"],
                    "exit_dt": today_str, "exit_px": exit_px, "realized_pnl": pnl,
                    "r_multiple": r_mult, "win": pnl > 0,
                    "btc_momentum_bucket": pos["btc_bucket"], "volatility_bucket": pos["vol_bucket"]
                }
                success, msg = self.learner.record_completed_trade(trade_record)
                logging.info(f"Learner Response: {msg}")
                closed_symbols.append(sym)
            else:
                atr_now = float(prev_bar["atr14"])
                cand_stop = float(prev_bar["close"]) - (ATR_MULTIPLIER * atr_now)
                if cand_stop > pos["stop_px"]:
                    pos["stop_px"] = cand_stop
                    logging.info(f"📈 RATCHET STOP: {sym} -> ${cand_stop:,.4f}")

        for sym in closed_symbols:
            del self.positions[sym]
        atomic_json_dump(self.positions, self.shadow_positions_path)

        # 2. Evaluate Entries with Cash Reservation
        available_cash = simulated_nav - sum(p["entry_notional"] for p in self.positions.values())

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
                qty_raw = min(dollar_risk / init_risk_prs, (simulated_nav * MAX_SINGLE_POS_NAV_PCT) / raw_entry, (available_cash / (1.0 + FEE_RATE)) / raw_entry)

                qty_clamped = self.client.format_order_qty(sym, qty_raw, raw_entry)
                if qty_clamped <= 0:
                    continue

                notional = qty_clamped * raw_entry
                userref = self.client.generate_userref(sym, today_str, "buy")

                order_intent = {
                    "timestamp": today_str, "pair": sym, "kraken_pair": self.client.pair_cache[sym]["kraken_pair"],
                    "type": "buy", "ordertype": "market", "volume": qty_clamped,
                    "userref": userref, "notional": notional, "stop_loss_trigger": raw_entry - init_risk_prs
                }
                self.log_order_intent(order_intent)
                logging.info(f"📋 SHADOW ORDER [BUY]: {sym} | Vol: {qty_clamped} (${notional:,.2f}) | Risk: {risk_pct*100:.3f}% | Userref: {userref}")

                available_cash -= (notional * (1.0 + FEE_RATE))
                self.positions[sym] = {
                    "entry_dt": today_str, "entry_px": raw_entry, "entry_notional": notional,
                    "qty": qty_clamped, "initial_risk_prs": init_risk_prs,
                    "stop_px": raw_entry - init_risk_prs, "btc_bucket": btc_bucket, "vol_bucket": vol_bucket
                }

        atomic_json_dump(self.positions, self.shadow_positions_path)
        logging.info(f"=== SHADOW CYCLE COMPLETE. POSITIONS: {len(self.positions)}/{MAX_POSITIONS} ===\n")

if __name__ == "__main__":
    daemon = KrakenG3B5ShadowDaemon(manifest_path=os.path.join(DATA_DIR, "universe_manifest.csv"))
    logging.info("Shadow Daemon initialized cleanly.")
