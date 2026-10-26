#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from team_props_policy import enforce_team_props_policy

SNAPSHOT_LOG = "bet_snapshots.jsonl"


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


def _clamp(x: float, low: float, high: float) -> float:
    return max(low, min(x, high))


def _strength(conf: float) -> str:
    if conf >= 0.88:
        return "A"
    if conf >= 0.75:
        return "B"
    return "C"


def _kelly(edge: float, conf: float) -> float:
    raw = (edge * 0.20) + max(0.0, conf - 0.50) * 0.05
    return round(_clamp(raw, 0.0, 0.08), 4)


def _game_from_payload(base_payload: Dict[str, Any]) -> str:
    return f"{base_payload.get('away_team', '')} @ {base_payload.get('home_team', '')}".strip()


def _remaining_seconds(base_payload: Dict[str, Any]) -> int:
    minutes = int(_to_float(base_payload.get("minutes_left", 0), 0))
    seconds = int(_to_float(base_payload.get("seconds_left", 0), 0))
    return max(0, minutes * 60 + seconds)


def _prop_confidence(diff: float, remaining_seconds: int, suspicious: bool) -> float:
    conf = 0.65 + min(diff / 18.0, 0.25)

    if remaining_seconds <= 120:
        conf -= 0.10
    elif remaining_seconds <= 300:
        conf -= 0.05

    if suspicious:
        conf -= 0.08

    return round(_clamp(conf, 0.50, 0.92), 4)


def _build_team_prop(
    *,
    game: str,
    team: str,
    side: str,
    line: float,
    proj: float,
    confidence: float,
    suspicious: bool,
) -> Dict[str, Any]:
    diff = abs(proj - line)
    edge = round(_clamp(diff / 18.0, 0.0, 0.35), 4)

    strength = _strength(confidence)
    kelly_q = _kelly(edge, confidence)
    edge_pct = round(edge * 100.0, 2)

    summary = (
        f"{team} team total {side} {round(line, 2)} | "
        f"proj={round(proj, 2)} | edge={edge_pct}% | "
        f"conf={round(confidence * 100.0, 1)}% | "
        f"suspicious={suspicious} | strength={strength} | "
        f"kelly_q={kelly_q}"
    )

    return {
        "game": game,
        "market": "team_total",
        "team": team,
        "side": side,
        "line": round(line, 2),
        "proj": round(proj, 2),
        "projection": round(proj, 2),
        "projected_team_total": round(proj, 2),
        "edge": edge,
        "display_edge": edge_pct,
        "display_edge_text": f"{edge_pct}%",
        "confidence": round(confidence, 4),
        "strength": strength,
        "kelly_quarter": kelly_q,
        "suspicious": suspicious,
        "score": round((edge * 100.0) + (confidence * 10.0) - (8.0 if suspicious else 0.0), 4),
        "summary": summary,
    }


def _candidate_props(base_payload: Dict[str, Any], market_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    game = _game_from_payload(base_payload)

    home_team = str(base_payload.get("home_team", "")).strip()
    away_team = str(base_payload.get("away_team", "")).strip()

    projected_home = _to_float(
        market_payload.get("projected_home", base_payload.get("projected_home", 0.0)),
        0.0,
    )
    projected_away = _to_float(
        market_payload.get("projected_away", base_payload.get("projected_away", 0.0)),
        0.0,
    )

    home_line = _to_float(market_payload.get("home_team_total_line", 0.0), 0.0)
    away_line = _to_float(market_payload.get("away_team_total_line", 0.0), 0.0)

    suspicious = bool(
        base_payload.get("suspicious_any", False)
        or market_payload.get("suspicious_any", False)
    )
    remaining = _remaining_seconds(base_payload)

    props: List[Dict[str, Any]] = []

    if home_team and home_line > 0 and projected_home > 0:
        diff = abs(projected_home - home_line)
        conf = _prop_confidence(diff, remaining, suspicious)
        side = "over" if projected_home > home_line else "under"
        props.append(
            _build_team_prop(
                game=game,
                team=home_team,
                side=side,
                line=home_line,
                proj=projected_home,
                confidence=conf,
                suspicious=suspicious,
            )
        )

    if away_team and away_line > 0 and projected_away > 0:
        diff = abs(projected_away - away_line)
        conf = _prop_confidence(diff, remaining, suspicious)
        side = "over" if projected_away > away_line else "under"
        props.append(
            _build_team_prop(
                game=game,
                team=away_team,
                side=side,
                line=away_line,
                proj=projected_away,
                confidence=conf,
                suspicious=suspicious,
            )
        )

    return props


def _append_snapshot(base_payload: Dict[str, Any], top_bets: List[Dict[str, Any]]) -> None:
    row = {
        "timestamp": int(time.time()),
        "home_team": base_payload.get("home_team"),
        "away_team": base_payload.get("away_team"),
        "top_bets": top_bets,
    }
    with open(SNAPSHOT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def run_team_props_bridge(
    base_payload: Dict[str, Any],
    market_payload: Dict[str, Any],
    write_logs: bool = False,
) -> Dict[str, Any]:
    candidates = _candidate_props(base_payload, market_payload)

    policy_input = {"best_team_props": candidates}
    policy_output = enforce_team_props_policy(policy_input, payload=base_payload)
    best_team_props = policy_output.get("best_team_props", [])

    if write_logs and best_team_props:
        _append_snapshot(base_payload, best_team_props)

    return {
        "candidate_team_props": candidates,
        "policy_output": policy_output,
        "best_team_props": best_team_props,
        "write_logs": write_logs,
    }


if __name__ == "__main__":
    sample_base = {
        "home_team": "Michigan St Spartans",
        "away_team": "North Dakota St Bison",
        "minutes_left": 10,
        "seconds_left": 0,
        "suspicious_any": False,
    }
    sample_market = {
        "projected_home": 87.6,
        "projected_away": 65.1,
        "home_team_total_line": 91.0,
        "away_team_total_line": 68.0,
        "suspicious_any": False,
    }

    result = run_team_props_bridge(sample_base, sample_market, write_logs=False)
    print("TEAM PROPS BRIDGE REPAIR TEST")
    print("candidates:", len(result["candidate_team_props"]))
    print("kept:", len(result["best_team_props"]))
    for item in result["best_team_props"]:
        print(f"- {item['summary']}")
