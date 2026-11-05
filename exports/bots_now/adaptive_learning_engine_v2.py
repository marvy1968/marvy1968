#!/usr/bin/env python3
"""
Production Closed-Loop Meta-Learner Engine (v2)
Features:
- Deterministic Trade ID Deduplication & Atomic File Writes
- Explicit Policy Namespaces (btc_risk_multipliers, vol_risk_multipliers)
- Asset Cooldown Penalty Box (Active Stop-Out Tracker)
- Historical Shadow Replay Engine (Drawdown/MAR Promotion Guard)
"""

import os
import json
import hashlib
import tempfile
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

DATA_DIR = "/home/moonrider1968/bots/crypto_data"
TELEMETRY_LOG_PATH = os.path.join(DATA_DIR, "trade_telemetry_ledger.json")
POLICY_STATE_PATH = os.path.join(DATA_DIR, "active_adaptive_policy.json")

# HARD IMMUTABLE CONSTRAINTS (LEVEL 0)
MIN_RISK_PER_TRADE = 0.0025   # 0.25% NAV Floor
MAX_RISK_PER_TRADE = 0.0050   # 0.50% NAV Ceiling
BASE_RISK_PER_TRADE = 0.0040  # 0.40% Production Baseline
MIN_SAMPLE_FOR_PROMOTION = 15 # Minimum sample support

def generate_trade_id(trade_dict: dict) -> str:
    """Generates a deterministic hash from immutable trade entry coordinates."""
    raw_str = f"{trade_dict['symbol']}_{trade_dict['timestamp']}_{trade_dict['entry_px']:.6f}_{trade_dict['exit_dt']}"
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()[:16]

def atomic_json_dump(data, target_path):
    """Safely writes JSON using a temporary file to avoid partial write corruptions."""
    dirname = os.path.dirname(target_path)
    with tempfile.NamedTemporaryFile("w", dir=dirname, delete=False) as tf:
        json.dump(data, tf, indent=2)
        temp_name = tf.name
    os.replace(temp_name, target_path)

class ShadowReplayEngine:
    """Replays historical trade outcomes under proposed risk scalars vs current baseline."""
    @staticmethod
    def evaluate_shadow_policy(ledger: list, candidate_policy: dict) -> dict:
        if len(ledger) < MIN_SAMPLE_FOR_PROMOTION:
            return {"promoted": False, "reason": "INSUFFICIENT_GLOBAL_SUPPORT"}

        df = pd.DataFrame(ledger)
        
        # Simulate Compounded NAV Curve under candidate policy
        initial_nav = 100_000.0
        nav = initial_nav
        curve = [nav]
        
        adjusted_pnls = []
        for _, row in df.iterrows():
            btc_mult = candidate_policy["btc_risk_multipliers"].get(row["btc_momentum_bucket"], 1.0)
            vol_mult = candidate_policy["vol_risk_multipliers"].get(row["volatility_bucket"], 1.0)
            scalar = min(btc_mult, vol_mult)
            effective_risk = np.clip(BASE_RISK_PER_TRADE * scalar, MIN_RISK_PER_TRADE, MAX_RISK_PER_TRADE)
            
            # Reconstruct PnL based on re-scaled initial risk
            dollar_risk = nav * effective_risk
            scaled_pnl = row["r_multiple"] * dollar_risk
            nav += scaled_pnl
            curve.append(nav)
            adjusted_pnls.append(scaled_pnl)

        curve_arr = np.array(curve)
        peak = np.maximum.accumulate(curve_arr)
        max_dd = abs(np.min((curve_arr - peak) / peak)) * 100.0
        total_ret = ((curve_arr[-1] / initial_nav) - 1.0) * 100.0
        mar = total_ret / max_dd if max_dd > 0 else 0.0
        
        return {
            "total_return_pct": total_ret,
            "max_dd_pct": max_dd,
            "mar": mar,
            "mean_r": float(df["r_multiple"].mean())
        }

class PromotionGuard:
    """Validates proposals via shadow backtest before modifying production state."""
    @staticmethod
    def audit_and_promote(ledger: list, current_policy: dict) -> tuple:
        if len(ledger) < MIN_SAMPLE_FOR_PROMOTION:
            return current_policy, False, "INSUFFICIENT_SUPPORT"

        df = pd.DataFrame(ledger)
        baseline_metrics = ShadowReplayEngine.evaluate_shadow_policy(ledger, current_policy)
        
        # Create proposed candidate
        proposed_policy = json.loads(json.dumps(current_policy))
        modifications = []

        # 1. Audit BTC Momentum Buckets
        for bucket, grp in df.groupby("btc_momentum_bucket"):
            if len(grp) >= MIN_SAMPLE_FOR_PROMOTION:
                exp = grp["r_multiple"].mean()
                if exp < -0.15:
                    proposed_policy["btc_risk_multipliers"][bucket] = 0.625 # 0.25% floor
                    modifications.append(f"Throttle {bucket} to 0.625x (Exp: {exp:+.2f}R)")
                elif exp > 0.30:
                    proposed_policy["btc_risk_multipliers"][bucket] = 1.25  # 0.50% ceiling
                    modifications.append(f"Expand {bucket} to 1.25x (Exp: {exp:+.2f}R)")
                else:
                    proposed_policy["btc_risk_multipliers"][bucket] = 1.0

        # 2. Audit Volatility Buckets
        for bucket, grp in df.groupby("volatility_bucket"):
            if len(grp) >= MIN_SAMPLE_FOR_PROMOTION:
                exp = grp["r_multiple"].mean()
                if exp < -0.15:
                    proposed_policy["vol_risk_multipliers"][bucket] = 0.625
                    modifications.append(f"Throttle {bucket} to 0.625x (Exp: {exp:+.2f}R)")

        if not modifications:
            return current_policy, False, "NO_PROPOSALS_TRIGGERED"

        # 3. Shadow Replay Candidate vs Baseline
        shadow_metrics = ShadowReplayEngine.evaluate_shadow_policy(ledger, proposed_policy)

        # 4. Certification Gates
        dd_pass = shadow_metrics["max_dd_pct"] <= (baseline_metrics["max_dd_pct"] + 0.05)
        mar_pass = shadow_metrics["mar"] >= (baseline_metrics["mar"] - 0.01)

        if dd_pass and mar_pass:
            return proposed_policy, True, f"PROMOTED: {'; '.join(modifications)}"
        else:
            return current_policy, False, f"REJECTED BY SHADOW GATE (Shadow MAR: {shadow_metrics['mar']:.3f} vs Base MAR: {baseline_metrics['mar']:.3f})"

class AdaptiveClosedLoopManager:
    """Master production controller for trade ingestion, telemetry, and execution policy."""
    def __init__(self, ledger_path=TELEMETRY_LOG_PATH, policy_path=POLICY_STATE_PATH):
        self.ledger_path = ledger_path
        self.policy_path = policy_path
        self.ledger = self._load_ledger()
        self.policy = self._load_policy()

    def _load_ledger(self) -> list:
        if os.path.exists(self.ledger_path):
            try:
                with open(self.ledger_path, "r") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _load_policy(self) -> dict:
        if os.path.exists(self.policy_path):
            try:
                with open(self.policy_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "base_risk_pct": BASE_RISK_PER_TRADE,
            "btc_risk_multipliers": {"STRONG_BULL": 1.0, "WEAK_BULL": 1.0},
            "vol_risk_multipliers": {"HIGH_VOL": 1.0, "NORM_VOL": 1.0},
            "symbol_penalty_box": {}  # {symbol: "YYYY-MM-DD"}
        }

    def record_completed_trade(self, raw_trade: dict) -> tuple:
        """Atomically ingests a completed trade, updates cooldowns, and audits policy promotion."""
        trade_id = generate_trade_id(raw_trade)
        
        # Deduplication check
        for existing in self.ledger:
            if existing.get("trade_id") == trade_id:
                return False, f"DUPLICATE_IGNORED: {trade_id}"

        raw_trade["trade_id"] = trade_id
        self.ledger.append(raw_trade)

        # Asset Penalty Box Logic (2 losses in last 14 days -> 5-day bench)
        if not raw_trade["win"]:
            sym = raw_trade["symbol"]
            exit_dt = datetime.strptime(raw_trade["exit_dt"], "%Y-%m-%d")
            recent_sym_losses = [
                t for t in self.ledger 
                if t["symbol"] == sym and not t["win"] 
                and (exit_dt - datetime.strptime(t["exit_dt"], "%Y-%m-%d")).days <= 14
            ]
            if len(recent_sym_losses) >= 2:
                unlock_date = (exit_dt + timedelta(days=5)).strftime("%Y-%m-%d")
                self.policy["symbol_penalty_box"][sym] = unlock_date

        # Execute Shadow Validation & Policy Promotion
        updated_policy, promoted, msg = PromotionGuard.audit_and_promote(self.ledger, self.policy)
        self.policy = updated_policy

        # Atomic writes
        atomic_json_dump(self.ledger, self.ledger_path)
        atomic_json_dump(self.policy, self.policy_path)

        return True, msg

    def is_symbol_eligible(self, symbol: str, current_dt_str: str) -> bool:
        """Checks if asset is currently benched in the penalty box."""
        if symbol in self.policy.get("symbol_penalty_box", {}):
            unlock_date = self.policy["symbol_penalty_box"][symbol]
            if current_dt_str < unlock_date:
                return False
            else:
                del self.policy["symbol_penalty_box"][symbol]
                atomic_json_dump(self.policy, self.policy_path)
        return True

    def get_effective_risk_pct(self, telemetry: dict) -> float:
        """Returns bounded risk per trade clamped to [0.25%, 0.50%]."""
        btc_bucket = telemetry.get("btc_momentum_bucket", "NORM_VOL")
        vol_bucket = telemetry.get("volatility_bucket", "NORM_VOL")

        btc_mult = self.policy["btc_risk_multipliers"].get(btc_bucket, 1.0)
        vol_mult = self.policy["vol_risk_multipliers"].get(vol_bucket, 1.0)

        scalar = min(btc_mult, vol_mult)
        target_risk = self.policy["base_risk_pct"] * scalar
        return float(np.clip(target_risk, MIN_RISK_PER_TRADE, MAX_RISK_PER_TRADE))
