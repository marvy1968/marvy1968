#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any


POLICY_FILE = "learned_policy.json"

MIN_EDGE_A = float(os.getenv("TP_MIN_EDGE_A", "0.10"))
MIN_EDGE_B = float(os.getenv("TP_MIN_EDGE_B", "0.07"))
MIN_CONF_A = float(os.getenv("TP_MIN_CONF_A", "0.84"))
MIN_CONF_B = float(os.getenv("TP_MIN_CONF_B", "0.72"))

MAX_PROPS_PER_GAME = int(os.getenv("TP_MAX_PROPS_PER_GAME", "2"))
MAX_PROPS_PER_TEAM = int(os.getenv("TP_MAX_PROPS_PER_TEAM", "1"))
MAX_KELLY = float(os.getenv("TP_MAX_KELLY", "0.10"))

BLOCK_CONFLICTING_SIDES = os.getenv("TP_BLOCK_CONFLICTING_SIDES", "true").lower() == "true"
BLOCK_SUSPICIOUS = os.getenv("TP_BLOCK_SUSPICIOUS", "false").lower() == "true"
SOFT_CONF_CAP_A = float(os.getenv("TP_SOFT_CONF_CAP_A", "0.87"))
SOFT_CONF_CAP_B = float(os.getenv("TP_SOFT_CONF_CAP_B", "0.82"))
HARD_CONF_CAP = float(os.getenv("TP_HARD_CONF_CAP", "0.91"))
SMALL_GAP_BLOCK = float(os.getenv("TP_SMALL_GAP_BLOCK", "1.5"))


def load_learned_policy() -> dict[str, Any]:
    if not os.path.exists(POLICY_FILE):
        return {}
    try:
        with open(POLICY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


LEARNED_POLICY = load_learned_policy()


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, str) and v.endswith("%"):
            return float(v[:-1])
        return float(v)
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


def _strength(item: dict[str, Any]) -> str:
    return str(item.get("strength", "")).strip().upper()


def _game(item: dict[str, Any], payload: dict[str, Any] | None = None) -> str:
    game = str(item.get("game", "")).strip()
    if game:
        return game
    if payload:
        return f"{payload.get('away_team', '')} @ {payload.get('home_team', '')}".strip()
    home = item.get("home_team", "")
    away = item.get("away_team", "")
    return f"{away} @ {home}".strip()


def _line_key(v: Any) -> str:
    if v is None:
        return "None"
    try:
        return f"{float(v):.3f}"
    except Exception:
        return str(v)


def _summary(item: dict[str, Any], payload: dict[str, Any] | None = None) -> str:
    if item.get("summary"):
        return str(item["summary"])
    game = _game(item, payload)
    team = str(item.get("team", "")).strip()
    side = str(item.get("side", "")).strip()
    line = item.get("line")
    return f"{game} | TEAM_TOTAL | {team} {side} {line}".strip()


def _sort_key(item: dict[str, Any]) -> tuple[float, float, float]:
    return (
        _to_float(item.get("score", 0.0), 0.0),
        _conf(item.get("confidence", 0.0)),
        _edge(item.get("edge", item.get("display_edge", 0.0))),
    )


def _apply_learned_adjustments(item: dict[str, Any]) -> None:
    edge = _edge(item.get("edge", item.get("display_edge", 0.0)))
    conf = _conf(item.get("confidence", 0.0))
    strength = _strength(item)

    if "team_total_penalty" in LEARNED_POLICY:
        edge *= max(0.50, min(_to_float(LEARNED_POLICY["team_total_penalty"], 1.0), 1.25))

    if "team_total_boost" in LEARNED_POLICY:
        edge *= max(0.75, min(_to_float(LEARNED_POLICY["team_total_boost"], 1.0), 1.35))

    scale_key = f"{strength}_confidence_scale"
    if scale_key in LEARNED_POLICY:
        conf *= max(0.80, min(_to_float(LEARNED_POLICY[scale_key], 1.0), 1.10))

    if item.get("suspicious") and "suspicious_penalty" in LEARNED_POLICY:
        conf -= max(0.0, min(_to_float(LEARNED_POLICY["suspicious_penalty"], 0.0), 0.20))

    item["edge"] = round(max(0.0, edge), 4)
    item["confidence"] = round(max(0.0, min(conf, 1.0)), 4)


def _compress_confidence(item: dict[str, Any]) -> None:
    conf = _conf(item.get("confidence", 0.0))
    edge = _edge(item.get("edge", 0.0))
    strength = _strength(item)

    cap = SOFT_CONF_CAP_A if strength == "A" else SOFT_CONF_CAP_B
    cap += min(edge * 0.35, 0.05)
    cap = min(cap, HARD_CONF_CAP)

    item["confidence"] = round(min(max(conf, 0.50), cap), 4)


def _rebuild_fields(item: dict[str, Any], payload: dict[str, Any] | None = None) -> None:
    edge = _edge(item.get("edge", item.get("display_edge", 0.0)))
    conf = _conf(item.get("confidence", 0.0))
    kelly = _to_float(item.get("kelly_quarter", item.get("kelly", 0.0)), 0.0)
    kelly = min(kelly, MAX_KELLY)

    score = (edge * 100.0) + (conf * 10.0)
    if item.get("suspicious"):
        score -= 8.0

    edge_pct = round(edge * 100.0, 2)

    item["game"] = _game(item, payload)
    item["summary"] = _summary(item, payload)
    item["display_edge"] = edge_pct
    item["display_edge_text"] = f"{edge_pct}%"
    item["confidence"] = round(conf, 4)
    item["kelly_quarter"] = round(kelly, 4)
    item["score"] = round(score, 4)


def _passes_thresholds(item: dict[str, Any]) -> bool:
    edge = _edge(item.get("edge", 0.0))
    conf = _conf(item.get("confidence", 0.0))
    strength = _strength(item)

    if strength == "A":
        return edge >= MIN_EDGE_A and conf >= MIN_CONF_A
    if strength == "B":
        return edge >= MIN_EDGE_B and conf >= MIN_CONF_B
    return edge >= MIN_EDGE_B and conf >= MIN_CONF_B


def _small_gap(item: dict[str, Any]) -> bool:
    proj = item.get("projection", item.get("proj", item.get("projected_team_total")))
    line = item.get("line")
    if proj is None or line is None:
        return False
    return abs(_to_float(proj, 0.0) - _to_float(line, 0.0)) < SMALL_GAP_BLOCK


def _is_conflict(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if _norm(a.get("game")) != _norm(b.get("game")):
        return False
    if _norm(a.get("team")) != _norm(b.get("team")):
        return False
    if _line_key(a.get("line")) != _line_key(b.get("line")):
        return False
    side_a = _norm(a.get("side"))
    side_b = _norm(b.get("side"))
    return bool(side_a and side_b and side_a != side_b)


def enforce_team_props_policy(policy_input: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(policy_input)

    props = (
        out.get("best_team_props")
        or out.get("ranked_team_props")
        or out.get("candidate_team_props")
        or out.get("team_props")
        or []
    )
    if not isinstance(props, list):
        props = []

    prepared: list[dict[str, Any]] = []
    drop_counts = defaultdict(int)

    for raw in props:
        if not isinstance(raw, dict):
            drop_counts["non_dict"] += 1
            continue

        item = dict(raw)
        item["team"] = str(item.get("team", "")).strip()
        item["side"] = str(item.get("side", "")).strip().lower()

        if BLOCK_SUSPICIOUS and item.get("suspicious"):
            drop_counts["blocked_suspicious"] += 1
            continue

        if _small_gap(item):
            drop_counts["small_gap"] += 1
            continue

        _apply_learned_adjustments(item)
        _compress_confidence(item)
        _rebuild_fields(item, payload)

        if not _passes_thresholds(item):
            drop_counts["below_threshold"] += 1
            continue

        prepared.append(item)

    prepared = sorted(prepared, key=_sort_key, reverse=True)

    if BLOCK_CONFLICTING_SIDES:
        conflict_pruned: list[dict[str, Any]] = []
        for item in prepared:
            if any(_is_conflict(item, existing) for existing in conflict_pruned):
                drop_counts["conflict_pruned"] += 1
                continue
            conflict_pruned.append(item)
        prepared = conflict_pruned

    final: list[dict[str, Any]] = []
    per_game = defaultdict(int)
    per_team = defaultdict(int)

    for item in prepared:
        game = _norm(item.get("game"))
        team = _norm(item.get("team"))

        if per_game[game] >= MAX_PROPS_PER_GAME:
            drop_counts["over_game_limit"] += 1
            continue

        if per_team[(game, team)] >= MAX_PROPS_PER_TEAM:
            drop_counts["over_team_limit"] += 1
            continue

        final.append(item)
        per_game[game] += 1
        per_team[(game, team)] += 1

    out["best_team_props"] = final
    out["policy_summary"] = {
        "props_after_policy": len(final),
        "max_kelly": MAX_KELLY,
        "max_props_per_game": MAX_PROPS_PER_GAME,
        "max_props_per_team": MAX_PROPS_PER_TEAM,
        "drop_counts": dict(drop_counts),
    }
    return out


if __name__ == "__main__":
    sample = {
        "best_team_props": [
            {
                "game": "North Dakota St Bison @ Michigan St Spartans",
                "team": "North Dakota St Bison",
                "side": "under",
                "line": 68.0,
                "proj": 65.1,
                "edge": 0.2754,
                "confidence": 0.90,
                "kelly_quarter": 0.12,
                "strength": "A",
            },
            {
                "game": "North Dakota St Bison @ Michigan St Spartans",
                "team": "North Dakota St Bison",
                "side": "over",
                "line": 68.0,
                "proj": 68.4,
                "edge": 0.081,
                "confidence": 0.70,
                "kelly_quarter": 0.05,
                "strength": "B",
            },
            {
                "game": "North Dakota St Bison @ Michigan St Spartans",
                "team": "Michigan St Spartans",
                "side": "under",
                "line": 91.0,
                "proj": 87.6,
                "edge": 0.287,
                "confidence": 0.90,
                "kelly_quarter": 0.12,
                "strength": "A",
            },
        ]
    }

    result = enforce_team_props_policy(sample)
    print("TEAM PROPS POLICY TEST")
    print("kept:", len(result.get("best_team_props", [])))
    for item in result.get("best_team_props", []):
        print(
            f"- {item.get('summary')} | edge={item.get('display_edge_text')} | "
            f"conf={item.get('confidence')} | kelly_q={item.get('kelly_quarter')}"
        )
    print(result.get("policy_summary", {}))
