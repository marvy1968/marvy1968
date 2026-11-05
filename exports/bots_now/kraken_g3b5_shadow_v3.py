#!/usr/bin/env python3
"""
Kraken G3-B5 Production Shadow Daemon (v3 Lifecycle Aligned).
Executes daily shadow cycles using certified 4-phase lifecycle:
  Phase 1A: Open Exits (Macro & Gap Stops)
  Phase 1C: Open Entries (Donchian Breakouts + Dynamic Risk + Lot Clamping)
  Phase 2:  Intraday Low Stops (All open positions)
  Phase 3:  Close MTM & Stop Ratchet
"""
import os
import sys
import json
import logging
import tempfile
import datetime
from datetime import timezone
import pandas as pd
import numpy as np

from kraken_preflight_adapter import KrakenAPIClient
from adaptive_learning_engine_v3 import AdaptiveClosedLoopManager

DATA_DIR = "/home/moonrider1968/bots/crypto_data"
LOG_FILE = os.path.join(DATA_DIR, "SHADOW_v3_daemon.log")

FEE_RATE = 0.0010
SLIPPAGE_RATE = 0.0010
MAX_POSITIONS = 3
MAX_SINGLE_POS_NAV_PCT = 0.40
LOOKBACK_DONCHIAN = 30
LIQUIDITY_FLOOR = 250_000.0
ATR_MULTIPLIER = 2.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode="a")
    ]
)

def atomic_json_dump(data, target_path):
    dirname = os.path.dirname(target_path)
    with tempfile.NamedTemporaryFile("w", dir=dirname, delete=False) as tf:
        json.dump(data, tf, indent=2)
        temp_name = tf.name
    os.replace(temp_name, target_path)

class KrakenG3B5ShadowDaemon:
    def __init__(self, manifest_path: str):
        self.manifest_path = manifest_path
        self.client = KrakenAPIClient()
        self.shadow_positions_path = os.path.join(DATA_DIR, "shadow_active_positions.json")
        self.shadow_orders_log = os.path.join(DATA_DIR, "shadow_order_intents.json")
        self.shadow_ledger_path = os.path.join(DATA_DIR, "SHADOW_v3_trade_telemetry_ledger.json")
        self.shadow_policy_path = os.path.join(DATA_DIR, "SHADOW_v3_active_adaptive_policy.json")
        self.shadow_proposal_path = os.path.join(DATA_DIR, "SHADOW_v3_adaptive_policy_proposals.json")

        self.learner = AdaptiveClosedLoopManager(
            ledger_path=self.shadow_ledger_path,
            policy_path=self.shadow_policy_path,
            proposal_path=self.shadow_proposal_path,
        )
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

    def run_daily_shadow_cycle(self, dfs: dict, simulated_nav: float, target_date_str: str = None) -> dict:
        today_str = target_date_str or datetime.datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logging.info(f"=== STARTING SHADOW CYCLE: {today_str} | Base NAV: ${simulated_nav:,.2f} ===")

        btc_df = dfs.get("BTC/USD")
        if btc_df is None or len(btc_df) < 60:
            logging.error("BTC/USD data missing. Aborting cycle fail-closed.")
            return {"status": "FAIL_CLOSED", "reason": "BTC data missing"}

        btc_prev = btc_df.iloc[-2]
        macro_bull = bool(btc_prev["close"] > btc_prev["ema20"] > btc_prev["sma50"])
        logging.info(f"BTC Macro State: {'BULLISH' if macro_bull else 'DEFENSIVE (Cash)'}")

        cash = simulated_nav - sum(p.get("entry_cost_total", p.get("entry_notional", 0)) for p in self.positions.values())
        if cash < 0:
            cash = 0.0

        # Phase 1A: Open Exits (Macro & Gap Stops)
        open_closed = []
        for sym, pos in list(self.positions.items()):
            df = dfs.get(sym)
            if df is None:
                continue
            today_bar = df.iloc[-1]
            open_px = float(today_bar["open"])

            exit_reason = None
            if not macro_bull:
                exit_reason = "macro_exit"
            elif open_px <= float(pos["stop_px"]):
                exit_reason = "gap_stop"

            if exit_reason:
                exit_px = open_px * (1.0 - SLIPPAGE_RATE)
                gross = float(pos["qty"]) * exit_px
                fee = gross * FEE_RATE
                net = gross - fee
                cash += net
                pnl = net - float(pos.get("entry_cost_total", pos["entry_notional"]))
                init_risk = float(pos["qty"]) * float(pos["initial_risk_prs"])
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                userref = self.client.generate_userref(sym, today_str, "sell")
                kraken_pair = getattr(self.client, "pair_cache", {}).get(sym, {}).get("kraken_pair", sym.replace("/", ""))
                order_intent = {
                    "timestamp": today_str, "pair": sym, "kraken_pair": kraken_pair,
                    "type": "sell", "ordertype": "market", "volume": pos["qty"],
                    "userref": userref, "reason": exit_reason, "estimated_px": exit_px
                }
                self.log_order_intent(order_intent)
                logging.info(f"📋 SHADOW ORDER [SELL 1A]: {sym} | Vol: {pos['qty']} | Userref: {userref} | Reason: {exit_reason} | PnL: ${pnl:.2f} ({r_mult:+.2f}R)")

                trade_record = {
                    "symbol": sym, "timestamp": pos["entry_dt"], "entry_px": pos["entry_px"],
                    "exit_dt": today_str, "exit_px": exit_px, "realized_pnl": pnl,
                    "r_multiple": r_mult, "win": pnl > 0,
                    "btc_momentum_bucket": pos.get("btc_bucket", "UNKNOWN"), "volatility_bucket": pos.get("vol_bucket", "UNKNOWN")
                }
                self.learner.record_completed_trade(trade_record)
                open_closed.append(sym)

        for sym in open_closed:
            del self.positions[sym]

        nav_at_open = cash
        for sym, pos in self.positions.items():
            df = dfs.get(sym)
            px = float(df.iloc[-1]["open"]) if df is not None else float(pos.get("last_px", pos["entry_px"]))
            nav_at_open += float(pos["qty"]) * px

        # Phase 1C: Open Entries
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
                if len(self.positions) >= MAX_POSITIONS or cash < 10.0:
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

                risk_pct = self.learner.get_effective_risk_pct(telemetry)
                dollar_risk = nav_at_open * risk_pct

                qty_raw = min(
                    dollar_risk / init_risk_prs,
                    (nav_at_open * MAX_SINGLE_POS_NAV_PCT) / entry_px,
                    (cash / (1.0 + FEE_RATE)) / entry_px
                )
                try:
                    qty_clamped = self.client.format_order_qty(sym, qty_raw, entry_px)
                except Exception:
                    qty_clamped = round(qty_raw, 4)

                if qty_clamped <= 0:
                    continue

                notional = qty_clamped * entry_px
                entry_cost_total = notional * (1.0 + FEE_RATE)
                if entry_cost_total > cash:
                    continue

                cash -= entry_cost_total
                stop_px = entry_px - init_risk_prs

                userref = self.client.generate_userref(sym, today_str, "buy")
                kraken_pair = getattr(self.client, "pair_cache", {}).get(sym, {}).get("kraken_pair", sym.replace("/", ""))
                order_intent = {
                    "timestamp": today_str, "pair": sym, "kraken_pair": kraken_pair,
                    "type": "buy", "ordertype": "market", "volume": qty_clamped,
                    "userref": userref, "notional": notional, "stop_loss_trigger": stop_px
                }
                self.log_order_intent(order_intent)
                logging.info(f"📋 SHADOW ORDER [BUY 1C]: {sym} | Vol: {qty_clamped} (${notional:,.2f}) | Risk: {risk_pct*100:.3f}% | Stop: {stop_px:.4f} | Userref: {userref}")

                self.positions[sym] = {
                    "entry_dt": today_str,
                    "entry_px": entry_px,
                    "entry_notional": notional,
                    "entry_cost_total": entry_cost_total,
                    "qty": qty_clamped,
                    "initial_risk_prs": init_risk_prs,
                    "stop_px": stop_px,
                    "last_px": float(today["open"]),
                    "btc_bucket": btc_bucket,
                    "vol_bucket": vol_bucket,
                }

        # Phase 2: Intraday Low Stops (All held positions, including new entries)
        intraday_closed = []
        for sym, pos in list(self.positions.items()):
            df = dfs.get(sym)
            if df is None:
                continue
            today_bar = df.iloc[-1]
            if float(today_bar["low"]) <= float(pos["stop_px"]):
                exit_px = float(pos["stop_px"]) * (1.0 - SLIPPAGE_RATE)
                gross = float(pos["qty"]) * exit_px
                fee = gross * FEE_RATE
                net = gross - fee
                cash += net
                pnl = net - float(pos.get("entry_cost_total", pos["entry_notional"]))
                init_risk = float(pos["qty"]) * float(pos["initial_risk_prs"])
                r_mult = pnl / init_risk if init_risk > 0 else 0.0

                userref = self.client.generate_userref(sym, today_str, "sell")
                kraken_pair = getattr(self.client, "pair_cache", {}).get(sym, {}).get("kraken_pair", sym.replace("/", ""))
                order_intent = {
                    "timestamp": today_str, "pair": sym, "kraken_pair": kraken_pair,
                    "type": "sell", "ordertype": "market", "volume": pos["qty"],
                    "userref": userref, "reason": "atr_stop", "estimated_px": exit_px
                }
                self.log_order_intent(order_intent)
                logging.info(f"📋 SHADOW ORDER [STOP 2]: {sym} | Hit Stop: {pos['stop_px']:.4f} | Exit: {exit_px:.4f} | PnL: ${pnl:.2f} ({r_mult:+.2f}R)")

                trade_record = {
                    "symbol": sym, "timestamp": pos["entry_dt"], "entry_px": pos["entry_px"],
                    "exit_dt": today_str, "exit_px": exit_px, "realized_pnl": pnl,
                    "r_multiple": r_mult, "win": pnl > 0,
                    "btc_momentum_bucket": pos.get("btc_bucket", "UNKNOWN"), "volatility_bucket": pos.get("vol_bucket", "UNKNOWN")
                }
                self.learner.record_completed_trade(trade_record)
                intraday_closed.append(sym)

        for sym in intraday_closed:
            del self.positions[sym]

        # Phase 3: Close MTM & Stop Ratchet
        nav_at_close = cash
        for sym, pos in self.positions.items():
            df = dfs.get(sym)
            if df is not None:
                today_bar = df.iloc[-1]
                close_px = float(today_bar["close"])
                pos["last_px"] = close_px
                atr_now = today_bar.get("atr14")
                if pd.notna(atr_now):
                    cand_stop = close_px - (ATR_MULTIPLIER * float(atr_now))
                    if cand_stop > float(pos["stop_px"]):
                        logging.info(f"📈 RATCHET STOP [Phase 3]: {sym} -> ${cand_stop:,.4f}")
                        pos["stop_px"] = cand_stop
                nav_at_close += float(pos["qty"]) * close_px
            else:
                nav_at_close += float(pos["qty"]) * float(pos.get("last_px", pos["entry_px"]))

        atomic_json_dump(self.positions, self.shadow_positions_path)
        logging.info(f"=== SHADOW CYCLE COMPLETE. NAV: ${nav_at_close:,.2f} | Open Positions: {len(self.positions)}/{MAX_POSITIONS} ===")
        
        return {
            "status": "SUCCESS",
            "cycle_date": today_str,
            "nav_at_close": nav_at_close,
            "cash": cash,
            "open_positions": len(self.positions),
        }

if __name__ == "__main__":
    daemon = KrakenG3B5ShadowDaemon(manifest_path=os.path.join(DATA_DIR, "universe_manifest.csv"))
    logging.info("KrakenG3B5ShadowDaemon initialized cleanly.")
