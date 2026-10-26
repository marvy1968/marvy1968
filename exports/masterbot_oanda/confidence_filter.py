#!/usr/bin/env python3
from __future__ import annotations

from typing import Dict, Any, List

# =========================
# GLOBAL THRESHOLDS
# =========================
MIN_CONFIDENCE = 85
S_TIER_CONFIDENCE = 90

MIN_PROP_EDGE = 0.18
MIN_MIDDLE_WINDOW = 5

# =========================
# EDGE FILTER
# =========================
def filter_edges(edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results = []

    for e in edges:
        conf = e.get("confidence", 0)

        if conf < MIN_CONFIDENCE:
            continue

        # Tier tagging
        if conf >= S_TIER_CONFIDENCE:
            e["tier"] = "S"
        else:
            e["tier"] = "A"

        results.append(e)

    return results


# =========================
# TEAM PROP FILTER
# =========================
def filter_props(props: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results = []

    for p in props:
        conf = p.get("confidence", 0)
        edge = p.get("edge", 0)

        if conf < MIN_CONFIDENCE:
            continue

        if edge < MIN_PROP_EDGE:
            continue

        if conf >= S_TIER_CONFIDENCE:
            p["tier"] = "S"
        else:
            p["tier"] = "A"

        results.append(p)

    return results


# =========================
# MIDDLE FILTER
# =========================
def filter_middles(middles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results = []

    for m in middles:
        conf = m.get("confidence", 0)
        window = m.get("window_size", 0)

        if conf < MIN_CONFIDENCE:
            continue

        if window < MIN_MIDDLE_WINDOW:
            continue

        # Tier logic
        if conf >= S_TIER_CONFIDENCE and window >= 8:
            m["tier"] = "S"
        else:
            m["tier"] = "A"

        results.append(m)

    return results


# =========================
# OPTIONAL SCORING SYSTEM
# =========================
def score_play(item: Dict[str, Any]) -> float:
    edge = float(item.get("edge", 0))
    conf = float(item.get("confidence", 0))
    window = float(item.get("window_size", 0))

    return (edge * 0.5) + (conf * 0.3) + (window * 0.2)


# =========================
# TOP PLAYS (OPTIONAL)
# =========================
def get_top_plays(items: List[Dict[str, Any]], top_n: int = 3) -> List[Dict[str, Any]]:
    return sorted(items, key=score_play, reverse=True)[:top_n]


# =========================
# TEST MODE
# =========================
if __name__ == "__main__":

    sample_edges = [
        {"game": "A @ B", "confidence": 82, "edge": 20},
        {"game": "A @ B", "confidence": 87, "edge": 18},
        {"game": "A @ B", "confidence": 92, "edge": 25},
    ]

    sample_props = [
        {"game": "A @ B", "confidence": 84, "edge": 0.20},
        {"game": "A @ B", "confidence": 88, "edge": 0.22},
    ]

    sample_middles = [
        {"game": "A @ B", "confidence": 86, "window_size": 4},
        {"game": "A @ B", "confidence": 91, "window_size": 10},
    ]

    print("\n=== EDGES ===")
    print(filter_edges(sample_edges))

    print("\n=== PROPS ===")
    print(filter_props(sample_props))

    print("\n=== MIDDLES ===")
    print(filter_middles(sample_middles))
