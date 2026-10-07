"""FIBA LiveStats (Genius Sports) box scores, used for EuroLeague Women.

FIBA publishes each game's live data as JSON at
    https://fibalivestats.dcd.shared.geniussports.com/data/<match id>/data.json
Team totals live under data["tm"]["1"|"2"] with "tot_s..." keys. Match ids are found on FIBA's
competition pages (links to fibalivestats). `python -m marv probe-ewl` checks all of this on the VM
and saves samples to state/probe/ if the format turns out to differ.
"""

import json
import logging
import re
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)
DATA = "https://fibalivestats.dcd.shared.geniussports.com/data/{mid}/data.json"
UA = {"User-Agent": "Mozilla/5.0 (marv-predict-bot)"}
ID_PATTERNS = [re.compile(r"fibalivestats[^\"'<>\s]*?/(\d{6,8})"), re.compile(r"\"(?:gameId|matchId|liveStatsId)\"\s*:\s*\"?(\d{6,8})")]

# FIBA tot_s* key -> ESPN-style column
TOTALS = {
    "tot_sPoints": "team_score", "tot_sFieldGoalsMade": "field_goals_made",
    "tot_sFieldGoalsAttempted": "field_goals_attempted", "tot_sThreePointersMade": "three_point_field_goals_made",
    "tot_sThreePointersAttempted": "three_point_field_goals_attempted", "tot_sFreeThrowsMade": "free_throws_made",
    "tot_sFreeThrowsAttempted": "free_throws_attempted", "tot_sReboundsOffensive": "offensive_rebounds",
    "tot_sReboundsDefensive": "defensive_rebounds", "tot_sReboundsTotal": "total_rebounds",
    "tot_sAssists": "assists", "tot_sTurnovers": "total_turnovers", "tot_sSteals": "steals", "tot_sBlocks": "blocks",
    "tot_sBlocksReceived": "blocks_against", "tot_sFoulsPersonal": "fouls", "tot_sFoulsOn": "fouls_drawn",
    "tot_sPointsInThePaint": "points_in_paint", "tot_sPointsFastBreak": "fast_break_points",
    "tot_sPointsFromTurnovers": "turnover_points", "tot_sBiggestLead": "largest_lead",
}


def match_ids(html: str) -> list[int]:
    found = []
    for pat in ID_PATTERNS:
        found += [int(x) for x in pat.findall(html)]
    return sorted(set(found))


def fetch_match(mid: int, cache: Path | None = None, final: bool = True) -> dict | None:
    path = cache / "fiba" / f"{mid}.json" if cache else None
    if path and path.exists():
        return json.loads(path.read_text())
    try:
        resp = requests.get(DATA.format(mid=mid), headers=UA, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("FIBA LiveStats %s unavailable: %s", mid, exc)
        return None
    if path and final and is_final(data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    return data


def is_final(data: dict) -> bool:
    """Game over: regulation (or overtime) period finished with the clock at zero and no tie."""
    status = str(data.get("matchStatus", data.get("status", ""))).upper()
    if status in ("COMPLETE", "COMPLETED", "FINISHED", "FINAL", "ENDED"):
        return True
    teams = data.get("tm") or {}
    scores = [pd.to_numeric((teams.get(k) or {}).get("score"), errors="coerce") for k in ("1", "2")]
    period = int(pd.to_numeric(data.get("period"), errors="coerce") or 0)
    clock_zero = str(data.get("clock", "")).strip() in ("00:00", "0:00", "00:00:00")
    return period >= 4 and clock_zero and scores[0] != scores[1]


def match_date(data: dict):
    for key in ("matchTime", "startTime", "date", "matchDate", "gameDate"):
        if data.get(key):
            d = pd.to_datetime(data[key], errors="coerce", utc=True)
            if pd.notna(d):
                return d.tz_convert(None).normalize()
    return pd.NaT


def parse_totals(data: dict, mid: int) -> list[dict]:
    """Two team rows in ESPN layout; team 1 is the home side in FIBA LiveStats."""
    teams = data.get("tm") or {}
    if "1" not in teams or "2" not in teams:
        return []
    rows = []
    for key, side in (("1", "home"), ("2", "away")):
        t = teams[key]
        row = {"game_id": f"FIBA{mid}", "team_display_name": t.get("name") or t.get("shortName"),
               "team_home_away": side}
        for src, dst in TOTALS.items():
            if src in t:
                row[dst] = pd.to_numeric(t[src], errors="coerce")
        if "team_score" not in row:
            row["team_score"] = pd.to_numeric(t.get("score"), errors="coerce")
        rows.append(row)
    for a, b in ((0, 1), (1, 0)):
        rows[a]["opponent_team_display_name"] = rows[b]["team_display_name"]
        rows[a]["team_id"] = rows[a]["team_display_name"]
        rows[a]["opponent_team_id"] = rows[b]["team_display_name"]
        rows[a]["opponent_team_score"] = rows[b]["team_score"]
    return rows
