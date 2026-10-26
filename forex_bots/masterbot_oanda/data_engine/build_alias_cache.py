from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

# Make project root importable when this file is run directly.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from live_engine.payload_builder import fetch_odds_events_async
from event_matcher import normalize_team_name

CACHE_PATH = os.path.join(PROJECT_ROOT, "data_engine", "alias_lookup_cache.json")


def generate_aliases(team_name: str) -> set[str]:
    name = normalize_team_name(team_name)
    words = name.split()

    aliases = set()
    if not name:
        return aliases

    aliases.add(name)

    if "state" in words and len(words) >= 2:
        school = " ".join(words[:-1])
        mascot = words[-1]
    elif len(words) >= 3:
        school = " ".join(words[:-2])
        mascot = " ".join(words[-2:])
    elif len(words) == 2:
        school = words[0]
        mascot = words[1]
    else:
        school = name
        mascot = ""

    if school:
        aliases.add(school)
    if mascot:
        aliases.add(mascot)

    return {a.strip() for a in aliases if a.strip()}


async def build_cache() -> None:
    print("Fetching live events from Odds API...")
    events = await fetch_odds_events_async("mens")

    unique_teams = set()
    for event in events:
        home = event.get("home_team")
        away = event.get("away_team")
        if home:
            unique_teams.add(home)
        if away:
            unique_teams.add(away)

    print(f"Found {len(unique_teams)} teams")

    alias_lookup: dict[str, str] = {}

    for team in unique_teams:
        canonical = normalize_team_name(team)
        for alias in generate_aliases(team):
            alias_lookup[alias] = canonical

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "odds_api",
        "total_aliases": len(alias_lookup),
        "aliases": dict(sorted(alias_lookup.items())),
    }

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"Alias cache saved -> {CACHE_PATH}")
    print(f"Aliases generated: {len(alias_lookup)}")


if __name__ == "__main__":
    asyncio.run(build_cache())
