"""Cohort key shared by the snapshot hash and the comparison join.

This matches marv.prop_snapshot.v1: sport, away team, home team, UTC start
to the second. It does not import the pricer, so hash code and calibration
code can both use it.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?")


class CohortError(ValueError):
    pass


def team_key(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def norm_person(name: str) -> str:
    n = str(name).lower().replace(".", "").replace("'", "").replace("-", " ")
    return " ".join(_SUFFIX.sub("", n).split())


def utc_stamp(value, what: str = "timestamp") -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CohortError(f"{what} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise CohortError(f"{what} must include a timezone")
    parsed = parsed.astimezone(timezone.utc).replace(microsecond=0)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def cohort_key(sport: str, away: str, home: str, scheduled_start: str) -> str:
    return "|".join([str(sport).strip().lower(), team_key(away), team_key(home), scheduled_start])
