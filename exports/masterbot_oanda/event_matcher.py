from __future__ import annotations

import json
from typing import Any
from rapidfuzz import fuzz


def load_team_aliases(path: str = "data_engine/team_aliases.json") -> dict[str, list[str]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_alias_lookup(alias_map: dict[str, list[str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical, aliases in alias_map.items():
        canonical_key = normalize_team_name(canonical)
        lookup[canonical_key] = canonical
        for alias in aliases:
            lookup[normalize_team_name(alias)] = canonical
    return lookup


def normalize_team_name(name: str) -> str:
    text = (name or "").lower().strip()
    replacements = {
        "st.": "saint",
        "&": "and",
        "'": "",
        ".": "",
        "-": " ",
        "university": "",
        "college": "",
        "the ": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return " ".join(text.split())


def canonical_team_key(name: str, alias_lookup: dict[str, str]) -> str:
    text = normalize_team_name(name)
    return alias_lookup.get(text, text)


def similarity_score(a: str, b: str) -> float:
    return float(fuzz.token_set_ratio(a, b))


def find_best_event_match(
    requested_away: str,
    requested_home: str,
    events: list[dict[str, Any]],
    alias_lookup: dict[str, str],
    min_score: float = 110.0,
) -> dict[str, Any] | None:
    req_away = canonical_team_key(requested_away, alias_lookup)
    req_home = canonical_team_key(requested_home, alias_lookup)

    best_event: dict[str, Any] | None = None
    best_score = -1.0
    best_orientation = "direct"

    for event in events:
        event_home_raw = str(event.get("home_team", "")).strip()
        event_away_raw = str(event.get("away_team", "")).strip()
        if not event_home_raw or not event_away_raw:
            continue

        event_home = canonical_team_key(event_home_raw, alias_lookup)
        event_away = canonical_team_key(event_away_raw, alias_lookup)

        direct_score = similarity_score(req_home, event_home) + similarity_score(req_away, event_away)
        reverse_score = similarity_score(req_home, event_away) + similarity_score(req_away, event_home)

        event_score = max(direct_score, reverse_score)
        orientation = "direct" if direct_score >= reverse_score else "reverse"

        if event_score > best_score:
            best_score = event_score
            best_orientation = orientation
            best_event = dict(event)
            best_event["_match_score"] = round(event_score, 2)
            best_event["_match_orientation"] = orientation

    if best_event is None or best_score < min_score:
        return None

    if best_orientation == "reverse":
        best_event["_matched_home_team"] = best_event.get("away_team", "")
        best_event["_matched_away_team"] = best_event.get("home_team", "")
    else:
        best_event["_matched_home_team"] = best_event.get("home_team", "")
        best_event["_matched_away_team"] = best_event.get("away_team", "")

    return best_event


def rank_event_matches(
    requested_away: str,
    requested_home: str,
    events: list[dict[str, Any]],
    alias_lookup: dict[str, str],
) -> list[dict[str, Any]]:
    req_away = canonical_team_key(requested_away, alias_lookup)
    req_home = canonical_team_key(requested_home, alias_lookup)

    ranked: list[dict[str, Any]] = []

    for event in events:
        event_home_raw = str(event.get("home_team", "")).strip()
        event_away_raw = str(event.get("away_team", "")).strip()
        if not event_home_raw or not event_away_raw:
            continue

        event_home = canonical_team_key(event_home_raw, alias_lookup)
        event_away = canonical_team_key(event_away_raw, alias_lookup)

        direct_score = similarity_score(req_home, event_home) + similarity_score(req_away, event_away)
        reverse_score = similarity_score(req_home, event_away) + similarity_score(req_away, event_home)

        event_score = max(direct_score, reverse_score)
        orientation = "direct" if direct_score >= reverse_score else "reverse"

        ranked.append({
            "home_team": event.get("home_team", ""),
            "away_team": event.get("away_team", ""),
            "score": round(event_score, 2),
            "orientation": orientation,
        })

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked
