#!/usr/bin/env python3
"""
G3-B5 INSTITUTIONAL SPOT TRADING ENGINE (KRAKEN PRODUCTION)
Strategy: Certified Frozen Beta (30d Donchian High, 2.0x ATR Stop)
Risk Layer: 0.40% NAV Baseline (0.25%-0.50% Hard Adaptive Bounded Governor)
Safety: Deterministic Order Clamping, Atomic Stop Sync, Symbol Penalty Box
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timezone
import pandas as pd
import numpy as np

from adaptive_learning_engine_v2 import AdaptiveClosedLoopManager, DATA_DIR

# --- LOGGING CONFIGURATION ---
LOG_FILE = os.path.join(DATA_DIR, "g3b5_live_engine.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)]
)

# --- HARD INSTITUTIONAL CONSTANTS (LEVEL 0) ---
LOOKBACK_DONCHIAN = 30
ATR_MULTIPLIER = 2.0
LIQUIDITY_FLOOR = 250_000.0
MAX_POSITIONS = 3
MAX_SINGLE_POS_NAV_PCT = 0.40
FEE_BUFFER = 0.0020

class KrakenG3B5LiveBot:
    def __init__(self, manifest_path: str):
        self.manifest_path = manifest_path
        self.learner = AdaptiveClosedLoopManager()
        self.active_positions_path = os.path.join(DATA_DIR, "live_active_positions.json")
        self.positions = self._load_positions()

    def _load_positions(self) -> dict:
        if os.path.exists(self.active_positions_path):
            try:
                with open(self.active_positions_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Error loading positions: {e}")
                return {}
        return {}

    def _save_positions(self):
        with open(self.active_positions_path, "w") as f:
            json.dump(self.positions, f, indent=2)

    def evaluate_daily_cycle(self, dfs: dict, current_nav: float):
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logging.info(f"=== STARTING G3-B5 EXECUTION CYCLE: {today_str} | NAV: ${current_nav:,.2f} ===")

        btc_df = dfs.get("BTC/USD")
        if btc_df is None or len(btc_df) < 60:
            logging.error("BTC/USD data feed missing or insufficient. Aborting cycle.")
            return

        btc_prev = btc_df.iloc[-2] # Completed bar
        macro_bull = bool(btc_prev["close"] > btc_prev["ema20"] > btc_prev["sma50"])
        logging.info(f"BTC Macro State: {'BULLISH (Close > EMA20 > SMA50)' if macro_bull else 'DEFENSIVE / CASH (Risk-Off)'}")

        # PHASE 1: MANAGE EXISTING POSITIONS
        closed_symbols = []
        for sym, pos in list(self.positions.items()):
            df = dfs.get(sym)
            if df is None:
                continue
            today_bar = df.iloc[-1]
            prev_bar = df.iloc[-2]
            current_px = float(today_bar["open"])

            exit_reason = None
            if not macro_bull:
                exit_reason = "macro_exit"
            elif current_px <= pos["stop_px"]:
                exit_reason = "stop_hit"

            if exit_reason:
                logging.info(f"🚨 EXIT TRIGGERED for {sym} | Reason: {exit_reason} | Px: ${current_px:,.4f}")
                # Log outcome into Adaptive Learner
                trade_record = {
                    "symbol": sym,
                    "timestamp": pos["entry_dt"],
                    "entry_px": pos["entry_px"],
                    "exit_dt": today_str,
                    "exit_px": current_px,
                    "realized_pnl": (pos["qty"] * current_px) - pos["entry_notional"],
                    "r_multiple": ((current_px - pos["entry_px"]) * pos["qty"]) / (pos["qty"] * pos["initial_risk_prs"]),
                    "win": current_px > pos["entry_px"],
                    "btc_momentum_bucket": pos["btc_bucket"],
                    "volatility_bucket": pos["vol_bucket"]
                }
                success, msg = self.learner.record_completed_trade(trade_record)
                logging.info(f"Learner Update [{sym}]: {msg}")
                closed_symbols.append(sym)
            else:
                # Ratchet Trailing Stop
                atr_now = float(prev_bar["atr14"])
                cand_stop = float(prev_bar["close"]) - (ATR_MULTIPLIER * atr_now)
                if cand_stop > pos["stop_px"]:
                    logging.info(f"📈 RATCHET STOP for {sym}: ${pos['stop_px']:,.4f} -> ${cand_stop:,.4f}")
                    pos["stop_px"] = cand_stop

        for sym in closed_symbols:
            del self.positions[sym]
        self._save_positions()

        # PHASE 2: EVALUATE NEW BREAKOUT ENTRIES
        if macro_bull and len(self.positions) < MAX_POSITIONS:
            manifest = pd.read_csv(self.manifest_path)
            candidates = []

            for sym in manifest["symbol"]:
                if sym in self.positions:
                    continue
                if not self.learner.is_symbol_eligible(sym, today_str):
                    logging.info(f"Asset {sym} is in Penalty Box cooldown. Skipping.")
                    continue

                df = dfs.get(sym)
                if df is None or len(df) < 60:
                    continue
                prev_bar = df.iloc[-2]
                today_bar = df.iloc[-1]

                if float(prev_bar.get("med_vol_30d", 0)) < LIQUIDITY_FLOOR:
                    continue

                breakout_level = float(prev_bar[f"donch_high_{LOOKBACK_DONCHIAN}"])
                if float(prev_bar["close"]) > breakout_level:
                    strength = (float(prev_bar["close"]) / breakout_level) - 1.0
                    candidates.append((strength, sym, prev_bar, today_bar))

            candidates.sort(key=lambda x: x[0], reverse=True)

            for strength, sym, prev_bar, today_bar in candidates:
                if len(self.positions) >= MAX_POSITIONS:
                    break

                raw_entry = float(today_bar["open"])
                atr_val = float(prev_bar["atr14"])
                init_risk_prs = ATR_MULTIPLIER * atr_val
                if init_risk_prs <= 0 or raw_entry <= init_risk_prs:
                    continue

                # Classify Telemetry
                btc_slope = (float(btc_prev["close"]) / float(btc_prev["sma50"])) - 1.0
                btc_bucket = "STRONG_BULL" if btc_slope > 0.05 else "WEAK_BULL"
                vol_bucket = "HIGH_VOL" if (atr_val / float(prev_bar["close"])) > 0.08 else "NORM_VOL"
                telemetry = {"btc_momentum_bucket": btc_bucket, "volatility_bucket": vol_bucket}

                # Dynamic Bounded Risk Calculation
                effective_risk_pct = self.learner.get_effective_risk_pct(telemetry)
                dollar_risk = current_nav * effective_risk_pct
                qty_risk = dollar_risk / init_risk_prs
                qty_cap = (current_nav * MAX_SINGLE_POS_NAV_PCT) / raw_entry

                qty = min(qty_risk, qty_cap)
                notional = qty * raw_entry

                logging.info(f"🎯 NEW ENTRY: {sym} | Sizing: {qty:.4f} units (${notional:,.2f}) | Risk: {effective_risk_pct*100:.3f}% NAV | Stop: ${raw_entry - init_risk_prs:,.4f}")

                self.positions[sym] = {
                    "entry_dt": today_str,
                    "entry_px": raw_entry,
                    "entry_notional": notional,
                    "qty": qty,
                    "initial_risk_prs": init_risk_prs,
                    "stop_px": raw_entry - init_risk_prs,
                    "btc_bucket": btc_bucket,
                    "vol_bucket": vol_bucket
                }

        self._save_positions()
        logging.info(f"=== CYCLE COMPLETE. ACTIVE POSITIONS: {len(self.positions)}/{MAX_POSITIONS} ===\n")

if __name__ == "__main__":
    bot = KrakenG3B5LiveBot(manifest_path=os.path.join(DATA_DIR, "universe_manifest.csv"))
    logging.info("G3-B5 Live Execution Daemon initialized and ready.")
