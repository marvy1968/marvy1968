#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

LEARNED_POLICY_FILE = os.getenv("LEARNED_POLICY_FILE", "learned_policy.json")

DEFAULT_POLICY = {
    "disabled_markets": [],
    "min_confidence": 0.62,
    "min_edge_spread": 0.02,
    "min_edge_ml": 0.015,
    "min_edge_total": 0.03,
    "max_kelly": 0.06,
    "allowed_strengths": ["A", "B"],
    "min_ev_percent": 0.0,
    "min_spread_conf_for_c": 0.67,
    "min_ml_conf_for_c": 0.65,
    "min_total_conf_for_c": 0.68,
    "suppress_suspicious_totals": True,
    "suppress_suspicious_all": True,
    "max_alerts_per_poll": 8,
    "max_alerts_per_game": 2,
    "max_alerts_per_market_per_game": 1,
    "enable_late_game_lockout": True,
    "late_game_lockout_minutes": 10,
    "late_game_lockout_model_win_prob": 0.95,
    "late_game_lockout_min_diff": 6,
    "late_game_lockout_allow_middles": True,
}


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return default
            if s.endswith("%"):
                return float(s[:-1]) / 100.0
        return float(v)
    except Exception:
        return default


def _to_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "y", "on"):
        return True
    if s in ("false", "0", "no", "n", "off"):
        return False
    return default


def _load_learned_policy() -> Dict[str, Any]:
    if not os.path.exists(LEARNED_POLICY_FILE):
        return {}
    try:
        with open(LEARNED_POLICY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _merged_policy() -> Dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    policy.update(_load_learned_policy())
    return policy


def _market_key(raw: str) -> str:
    m = str(raw or "").lower().strip()
    if m in ("spread", "spreads"):
        return "spread"
    if m in ("moneyline", "ml", "h2h"):
        return "ml"
    if m in ("total", "totals"):
        return "total"
    if m in ("middle", "middles"):
        return "middle"
    return m


def _model_no_expected_ot(signal: Dict[str, Any]) -> bool:
    minutes_left = _to_float(signal.get("minutes_left", 999), 999)
    seconds_left = _to_float(signal.get("seconds_left", 0), 0)
    score_diff = _to_float(signal.get("score_diff", 0), 0)
    pace = str(signal.get("pace", "normal")).lower()
    pressure = str(signal.get("pressure", "normal")).lower()
    foul_window = _to_bool(signal.get("foul_window", False), False)
    comeback_window = _to_bool(signal.get("comeback_window", False), False)

    time_left = minutes_left + (seconds_left / 60.0)

    if time_left <= 1.5 and score_diff >= 4:
        return True
    if time_left <= 3.0 and score_diff >= 5 and not foul_window:
        return True
    if time_left <= 5.0 and score_diff >= 7 and pace != "foul_chaos":
        return True
    if time_left <= 7.0 and score_diff >= 9 and pressure != "high":
        return True
    if time_left <= 10.0 and score_diff >= 12 and not comeback_window:
        return True

    return False


def _late_game_lockout(signal: Dict[str, Any], policy: Dict[str, Any]) -> bool:
    if not _to_bool(policy.get("enable_late_game_lockout", True), True):
        return False

    market = _market_key(signal.get("market", ""))
    if market == "middle" and _to_bool(policy.get("late_game_lockout_allow_middles", True), True):
        return False

    minutes_left = _to_float(signal.get("minutes_left", 999), 999)
    seconds_left = _to_float(signal.get("seconds_left", 0), 0)
    score_diff = _to_float(signal.get("score_diff", 0), 0)
    leader_prob = _to_float(signal.get("leader_win_prob", 0.0), 0.0)

    time_left = minutes_left + (seconds_left / 60.0)
    late_cutoff = _to_float(policy.get("late_game_lockout_minutes", 10), 10)
    min_diff = _to_float(policy.get("late_game_lockout_min_diff", 6), 6)
    win_prob_cutoff = _to_float(policy.get("late_game_lockout_model_win_prob", 0.95), 0.95)

    if time_left > late_cutoff:
        return False
    if leader_prob < win_prob_cutoff:
        return False
    if score_diff < min_diff:
        return False
    if not _model_no_expected_ot(signal):
        return False

    return True


def _passes_thresholds(signal: Dict[str, Any], policy: Dict[str, Any]) -> bool:
    market = _market_key(signal.get("market", ""))
    conf = _to_float(signal.get("confidence", 0.0), 0.0)
    edge = _to_float(signal.get("edge", 0.0), 0.0)
    ev_percent = _to_float(signal.get("ev_percent", 0.0), 0.0)
    strength = str(signal.get("strength", "D")).upper()
    suspicious = _to_bool(signal.get("suspicious", False), False)

    if market in set(policy.get("disabled_markets", [])):
        return False

    if conf < _to_float(policy.get("min_confidence", 0.62), 0.62):
        return False

    if ev_percent < _to_float(policy.get("min_ev_percent", 0.0), 0.0):
        return False

    if suspicious and _to_bool(policy.get("suppress_suspicious_all", True), True):
        return False

    if market == "total" and suspicious and _to_bool(policy.get("suppress_suspicious_totals", True), True):
        return False

    if market == "spread" and edge < _to_float(policy.get("min_edge_spread", 0.02), 0.02):
        return False
    if market == "ml" and edge < _to_float(policy.get("min_edge_ml", 0.015), 0.015):
        return False
    if market == "total" and edge < _to_float(policy.get("min_edge_total", 0.03), 0.03):
        return False

    allowed_strengths = list(policy.get("allowed_strengths", ["A", "B"]))
    if strength in ("A", "B"):
        if strength not in allowed_strengths:
            return False
    elif strength == "C":
        if market == "spread" and conf < _to_float(policy.get("min_spread_conf_for_c", 0.67), 0.67):
            return False
        if market == "ml" and conf < _to_float(policy.get("min_ml_conf_for_c", 0.65), 0.65):
            return False
        if market == "total" and conf < _to_float(policy.get("min_total_conf_for_c", 0.68), 0.68):
            return False
    else:
        return False

    return True


def _apply_caps(alerts: List[Dict[str, Any]], policy: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    max_poll = int(_to_float(policy.get("max_alerts_per_poll", 8), 8))
    max_game = int(_to_float(policy.get("max_alerts_per_game", 2), 2))
    max_market_game = int(_to_float(policy.get("max_alerts_per_market_per_game", 1), 1))

    kept: List[Dict[str, Any]] = []
    per_game: Dict[str, int] = {}
    per_market_game: Dict[str, int] = {}

    drop_counts = {
        "rejected_by_policy": 0,
        "game_cap": 0,
        "market_cap": 0,
        "poll_cap": 0,
        "late_game_lockout": 0,
    }

    for s in alerts:
        game = str(s.get("game", ""))
        market = _market_key(s.get("market", ""))
        gk = game
        mgk = f"{game}|{market}"

        if len(kept) >= max_poll:
            drop_counts["poll_cap"] += 1
            continue

        if per_game.get(gk, 0) >= max_game:
            drop_counts["game_cap"] += 1
            continue

        if per_market_game.get(mgk, 0) >= max_market_game:
            drop_counts["market_cap"] += 1
            continue

        kept.append(s)
        per_game[gk] = per_game.get(gk, 0) + 1
        per_market_game[mgk] = per_market_game.get(mgk, 0) + 1

    return kept, drop_counts


def _inject_display_fields(signal: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    s = dict(signal)

    # This is the key add:
    # prefer the new bot-vs-book edge display, fall back to old edge if missing
    s["display_edge"] = s.get("win_pct_edge", round(_to_float(s.get("edge", 0.0)) * 100.0, 2))

    # hard-cap production kelly
    s["kelly_quarter"] = min(
        _to_float(policy.get("max_kelly", 0.06), 0.06),
        _to_float(s.get("kelly_quarter", 0.0), 0.0)
    )

    return s


def enforce_production_policy(intelligence: Dict[str, Any]) -> Dict[str, Any]:
    policy = _merged_policy()

    ranked = list(intelligence.get("ranked_signals", []) or [])
    alerts: List[Dict[str, Any]] = []

    rejected_count = 0
    late_game_lockout_count = 0

    for sig in ranked:
        if _late_game_lockout(sig, policy):
            late_game_lockout_count += 1
            continue

        if _passes_thresholds(sig, policy):
            alerts.append(_inject_display_fields(sig, policy))
        else:
            rejected_count += 1

    capped_alerts, cap_drops = _apply_caps(alerts, policy)
    cap_drops["rejected_by_policy"] = rejected_count
    cap_drops["late_game_lockout"] = late_game_lockout_count

    out = dict(intelligence)
    out["alerts_to_send"] = capped_alerts
    out["policy_summary"] = {
        "allowed_strengths": policy.get("allowed_strengths", ["A", "B"]),
        "suppress_suspicious_totals": _to_bool(policy.get("suppress_suspicious_totals", True), True),
        "suppress_suspicious_all": _to_bool(policy.get("suppress_suspicious_all", True), True),
        "max_production_kelly": _to_float(policy.get("max_kelly", 0.06), 0.06),
        "min_confidence": _to_float(policy.get("min_confidence", 0.62), 0.62),
        "min_edge_spread": _to_float(policy.get("min_edge_spread", 0.02), 0.02),
        "min_edge_ml": _to_float(policy.get("min_edge_ml", 0.015), 0.015),
        "min_edge_total": _to_float(policy.get("min_edge_total", 0.03), 0.03),
        "disabled_markets": list(policy.get("disabled_markets", [])),
        "late_game_lockout_enabled": _to_bool(policy.get("enable_late_game_lockout", True), True),
        "late_game_lockout_minutes": _to_float(policy.get("late_game_lockout_minutes", 10), 10),
        "late_game_lockout_model_win_prob": _to_float(policy.get("late_game_lockout_model_win_prob", 0.95), 0.95),
        "ranked_after_policy": len(ranked),
        "alerts_after_policy": len(capped_alerts),
        "drop_counts": cap_drops,
    }
    return out


if __name__ == "__main__":
    sample = {
        "ranked_signals": [
            {
                "game": "Maryland Terrapins @ North Carolina Tar Heels",
                "market": "total",
                "selection": "under 150.5",
                "line": 150.5,
                "confidence": 0.752,
                "edge": 0.22,
                "win_pct_edge": 22.82,
                "ev_percent": 43.56,
                "kelly_quarter": 0.08,
                "strength": "B",
                "suspicious": False,
                "minutes_left": 0,
                "seconds_left": 31,
                "leader": "North Carolina Tar Heels",
                "leader_win_prob": 0.8075,
                "score_diff": 8,
                "pace": "foul_chaos",
                "pressure": "high",
                "foul_window": True,
                "comeback_window": True,
            }
        ]
    }

    out = enforce_production_policy(sample)
    print("PRODUCTION POLICY DISPLAY EDGE TEST")
    print(out["policy_summary"])
    for a in out["alerts_to_send"]:
        print("display_edge =", a.get("display_edge"))
        print(a["selection"])
