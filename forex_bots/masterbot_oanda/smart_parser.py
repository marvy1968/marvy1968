from __future__ import annotations

import re
from rapidfuzz import fuzz

from event_matcher import canonical_team_key


def normalize_and_extract_mode(raw_text: str) -> tuple[str, str]:
    text = raw_text.lower().strip()

    text = re.sub(r"\s*@\s*", " vs ", text)
    text = re.sub(r"\s+versus\s+", " vs ", text)
    text = re.sub(r"\s+vs\.?\s+", " vs ", text)
    text = re.sub(r"\s+v\s+", " vs ", text)

    mode_match = re.search(r"\b(1h|2h|full)\b", text)
    mode = mode_match.group(1) if mode_match else "full"

    text = re.sub(r"\b(1h|2h|full)\b", " ", text)
    text = re.sub(r"\b(live|game|now|today)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text, mode


def best_known_team_match(text: str, alias_lookup: dict[str, str]) -> tuple[str, float]:
    norm = text.strip().lower()
    best_key = ""
    best_score = -1.0

    for known_key in alias_lookup.keys():
        score = float(fuzz.token_set_ratio(norm, known_key))
        if score > best_score:
            best_score = score
            best_key = known_key

    return best_key, best_score


def score_candidate_splits(team_text: str, alias_lookup: dict[str, str]):
    words = team_text.split()
    candidates = []

    for i in range(1, len(words)):
        left_side = " ".join(words[:i])
        right_side = " ".join(words[i:])

        left_best_key, left_score = best_known_team_match(left_side, alias_lookup)
        right_best_key, right_score = best_known_team_match(right_side, alias_lookup)

        candidates.append({
            "left_raw": left_side,
            "right_raw": right_side,
            "left_best_key": left_best_key,
            "right_best_key": right_best_key,
            "left_score": left_score,
            "right_score": right_score,
            "combined_score": left_score + right_score,
        })

    candidates.sort(key=lambda x: x["combined_score"], reverse=True)
    return candidates


def parse_matchup(raw_text: str, alias_lookup: dict[str, str]) -> dict[str, str]:
    team_text, mode = normalize_and_extract_mode(raw_text)

    if not team_text:
        raise ValueError("No team text found.")

    if " vs " in team_text:
        away_raw, home_raw = team_text.split(" vs ", 1)
        away_raw = away_raw.strip()
        home_raw = home_raw.strip()

        if not away_raw or not home_raw:
            raise ValueError("Could not parse both teams.")

        away_team = canonical_team_key(away_raw, alias_lookup)
        home_team = canonical_team_key(home_raw, alias_lookup)

        return {
            "away_team": away_team,
            "home_team": home_team,
            "mode": mode,
        }

    ranked = score_candidate_splits(team_text, alias_lookup)
    if not ranked:
        raise ValueError("Could not split team text into two teams.")

    best = ranked[0]

    if best["left_score"] < 75 or best["right_score"] < 75:
        raise ValueError("Could not confidently identify both teams.")

    away_team = alias_lookup.get(best["left_best_key"], best["left_best_key"])
    home_team = alias_lookup.get(best["right_best_key"], best["right_best_key"])

    return {
        "away_team": away_team,
        "home_team": home_team,
        "mode": mode,
    }
