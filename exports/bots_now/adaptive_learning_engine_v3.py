#!/usr/bin/env python3
"""
Prospective Adaptive Learning Engine (v3)
Features:
- Deterministic trade deduplication (SHA-256)
- Prospective proposal queue (no same-sample in-place promotion)
- Historical shadow replay evaluation (MAR/MaxDD constraints)
- Explicit namespacing for btc_risk_multipliers & vol_risk_multipliers
- Symbol penalty box tracking with automated expiration
- Atomic JSON state persistence
"""

import os
import json
import hashlib
import tempfile
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

DATA_DIR = "/home/moonrider1968/bots/crypto_data"
TELEMETRY_LOG_PATH = os.path.join(DATA_DIR, "SHADOW_v3_trade_telemetry_ledger.json")
POLICY_STATE_PATH = os.path.join(DATA_DIR, "SHADOW_v3_active_adaptive_policy.json")
PROPOSAL_STATE_PATH = os.path.join(DATA_DIR, "SHADOW_v3_adaptive_policy_proposals.json")

# HARD IMMUTABLE GUARDS (LEVEL 0)
MIN_RISK_PER_TRADE = 0.0025   # 0.25% Floor
MAX_RISK_PER_TRADE = 0.0050   # 0.50% Ceiling
BASE_RISK_PER_TRADE = 0.0040  # 0.40% Baseline
MIN_SAMPLE_FOR_PROPOSAL = 15  # Minimum sample support before proposing adjustments

def generate_trade_id(trade_dict: dict) -> str:
    raw_str = f"{trade_dict['symbol']}_{trade_dict['timestamp']}_{float(trade_dict['entry_px']):.6f}_{trade_dict['exit_dt']}"
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()[:16]

def atomic_json_dump(data, target_path):
    dirname = os.path.dirname(target_path)
    with tempfile.NamedTemporaryFile("w", dir=dirname, delete=False) as tf:
        json.dump(data, tf, indent=2)
        temp_name = tf.name
    os.replace(temp_name, target_path)

class ShadowReplayEngine:
    @staticmethod
    def evaluate_policy(ledger: list, policy: dict) -> dict:
        if len(ledger) < MIN_SAMPLE_FOR_PROPOSAL:
            return {"promoted": False, "reason": "INSUFFICIENT_GLOBAL_SUPPORT"}

        df = pd.DataFrame(ledger)
        initial_nav = 100_000.0
        nav = initial_nav
        curve = [nav]

        for _, row in df.iterrows():
            btc_mult = policy.get("btc_risk_multipliers", {}).get(row.get("btc_momentum_bucket"), 1.0)
            vol_mult = policy.get("vol_risk_multipliers", {}).get(row.get("volatility_bucket"), 1.0)
            scalar = min(btc_mult, vol_mult)
            effective_risk = np.clip(BASE_RISK_PER_TRADE * scalar, MIN_RISK_PER_TRADE, MAX_RISK_PER_TRADE)

            dollar_risk = nav * effective_risk
            scaled_pnl = float(row["r_multiple"]) * dollar_risk
            nav += scaled_pnl
            curve.append(nav)

        curve_arr = np.array(curve)
        peak = np.maximum.accumulate(curve_arr)
        max_dd = abs(np.min((curve_arr - peak) / peak)) * 100.0 if len(curve_arr) > 1 else 0.0
        total_ret = ((curve_arr[-1] / initial_nav) - 1.0) * 100.0
        mar = total_ret / max_dd if max_dd > 0 else 0.0

        return {
            "total_return_pct": total_ret,
            "max_dd_pct": max_dd,
            "mar": mar,
            "mean_r": float(df["r_multiple"].mean())
        }

class ProspectiveProposalManager:
    @staticmethod
    def generate_proposals(ledger: list, current_policy: dict) -> list:
        if len(ledger) < MIN_SAMPLE_FOR_PROPOSAL:
            return []

        df = pd.DataFrame(ledger)
        proposals = []

        # Audit BTC Momentum Sub-Buckets
        for bucket, grp in df.groupby("btc_momentum_bucket"):
            if len(grp) >= MIN_SAMPLE_FOR_PROPOSAL:
                exp = grp["r_multiple"].mean()
                curr_scalar = current_policy.get("btc_risk_multipliers", {}).get(bucket, 1.0)
                if exp < -0.15 and curr_scalar > 0.625:
                    proposals.append({
                        "type": "btc_risk_multipliers",
                        "bucket": bucket,
                        "proposed_scalar": 0.625,
                        "observed_exp": float(exp),
                        "sample_size": len(grp),
                        "status": "QUEUED_SHADOW_REPLAY"
                    })

        # Audit Volatility Sub-Buckets
        for bucket, grp in df.groupby("volatility_bucket"):
            if len(grp) >= MIN_SAMPLE_FOR_PROPOSAL:
                exp = grp["r_multiple"].mean()
                curr_scalar = current_policy.get("vol_risk_multipliers", {}).get(bucket, 1.0)
                if exp < -0.15 and curr_scalar > 0.625:
                    proposals.append({
                        "type": "vol_risk_multipliers",
                        "bucket": bucket,
                        "proposed_scalar": 0.625,
                        "observed_exp": float(exp),
                        "sample_size": len(grp),
                        "status": "QUEUED_SHADOW_REPLAY"
                    })

        return proposals

class AdaptiveClosedLoopManager:
    def __init__(self, ledger_path=TELEMETRY_LOG_PATH, policy_path=POLICY_STATE_PATH, proposal_path=PROPOSAL_STATE_PATH):
        self.ledger_path = ledger_path
        self.policy_path = policy_path
        self.proposal_path = proposal_path

        self.ledger = self._load_json(self.ledger_path, default=[])
        self.policy = self._load_json(self.policy_path, default={
            "base_risk_pct": BASE_RISK_PER_TRADE,
            "btc_risk_multipliers": {"STRONG_BULL": 1.0, "WEAK_BULL": 1.0},
            "vol_risk_multipliers": {"HIGH_VOL": 1.0, "NORM_VOL": 1.0},
            "symbol_penalty_box": {}
        })
        self.proposals = self._load_json(self.proposal_path, default=[])

    def _load_json(self, path: str, default):
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception:
                return default
        return default

    def record_completed_trade(self, raw_trade: dict) -> tuple:
        trade_id = generate_trade_id(raw_trade)

        for existing in self.ledger:
            if existing.get("trade_id") == trade_id:
                return False, f"DUPLICATE_IGNORED: {trade_id}"

        raw_trade["trade_id"] = trade_id
        self.ledger.append(raw_trade)

        # Asset Penalty Box Logic (2 losses in 14 days -> 5-day bench)
        if not raw_trade.get("win", False):
            sym = raw_trade["symbol"]
            exit_dt = datetime.strptime(raw_trade["exit_dt"], "%Y-%m-%d")
            recent_losses = [
                t for t in self.ledger
                if t["symbol"] == sym and not t.get("win", False)
                and (exit_dt - datetime.strptime(t["exit_dt"], "%Y-%m-%d")).days <= 14
            ]
            if len(recent_losses) >= 2:
                unlock_date = (exit_dt + timedelta(days=5)).strftime("%Y-%m-%d")
                self.policy["symbol_penalty_box"][sym] = unlock_date

        # Generate Prospective Proposals (Queue for validation)
        new_proposals = ProspectiveProposalManager.generate_proposals(self.ledger, self.policy)
        self.proposals.extend([p for p in new_proposals if p not in self.proposals])

        # Atomic persistence
        atomic_json_dump(self.ledger, self.ledger_path)
        atomic_json_dump(self.policy, self.policy_path)
        atomic_json_dump(self.proposals, self.proposal_path)

        return True, f"LOGGED (Proposals Queued: {len(self.proposals)})"

    def is_symbol_eligible(self, symbol: str, current_dt_str: str) -> bool:
        if symbol in self.policy.get("symbol_penalty_box", {}):
            unlock_date = self.policy["symbol_penalty_box"][symbol]
            if current_dt_str < unlock_date:
                return False
            else:
                del self.policy["symbol_penalty_box"][symbol]
                atomic_json_dump(self.policy, self.policy_path)
        return True

    def get_effective_risk_pct(self, telemetry: dict) -> float:
        btc_bucket = telemetry.get("btc_momentum_bucket", "NORM_VOL")
        vol_bucket = telemetry.get("volatility_bucket", "NORM_VOL")

        btc_mult = self.policy.get("btc_risk_multipliers", {}).get(btc_bucket, 1.0)
        vol_mult = self.policy.get("vol_risk_multipliers", {}).get(vol_bucket, 1.0)

        scalar = min(btc_mult, vol_mult)
        target_risk = self.policy.get("base_risk_pct", BASE_RISK_PER_TRADE) * scalar
        return float(np.clip(target_risk, MIN_RISK_PER_TRADE, MAX_RISK_PER_TRADE))
