#!/usr/bin/env python3
from __future__ import annotations

import os
from typing import Any


# =========================
# ENV CONFIG
# =========================
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "72"))
MIN_MODEL_PROB = float(os.getenv("MIN_MODEL_PROB", "0.54"))
MAX_MARKET_PROB = float(os.getenv("MAX_MARKET_PROB", "0.80"))

MIN_TIME_REMAINING_SEC = int(os.getenv("MIN_TIME_REMAINING_SEC", "120"))
MAX_SCORE_DIFF = int(os.getenv("MAX_SCORE_DIFF", "22"))

ALLOW_BLOWOUTS = os.getenv("ALLOW_BLOWOUTS", "false").lower() == "true"
ALLOW_GARBAGE_TIME = os.getenv("ALLOW_GARBAGE_TIME", "false").lower() == "true"

MAX_TOTAL_DEVIATION = float(os.getenv("MAX_TOTAL_DEVIATION", "24"))
MAX_SPREAD_DEVIATION = float(os.getenv("MAX_SPREAD_DEVIATION", "12"))

MIN_SOURCE_COUNT = int(os.getenv("MIN_SOURCE_COUNT", "1"))
MAX_VOLATILITY = float(os.getenv("MAX_VOLATILITY", "999"))

DEDUPE_WINDOW_SEC = int(os.getenv("DEDUPE_WINDOW_SEC", "180"))
MAX_SIGNALS_PER_GAME = int(os.getenv("MAX_SIGNALS_PER_GAME", "2"))
MAX_FINAL_SIGNALS = int(os.getenv("MAX_FINAL_SIGNALS", "8"))

PREFER_LIVE_ONLY = os.getenv("PREFER_LIVE_ONLY", "true").lower() == "true"

MAX_BETS_PER_CYCLE = int(os.getenv("MAX_BETS_PER_CYCLE", "3"))
ALLOW_D_TIER = os.getenv("ALLOW_D_TIER", "false").lower() == "true"
ALLOW_C_TIER = os.getenv("ALLOW_C_TIER", "true").lower() == "true"
MIN_STRONG_ONLY = os.getenv("MIN_STRONG_ONLY", "false").lower() == "true"


# =========================
# HELPERS
# =========================
def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _sport_key(payload: dict[str, Any]) -> str:
    return str(payload.get("sport_key", "")).lower()


def _seconds_remaining(payload: dict[str, Any]) -> int:
    minutes_left = _safe_float(payload.get("minutes_left", 0.0))
    seconds_left = _safe_float(payload.get("seconds_left", 0.0))
    return int(minutes_left * 60 + seconds_left)


def _is_live(payload: dict[str, Any]) -> bool:
    period = _safe_int(payload.get("period", 0))
    seconds_remaining = _seconds_remaining(payload)
    return period > 0 and seconds_remaining > 0


def _is_garbage_time(payload: dict[str, Any]) -> bool:
    score_diff = abs(_safe_float(payload.get("score_diff", 0.0)))
    sec_left = _seconds_remaining(payload)
    period = _safe_int(payload.get("period", 0))

    if period < 2:
        return False

    if sec_left <= MIN_TIME_REMAINING_SEC and score_diff >= 10:
        return True

    if not ALLOW_BLOWOUTS and score_diff > MAX_SCORE_DIFF:
        return True

    return False


def _tier_from_confidence(conf: float) -> str:
    if conf >= 86:
        return "A"
    if conf >= 78:
        return "B"
    if conf >= 70:
        return "C"
    return "D"


def _market_type(market: str) -> str:
    m = str(market).lower()
    if "total" in m:
        return "total"
    if "spread" in m:
        return "spread"
    if "money" in m or m == "ml":
        return "moneyline"
    return m


def _team_strength_modifier(win_pct: float) -> float:
    wp = _safe_float(win_pct, 0.0)
    if wp < 0.35:
        return -2.0
    if wp > 0.65:
        return 1.5
    return 0.0


def _base_game_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "sport_key": payload.get("sport_key"),
        "game": payload.get("game") or f"{payload.get('away_team', '')} @ {payload.get('home_team', '')}".strip(),
        "home_team": payload.get("home_team"),
        "away_team": payload.get("away_team"),
        "period": _safe_int(payload.get("period", 0)),
        "minutes_left": _safe_float(payload.get("minutes_left", 0.0)),
        "seconds_left": _safe_float(payload.get("seconds_left", 0.0)),
        "score_diff": _safe_float(payload.get("score_diff", 0.0)),
    }


# =========================
# CANDIDATE BUILDING
# =========================
def _build_candidates_from_edge(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Builds candidate signals directly from edge payload fields if detect_edges
    did not already provide a candidates list.
    """
    game_fields = _base_game_fields(payload)

    candidates: list[dict[str, Any]] = []

    # spread
    home_spread_edge = _safe_float(payload.get("home_spread_edge", 0.0))
    away_spread_edge = _safe_float(payload.get("away_spread_edge", 0.0))
    home_cover_prob = _safe_float(payload.get("home_cover_prob", 0.0))
    away_cover_prob = _safe_float(payload.get("away_cover_prob", 0.0))

    if home_spread_edge > 0:
        candidates.append({
            **game_fields,
            "market": "spread",
            "selection": payload.get("home_team"),
            "edge": home_spread_edge,
            "model_prob": home_cover_prob,
            "market_prob": max(0.0, home_cover_prob - home_spread_edge),
            "market_line": payload.get("home_spread_line"),
            "system_value": payload.get("model_margin"),
        })

    if away_spread_edge > 0:
        candidates.append({
            **game_fields,
            "market": "spread",
            "selection": payload.get("away_team"),
            "edge": away_spread_edge,
            "model_prob": away_cover_prob,
            "market_prob": max(0.0, away_cover_prob - away_spread_edge),
            "market_line": payload.get("home_spread_line"),
            "system_value": payload.get("model_margin"),
        })

    # totals
    over_edge = _safe_float(payload.get("over_edge", 0.0))
    under_edge = _safe_float(payload.get("under_edge", 0.0))
    over_prob = _safe_float(payload.get("over_prob", 0.0))
    under_prob = _safe_float(payload.get("under_prob", 0.0))

    if over_edge > 0:
        candidates.append({
            **game_fields,
            "market": "total",
            "selection": "over",
            "edge": over_edge,
            "model_prob": over_prob,
            "market_prob": max(0.0, over_prob - over_edge),
            "market_line": payload.get("market_total"),
            "system_value": payload.get("model_total"),
        })

    if under_edge > 0:
        candidates.append({
            **game_fields,
            "market": "total",
            "selection": "under",
            "edge": under_edge,
            "model_prob": under_prob,
            "market_prob": max(0.0, under_prob - under_edge),
            "market_line": payload.get("market_total"),
            "system_value": payload.get("model_total"),
        })

    # moneyline
    home_ml_edge = _safe_float(payload.get("home_ml_edge", 0.0))
    away_ml_edge = _safe_float(payload.get("away_ml_edge", 0.0))
    home_ml_prob = _safe_float(payload.get("home_ml_prob", 0.0))
    away_ml_prob = _safe_float(payload.get("away_ml_prob", 0.0))

    if home_ml_edge > 0:
        candidates.append({
            **game_fields,
            "market": "moneyline",
            "selection": payload.get("home_team"),
            "edge": home_ml_edge,
            "model_prob": home_ml_prob,
            "market_prob": max(0.0, home_ml_prob - home_ml_edge),
            "market_line": payload.get("home_ml"),
            "system_value": payload.get("model_margin"),
        })

    if away_ml_edge > 0:
        candidates.append({
            **game_fields,
            "market": "moneyline",
            "selection": payload.get("away_team"),
            "edge": away_ml_edge,
            "model_prob": away_ml_prob,
            "market_prob": max(0.0, away_ml_prob - away_ml_edge),
            "market_line": payload.get("away_ml"),
            "system_value": payload.get("model_margin"),
        })

    return candidates


# =========================
# SCORING
# =========================
def _score_signal(sig: dict[str, Any], payload: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []

    edge = _safe_float(sig.get("edge", 0.0))
    model_prob = _safe_float(sig.get("model_prob", 0.0))
    market_prob = _safe_float(sig.get("market_prob", 0.0))
    market = _market_type(sig.get("market", ""))
    score_diff = abs(_safe_float(payload.get("score_diff", 0.0)))
    sec_left = _seconds_remaining(payload)
    suspicious = bool(payload.get("suspicious_any", False))

    # source count / volatility are optional
    source_count = _safe_int(sig.get("source_count", payload.get("source_count", 1)), 1)
    volatility = _safe_float(sig.get("volatility", payload.get("volatility", 0.0)), 0.0)

    price_mismatch = abs(model_prob - market_prob)

    score = 0.0
    score += edge * 100.0 * 0.85
    score += model_prob * 20.0
    score += min(12.0, price_mismatch * 100.0 * 0.50)
    score += min(8.0, source_count * 2.0)

    # penalties
    score -= min(10.0, volatility * 0.75)
    score -= min(10.0, score_diff * 0.50)

    if sec_left < 300:
        score -= min(8.0, (300 - sec_left) / 40.0)

    if suspicious and market == "total":
        score -= 10.0
        reasons.append("suspicious_total_penalty")

    if market == "total":
        market_total = _safe_float(payload.get("market_total", 0.0))
        model_total = _safe_float(payload.get("model_total", 0.0))
        if market_total and abs(model_total - market_total) > MAX_TOTAL_DEVIATION:
            score -= 15.0
            reasons.append("excess_total_deviation")

    if market == "spread":
        home_line = _safe_float(payload.get("home_spread_line", 0.0))
        model_margin = _safe_float(payload.get("model_margin", 0.0))
        # home spread convention: negative = home favored
        system_home_spread = -model_margin
        if abs(system_home_spread - home_line) > MAX_SPREAD_DEVIATION:
            score -= 15.0
            reasons.append("excess_spread_deviation")

    return round(score, 2), reasons


def _confidence_from_score(score: float) -> float:
    # tuned to make 70-90 a realistic band
    conf = 55.0 + (score * 0.42)
    return round(_clip(conf, 0.0, 99.0), 2)


# =========================
# FILTERS
# =========================
def _passes_money_filter(sig: dict[str, Any], payload: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    edge = _safe_float(sig.get("edge", 0.0))
    model_prob = _safe_float(sig.get("model_prob", 0.0))
    market_prob = _safe_float(sig.get("market_prob", 0.0))
    market = _market_type(sig.get("market", ""))
    real_edge = abs(model_prob - market_prob)

    if model_prob < MIN_MODEL_PROB:
        reasons.append("model_prob_below_floor")
    if market_prob > MAX_MARKET_PROB and market != "total":
        reasons.append("market_prob_too_expensive")
    if real_edge < 0.05:
        reasons.append("real_edge_too_small")
    if edge < 0.04:
        reasons.append("edge_too_small")

    # kill crazy totals
    if market == "total":
        model_total = _safe_float(payload.get("model_total", 0.0))
        market_total = _safe_float(payload.get("market_total", 0.0))
        if market_total and abs(model_total - market_total) > MAX_TOTAL_DEVIATION:
            reasons.append("total_too_far_from_market")

    return len(reasons) == 0, reasons


def _passes_game_state_filter(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    if PREFER_LIVE_ONLY and not _is_live(payload):
        # keep pregame candidates only if not using live-only mode
        pass

    if not ALLOW_GARBAGE_TIME and _is_garbage_time(payload):
        reasons.append("garbage_time")

    if not ALLOW_BLOWOUTS and abs(_safe_float(payload.get("score_diff", 0.0))) > MAX_SCORE_DIFF:
        reasons.append("blowout")

    if _is_live(payload) and _seconds_remaining(payload) < MIN_TIME_REMAINING_SEC:
        reasons.append("too_late")

    return len(reasons) == 0, reasons


def _dedupe_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # keep best one per market+selection
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for s in signals:
        key = (_market_type(s.get("market", "")), str(s.get("selection", "")))
        prev = best.get(key)
        if prev is None or (s.get("confidence", 0), s.get("edge", 0)) > (prev.get("confidence", 0), prev.get("edge", 0)):
            best[key] = s

    out = list(best.values())
    out.sort(key=lambda x: (x.get("confidence", 0), x.get("edge", 0)), reverse=True)

    # per-game cap
    return out[:MAX_SIGNALS_PER_GAME]


# =========================
# MAIN API
# =========================
def filter_signals(edge_output: dict[str, Any]) -> dict[str, Any]:
    """
    Accepts edge output / payload and returns intelligence object:
    {
      signal_count,
      status,
      alerts_to_send,
      rejected_count,
      rejected_reasons,
      scored_signals
    }
    """
    payload = dict(edge_output or {})
    candidates = payload.get("candidates") or payload.get("signals") or []
    if not candidates:
        candidates = _build_candidates_from_edge(payload)

    if not candidates:
        return {
            "signal_count": 0,
            "status": "no_candidates",
            "alerts_to_send": [],
            "rejected_count": 0,
            "rejected_reasons": [],
            "scored_signals": [],
        }

    scored_signals: list[dict[str, Any]] = []
    rejected_reasons: list[str] = []

    game_ok, game_reasons = _passes_game_state_filter(payload)
    if not game_ok:
        return {
            "signal_count": 0,
            "status": "game_state_rejected",
            "alerts_to_send": [],
            "rejected_count": len(candidates),
            "rejected_reasons": game_reasons,
            "scored_signals": [],
        }

    for raw_sig in candidates:
        sig = dict(raw_sig)
        sig["market_type"] = _market_type(sig.get("market", ""))

        money_ok, money_reasons = _passes_money_filter(sig, payload)
        if not money_ok:
            rejected_reasons.extend(money_reasons)
            continue

        score, score_reasons = _score_signal(sig, payload)
        confidence = _confidence_from_score(score)

        # spread-only team strength adjustment
        if sig.get("market_type") == "spread":
            away_team = str(payload.get("away_team", ""))
            home_team = str(payload.get("home_team", ""))
            selection = str(sig.get("selection", ""))

            away_win_pct = _safe_float(payload.get("away_win_rate", 0.0), 0.0)
            home_win_pct = _safe_float(payload.get("home_win_rate", 0.0), 0.0)

            away_mod = _team_strength_modifier(away_win_pct)
            home_mod = _team_strength_modifier(home_win_pct)

            if selection == away_team:
                confidence += (away_mod - home_mod)
            elif selection == home_team:
                confidence += (home_mod - away_mod)

            confidence = round(_clip(confidence, 0.0, 99.0), 2)

        tier = _tier_from_confidence(confidence)

        sig["score"] = score
        sig["confidence"] = confidence
        sig["tier"] = tier
        sig["reasons"] = score_reasons
        sig["suspicious"] = bool(payload.get("suspicious_any", False))

        if confidence < MIN_CONFIDENCE:
            rejected_reasons.append("confidence_below_floor")
            continue

        if MIN_STRONG_ONLY and tier not in {"A", "B"}:
            rejected_reasons.append("not_strong_enough")
            continue

        if tier == "D" and not ALLOW_D_TIER:
            rejected_reasons.append("d_tier_blocked")
            continue

        if tier == "C" and not ALLOW_C_TIER:
            rejected_reasons.append("c_tier_blocked")
            continue

        scored_signals.append(sig)

    if not scored_signals:
        return {
            "signal_count": 0,
            "status": "no_signals",
            "alerts_to_send": [],
            "rejected_count": len(candidates),
            "rejected_reasons": rejected_reasons[:20],
            "scored_signals": [],
        }

    deduped = _dedupe_signals(scored_signals)
    deduped.sort(key=lambda x: (x.get("confidence", 0), x.get("edge", 0)), reverse=True)
    final_alerts = deduped[:MAX_FINAL_SIGNALS]
    final_alerts = final_alerts[:MAX_BETS_PER_CYCLE]

    return {
        "signal_count": len(final_alerts),
        "status": "signals_ready" if final_alerts else "no_signals",
        "alerts_to_send": final_alerts,
        "rejected_count": max(0, len(candidates) - len(final_alerts)),
        "rejected_reasons": rejected_reasons[:20],
        "scored_signals": scored_signals[:20],
    }


if __name__ == "__main__":
    sample = {
        "game": "Chicago Bulls @ Philadelphia 76ers",
        "sport_key": "basketball_nba",
        "home_team": "Philadelphia 76ers",
        "away_team": "Chicago Bulls",
        "market_total": 238.5,
        "home_spread_line": -14.5,
        "period": 2,
        "minutes_left": 2,
        "seconds_left": 44,
        "score_diff": 21,
        "model_margin": 19.14,
        "model_total": 220.5,
        "home_cover_prob": 0.8735,
        "away_cover_prob": 0.1265,
        "over_prob": 0.0008,
        "under_prob": 0.9992,
        "home_ml_prob": 0.9952,
        "away_ml_prob": 0.0048,
        "home_spread_edge": 0.14,
        "away_spread_edge": 0.0,
        "over_edge": 0.0,
        "under_edge": 0.14,
        "home_ml_edge": 0.14,
        "away_ml_edge": 0.0,
        "suspicious_any": True,
    }

    out = filter_signals(sample)
    print(out["status"])
    print("signal_count:", out["signal_count"])
    for a in out["alerts_to_send"]:
        print(a["market"], a["selection"], a["edge"], a["confidence"], a["tier"])
