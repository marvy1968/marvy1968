#!/usr/bin/env python3
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any


MAX_GAME_ALERTS = int(os.getenv("ENTRY_MAX_GAME_ALERTS", "2"))
MAX_GAME_ALERTS_PER_MARKET = int(os.getenv("ENTRY_MAX_GAME_ALERTS_PER_MARKET", "1"))
MAX_TEAM_PROPS_PER_GAME = int(os.getenv("ENTRY_MAX_TEAM_PROPS_PER_GAME", "2"))
MAX_MIDDLES_PER_GAME = int(os.getenv("ENTRY_MAX_MIDDLES_PER_GAME", "1"))

MIN_GAME_EDGE = float(os.getenv("ENTRY_MIN_GAME_EDGE", "0.09"))
MIN_TEAM_PROP_EDGE = float(os.getenv("ENTRY_MIN_TEAM_PROP_EDGE", "0.08"))
MIN_MIDDLE_SIZE_A = int(os.getenv("ENTRY_MIN_MIDDLE_SIZE_A", "6"))
MIN_MIDDLE_SIZE_B = int(os.getenv("ENTRY_MIN_MIDDLE_SIZE_B", "4"))

EARLY_GAME_MINUTES_BLOCK_TOTAL = int(os.getenv("ENTRY_EARLY_GAME_MINUTES_BLOCK_TOTAL", "18"))
LATE_GAME_SECONDS_BLOCK_SPREAD = int(os.getenv("ENTRY_LATE_GAME_SECONDS_BLOCK_SPREAD", "90"))
LATE_GAME_SECONDS_BLOCK_TOTAL = int(os.getenv("ENTRY_LATE_GAME_SECONDS_BLOCK_TOTAL", "120"))
LATE_GAME_SECONDS_BLOCK_TEAM_TOTAL = int(os.getenv("ENTRY_LATE_GAME_SECONDS_BLOCK_TEAM_TOTAL", "120"))

BLOCK_CONFLICTING_TEAM_PROPS = os.getenv("ENTRY_BLOCK_CONFLICTING_TEAM_PROPS", "true").lower() == "true"
BLOCK_LOW_CONF_MIDDLES = os.getenv("ENTRY_BLOCK_LOW_CONF_MIDDLES", "true").lower() == "true"
MIN_MIDDLE_CONF = float(os.getenv("ENTRY_MIN_MIDDLE_CONF", "0.79"))

BLOCK_FINALS = os.getenv("ENTRY_BLOCK_FINALS", "true").lower() == "true"
BLOCK_HALFTIME = os.getenv("ENTRY_BLOCK_HALFTIME", "true").lower() == "true"
BLOCK_SUSPICIOUS_LATE = os.getenv("ENTRY_BLOCK_SUSPICIOUS_LATE", "true").lower() == "true"
BLOCK_ENDGAME_MONEYLINE = os.getenv("ENTRY_BLOCK_ENDGAME_MONEYLINE", "true").lower() == "true"
ENDGAME_MONEYLINE_SECONDS = int(os.getenv("ENTRY_ENDGAME_MONEYLINE_SECONDS", "180"))


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return default
            if v.endswith("%"):
                return float(v[:-1]) / 100.0
        return float(v)
    except Exception:
        return default


def _to_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(float(v))
    except Exception:
        return default


def _conf(v: Any) -> float:
    x = _to_float(v, 0.0)
    if x > 1.0:
        x /= 100.0
    return max(0.0, min(x, 1.0))


def _edge(v: Any) -> float:
    x = _to_float(v, 0.0)
    if x > 1.0:
        x /= 100.0
    return max(0.0, x)


def _norm(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().split())


def _game(payload: dict[str, Any], item: dict[str, Any] | None = None) -> str:
    if item and item.get("game"):
        return str(item["game"])
    return f"{payload.get('away_team', '')} @ {payload.get('home_team', '')}".strip()


def _remaining_seconds(payload: dict[str, Any]) -> int:
    minutes = _to_int(payload.get("minutes_left", 0), 0)
    seconds = _to_int(payload.get("seconds_left", 0), 0)
    return max(0, (minutes * 60) + seconds)


def _market(item: dict[str, Any]) -> str:
    raw = _norm(item.get("market"))
    aliases = {
        "h2h": "moneyline",
        "ml": "moneyline",
        "money line": "moneyline",
        "spreads": "spread",
        "totals": "total",
        "team total": "team_total",
        "team totals": "team_total",
        "tt": "team_total",
        "middle": "middle",
        "middles": "middle",
    }
    return aliases.get(raw, raw)


def _status_text(payload: dict[str, Any]) -> str:
    return _norm(payload.get("status_detail", ""))


def _is_final_or_dead(payload: dict[str, Any]) -> bool:
    """
    Do not kill OT transitions.
    """
    status = _status_text(payload)
    rem = _remaining_seconds(payload)
    mode = _norm(payload.get("mode"))

    if bool(payload.get("completed", False)):
        return True

    if "final" in status or "end of game" in status or "end of 2nd half" in status:
        return True

    if rem == 0 and mode == "2h" and "overtime" not in status and "ot" not in status:
        return True

    return False


def _is_halftime(payload: dict[str, Any]) -> bool:
    status = _status_text(payload)
    rem = _remaining_seconds(payload)
    mode = _norm(payload.get("mode"))

    if "halftime" in status:
        return True
    if mode == "1h" and rem == 0:
        return True
    return False


def _sort_key(item: dict[str, Any]) -> tuple[float, float, float]:
    """
    Stable sorting:
    score first, then true edge, then confidence.
    """
    score = _to_float(item.get("score", 0.0), 0.0)
    edge = _edge(item.get("edge", item.get("display_edge", 0.0)))
    conf = _conf(item.get("confidence", 0.0))
    return (score, edge, conf)


def _dedup_game_alerts(alerts: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    seen = set()

    for item in sorted(alerts, key=_sort_key, reverse=True):
        key = (
            _norm(_game(payload, item)),
            _market(item),
            _norm(item.get("team")),
            _norm(item.get("side")),
            str(item.get("line")),
        )
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)

    return kept


def filter_game_alerts(alerts: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Main gatekeeper for live game alerts.
    """
    if not isinstance(alerts, list) or not alerts:
        return []

    if BLOCK_FINALS and _is_final_or_dead(payload):
        return []

    if BLOCK_HALFTIME and _is_halftime(payload):
        return []

    remaining = _remaining_seconds(payload)
    minutes_left = _to_int(payload.get("minutes_left", 0), 0)
    suspicious_any = bool(payload.get("suspicious_any", False))

    deduped = _dedup_game_alerts(alerts, payload)

    kept: list[dict[str, Any]] = []
    per_game = defaultdict(int)
    per_game_market = defaultdict(int)

    for item in sorted(deduped, key=_sort_key, reverse=True):
        market = _market(item)
        edge = _edge(item.get("edge", item.get("display_edge", 0.0)))
        game_id = _norm(_game(payload, item))

        # 1) Minimum value
        if edge < MIN_GAME_EDGE:
            continue

        # 2) Early first-half total noise
        if market == "total" and payload.get("mode") == "1h" and minutes_left >= EARLY_GAME_MINUTES_BLOCK_TOTAL:
            continue

        # 3) Late dead zones
        if market == "total" and remaining <= LATE_GAME_SECONDS_BLOCK_TOTAL:
            continue

        if market == "spread" and remaining <= LATE_GAME_SECONDS_BLOCK_SPREAD:
            continue

        # 4) Endgame moneyline safety
        if BLOCK_ENDGAME_MONEYLINE and market == "moneyline" and remaining <= ENDGAME_MONEYLINE_SECONDS:
            continue

        # 5) Suspicious late-game block
        if BLOCK_SUSPICIOUS_LATE and suspicious_any and remaining <= 600:
            if market in {"spread", "total", "moneyline"}:
                continue

        # 6) Volume caps
        if per_game[game_id] >= MAX_GAME_ALERTS:
            continue

        if per_game_market[(game_id, market)] >= MAX_GAME_ALERTS_PER_MARKET:
            continue

        kept.append(item)
        per_game[game_id] += 1
        per_game_market[(game_id, market)] += 1

    return kept


def _team_prop_conflict(a: dict[str, Any], b: dict[str, Any], payload: dict[str, Any]) -> bool:
    if _norm(_game(payload, a)) != _norm(_game(payload, b)):
        return False

    team_a = _norm(a.get("team"))
    team_b = _norm(b.get("team"))
    side_a = _norm(a.get("side"))
    side_b = _norm(b.get("side"))
    line_a = str(a.get("line"))
    line_b = str(b.get("line"))

    return bool(team_a == team_b and line_a == line_b and side_a and side_b and side_a != side_b)


def filter_team_props(props: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(props, list) or not props:
        return []

    if BLOCK_FINALS and _is_final_or_dead(payload):
        return []

    if BLOCK_HALFTIME and _is_halftime(payload):
        return []

    remaining = _remaining_seconds(payload)
    suspicious_any = bool(payload.get("suspicious_any", False))
    game = _norm(_game(payload))
    kept: list[dict[str, Any]] = []

    for item in sorted(props, key=_sort_key, reverse=True):
        edge = _edge(item.get("edge", item.get("display_edge", 0.0)))

        if edge < MIN_TEAM_PROP_EDGE:
            continue

        if remaining <= LATE_GAME_SECONDS_BLOCK_TEAM_TOTAL:
            continue

        # If the whole game context is suspicious, be much stricter late
        if suspicious_any and remaining <= 900:
            continue

        if BLOCK_CONFLICTING_TEAM_PROPS and any(_team_prop_conflict(item, x, payload) for x in kept):
            continue

        same_game_count = sum(1 for x in kept if _norm(_game(payload, x)) == game)
        if same_game_count >= MAX_TEAM_PROPS_PER_GAME:
            continue

        kept.append(item)

    return kept


def filter_middles(middles: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(middles, list) or not middles:
        return []

    if BLOCK_FINALS and _is_final_or_dead(payload):
        return []

    if BLOCK_HALFTIME and _is_halftime(payload):
        return []

    game = _norm(_game(payload))
    kept: list[dict[str, Any]] = []

    for item in sorted(middles, key=_sort_key, reverse=True):
        strength = str(item.get("strength", "")).strip().upper()
        size = _to_int(item.get("size", 0), 0)
        conf = _conf(item.get("confidence", 0.0))

        if BLOCK_LOW_CONF_MIDDLES and conf < MIN_MIDDLE_CONF:
            continue

        if strength == "A" and size < MIN_MIDDLE_SIZE_A:
            continue

        if strength == "B" and size < MIN_MIDDLE_SIZE_B:
            continue

        same_game_count = sum(1 for x in kept if _norm(str(x.get("game", ""))) == game)
        if same_game_count >= MAX_MIDDLES_PER_GAME:
            continue

        kept.append(item)

    return kept


if __name__ == "__main__":
    payload_dead = {
        "home_team": "Iowa State Cyclones",
        "away_team": "Tennessee State Tigers",
        "minutes_left": 0,
        "seconds_left": 0,
        "mode": "2h",
        "status_detail": "End of 2nd Half",
        "completed": False,
        "suspicious_any": False,
    }

    payload_suspicious = {
        "home_team": "Tennessee Volunteers",
        "away_team": "Miami (OH) RedHawks",
        "minutes_left": 3,
        "seconds_left": 12,
        "mode": "1h",
        "status_detail": "3:12 - 1st Half",
        "completed": False,
        "suspicious_any": True,
    }

    alerts = [
        {"game": "Tennessee St Tigers @ Iowa State Cyclones", "market": "moneyline", "team": "Iowa State Cyclones", "edge": 0.42, "confidence": 0.95, "score": 50.0},
        {"game": "Tennessee St Tigers @ Iowa State Cyclones", "market": "spread", "team": "Tennessee State Tigers", "line": 33.5, "edge": 0.26, "confidence": 0.81, "score": 27.0},
    ]

    props = [
        {"game": "Miami (OH) RedHawks @ Tennessee Volunteers", "market": "team_total", "team": "Tennessee Volunteers", "side": "under", "line": 88.0, "edge": 0.35, "confidence": 0.85, "score": 43.0},
    ]

    print("ENTRY TIMING FILTER REPAIR TEST")
    print("dead-window alerts kept:", len(filter_game_alerts(alerts, payload_dead)))
    print("suspicious-late props kept:", len(filter_team_props(props, payload_suspicious)))
