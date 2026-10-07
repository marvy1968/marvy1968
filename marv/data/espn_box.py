"""Scrape team box scores for finished games from ESPN game pages (summary JSON).

Used to fill games the daily sportsdataverse files haven't picked up yet, so the stats are
current on game day. Output matches the sportsdataverse team-box columns.
"""

import json
import logging
import re
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/{league}/summary"


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def parse_summary(data: dict, game_id: str) -> list[dict]:
    comp = (data.get("header", {}).get("competitions") or [{}])[0]
    scores = {str(c.get("team", {}).get("id")): c.get("score") for c in comp.get("competitors", [])}
    sides = {str(c.get("team", {}).get("id")): c.get("homeAway") for c in comp.get("competitors", [])}
    season = (data.get("header", {}).get("season") or {}).get("year")
    stype = (data.get("header", {}).get("season") or {}).get("type", 2)
    date = (comp.get("date") or "")[:10]
    teams = data.get("boxscore", {}).get("teams", [])
    rows = []
    for t in teams:
        team = t.get("team", {})
        tid = str(team.get("id"))
        row = {"game_id": game_id, "season": season, "season_type": stype, "game_date": date,
               "team_id": int(tid) if tid.isdigit() else tid, "team_display_name": team.get("displayName"),
               "team_home_away": t.get("homeAway") or sides.get(tid), "team_score": pd.to_numeric(scores.get(tid), errors="coerce")}
        for st in t.get("statistics", []):
            name, value = st.get("name", ""), str(st.get("displayValue", ""))
            if "-" in name and "-" in value:
                for n, v in zip(name.split("-"), value.split("-")):
                    row[_snake(n)] = pd.to_numeric(v, errors="coerce")
            else:
                row[_snake(name)] = pd.to_numeric(value, errors="coerce")
        rows.append(row)
    if len(rows) == 2:
        for a, b in ((0, 1), (1, 0)):
            rows[a]["opponent_team_id"] = rows[b]["team_id"]
            rows[a]["opponent_team_display_name"] = rows[b]["team_display_name"]
            rows[a]["opponent_team_score"] = rows[b]["team_score"]
    return rows if len(rows) == 2 else []


def fetch_boxes(league: str, game_ids: list[str], cache: Path) -> pd.DataFrame:
    rows = []
    for gid in game_ids:
        path = cache / "espn_box" / f"{league}_{gid}.json"
        try:
            if path.exists():
                data = json.loads(path.read_text())
            else:
                resp = requests.get(SUMMARY.format(league=league), params={"event": gid}, timeout=30,
                                    headers={"User-Agent": "Mozilla/5.0 (marv-predict-bot)"})
                resp.raise_for_status()
                data = resp.json()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data))
            rows += parse_summary(data, str(gid))
        except Exception as exc:
            log.warning("ESPN box score %s %s unavailable: %s", league, gid, exc)
    return pd.DataFrame(rows)
