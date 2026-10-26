#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, List, Dict


def _key_prop(item: Dict[str, Any]) -> tuple:
    return (
        item.get("game"),
        item.get("team"),
        item.get("side"),
    )


def _key_edge(item: Dict[str, Any]) -> tuple:
    return (
        item.get("game"),
        item.get("market"),
        item.get("side"),
    )


def _better_prop(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    # prefer higher edge
    if a.get("edge", 0) > b.get("edge", 0):
        return a
    return b


def _better_edge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    # prioritize edge then confidence
    if a.get("edge", 0) > b.get("edge", 0):
        return a
    if a.get("confidence", 0) > b.get("confidence", 0):
        return a
    return b


# =========================
# TEAM PROPS CONSOLIDATION
# =========================
def consolidate_team_props(props: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best_map: Dict[tuple, Dict[str, Any]] = {}

    for p in props:
        key = _key_prop(p)

        if key not in best_map:
            best_map[key] = p
        else:
            best_map[key] = _better_prop(p, best_map[key])

    return list(best_map.values())


# =========================
# GAME EDGE CONSOLIDATION
# =========================
def consolidate_edges(edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best_map: Dict[tuple, Dict[str, Any]] = {}

    for e in edges:
        key = _key_edge(e)

        if key not in best_map:
            best_map[key] = e
        else:
            best_map[key] = _better_edge(e, best_map[key])

    return list(best_map.values())


# =========================
# FINAL GAME SUMMARY
# =========================
def build_game_summary(
    edges: List[Dict[str, Any]],
    props: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    games: Dict[str, Dict[str, Any]] = {}

    for e in edges:
        game = e.get("game")
        if game not in games:
            games[game] = {
                "game": game,
                "primary_edge": None,
                "secondary_edges": [],
                "team_props": [],
            }

        # assign primary edge (highest edge)
        if not games[game]["primary_edge"]:
            games[game]["primary_edge"] = e
        else:
            if e.get("edge", 0) > games[game]["primary_edge"].get("edge", 0):
                games[game]["secondary_edges"].append(games[game]["primary_edge"])
                games[game]["primary_edge"] = e
            else:
                games[game]["secondary_edges"].append(e)

    for p in props:
        game = p.get("game")
        if game not in games:
            games[game] = {
                "game": game,
                "primary_edge": None,
                "secondary_edges": [],
                "team_props": [],
            }

        games[game]["team_props"].append(p)

    return list(games.values())


# =========================
# MAIN ENTRY
# =========================
def consolidate_all(
    edges: List[Dict[str, Any]],
    props: List[Dict[str, Any]],
) -> Dict[str, Any]:

    clean_edges = consolidate_edges(edges)
    clean_props = consolidate_team_props(props)

    summary = build_game_summary(clean_edges, clean_props)

    return {
        "edges": clean_edges,
        "props": clean_props,
        "summary": summary,
    }


# =========================
# TEST MODE
# =========================
if __name__ == "__main__":

    sample_edges = [
        {"game": "A @ B", "market": "total", "side": "under", "edge": 20, "confidence": 90},
        {"game": "A @ B", "market": "total", "side": "under", "edge": 25, "confidence": 95},
    ]

    sample_props = [
        {"game": "A @ B", "team": "A", "side": "under", "edge": 22},
        {"game": "A @ B", "team": "A", "side": "under", "edge": 28},
    ]

    result = consolidate_all(sample_edges, sample_props)

    print("\n=== CONSOLIDATED OUTPUT ===\n")
    print(result)

