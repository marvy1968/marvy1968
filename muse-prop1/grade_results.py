"""Turn a saved final ESPN box into typed settlements. No odds calls.

pending and void stay explicit. A missing player in a final box is void.
A game that is not final is pending. This module does not fit a calibration.
"""
from __future__ import annotations

import json
from pathlib import Path

from cohort import cohort_key, norm_person, utc_stamp
from live_feed import parse_summary
from prop_snapshot import STAT_KEY, _raw_groups, hash_payload, input_hash

FINAL_SCHEMA = "muse-prop1.final-box.v1"


def _final_stat(sport: str, player: dict, market: str) -> float | None:
    if market == "player_anytime_td":
        if "rush_tds" not in player and "rec_tds" not in player:
            return None
        return float(player.get("rush_tds") or 0) + float(player.get("rec_tds") or 0)
    key = STAT_KEY.get(market)
    if not key or key not in player:
        return None
    return float(player.get(key) or 0)


def settlements_for_observation(obs: dict, final: dict | None) -> list[dict]:
    """One settlement row per quoted player/market. Does not invent a final."""
    payload = hash_payload(obs, "observation")
    key = cohort_key(payload["sport"], payload["away"], payload["home"], payload["scheduled_start"])
    state = (final or {}).get("state")
    players = {}
    if final and isinstance(final.get("boxscore"), dict):
        parsed = parse_summary({"boxscore": final["boxscore"], "header": {"competitions": [{}]}},
                               "nba" if payload["sport"] == "nba" else ("ncaaf" if payload["sport"] == "cfb" else payload["sport"]))
        players = {norm_person(name): row for name, row in (parsed.get("players") or {}).items()}
    rows = []
    seen = set()
    for group in _raw_groups(payload["quoted_odds"]):
        ident = (norm_person(group["player"]), group["market"])
        if ident in seen:
            continue
        seen.add(ident)
        status = "pending"
        final_value = None
        if state == "post":
            who = players.get(ident[0])
            if who is None:
                status = "void"
            else:
                stat = _final_stat(payload["sport"], who, group["market"])
                if stat is None:
                    status = "void"
                else:
                    status = "final"
                    final_value = stat
        rows.append({
            "cohort_key": key,
            "sport": payload["sport"],
            "home": payload["home"],
            "away": payload["away"],
            "scheduled_start": payload["scheduled_start"],
            "player": group["player"],
            "market": group["market"],
            "status": status,
            "final": final_value,
            "input_hash": input_hash(payload),
        })
    return rows


def load_final(path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    if data.get("schema") != FINAL_SCHEMA:
        raise ValueError(f"final file schema must be {FINAL_SCHEMA}")
    if data.get("state") not in ("pre", "in", "post"):
        raise ValueError("final state must be pre, in, or post")
    if not isinstance(data.get("boxscore"), dict):
        raise ValueError("final boxscore must be the raw ESPN object")
    return data


def write_settlement(path: Path, results: list[dict], synthetic: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "schema": "prop1.settlement.v1",
        "synthetic": synthetic,
        "results": results,
    }
    text = json.dumps(body, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def stamp(value: str) -> str:
    return utc_stamp(value, "timestamp")
