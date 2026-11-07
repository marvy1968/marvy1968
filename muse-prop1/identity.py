"""Cohort identity for Muse Prop1.

The comparable game key is the snapshot cohort key: normalized sport, away
team, home team, and UTC scheduled start to the second. Provider ids are
aliases beside that key. They are not the key.
"""
from __future__ import annotations

from datetime import datetime, timezone

from cohort import CohortError, cohort_key, norm_person, team_key, utc_stamp


def parse_time(value):
    if value is None or value == "":
        return None
    try:
        text = utc_stamp(value, "timestamp")
    except CohortError:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def format_time(value) -> str | None:
    parsed = parse_time(value)
    if parsed is None:
        return None
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_player(name: str | None) -> str | None:
    if not name or not str(name).strip():
        return None
    return norm_person(name) or None


def normalize_team(name: str | None) -> str | None:
    if not name or not str(name).strip():
        return None
    return team_key(name) or None


def game_key(sport: str | None, home: str | None, away: str | None, scheduled_start) -> str | None:
    """Cohort key from home/away arguments.

    Callers pass home then away. The key itself is away then home, matching
    marv.prop_snapshot.v1. Returns None when any piece is missing.
    """
    if not sport or not home or not away or not scheduled_start:
        return None
    try:
        start = utc_stamp(scheduled_start, "scheduled_start")
    except Exception:
        return None
    return cohort_key(str(sport), str(away), str(home), start)
