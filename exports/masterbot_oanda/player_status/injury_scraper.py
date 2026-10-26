#!/usr/bin/env python3
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

ESPN_NBA_INJURIES_URL = "https://www.espn.com/nba/injuries"
CACHE_DIR = Path("storage/injury_cache")
DEFAULT_TTL_MINUTES = 60
REQUEST_TIMEOUT = 20
MIN_SLEEP_SEC = 2.0
MAX_SLEEP_SEC = 4.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    )
}

TEAM_NAME_NORMALIZER = {
    "la clippers": "Los Angeles Clippers",
    "la lakers": "Los Angeles Lakers",
    "okc thunder": "Oklahoma City Thunder",
    "76ers": "Philadelphia 76ers",
    "sixers": "Philadelphia 76ers",
    "trail blazers": "Portland Trail Blazers",
    "blazers": "Portland Trail Blazers",
}

# Starter v1 impact map; extend as needed.
# Think of this as a rough additive player-impact prior, not final truth.
PLAYER_IMPACT_WEIGHTS = {
    "lebron james": 0.18,
    "anthony davis": 0.18,
    "luka doncic": 0.20,
    "nikola jokic": 0.22,
    "jamal murray": 0.12,
    "jayson tatum": 0.18,
    "jaylen brown": 0.14,
    "joel embiid": 0.22,
    "tyrese maxey": 0.15,
    "giannis antetokounmpo": 0.22,
    "damian lillard": 0.15,
    "stephen curry": 0.20,
    "jimmy butler": 0.16,
    "bam adebayo": 0.14,
    "donovan mitchell": 0.17,
    "darius garland": 0.13,
    "shai gilgeous-alexander": 0.22,
    "chet holmgren": 0.13,
    "anthony edwards": 0.18,
    "karl-anthony towns": 0.15,
    "devin booker": 0.17,
    "kevin durant": 0.18,
    "devin vassell": 0.10,
    "victor wembanyama": 0.22,
    "trae young": 0.18,
    "cade cunningham": 0.16,
    "tyrese haliburton": 0.18,
    "pascal siakam": 0.14,
}

STATUS_MULTIPLIERS = {
    "out": 1.00,
    "doubtful": 0.75,
    "questionable": 0.50,
    "day-to-day": 0.40,
    "probable": 0.20,
    "active": 0.00,
}

@dataclass
class PlayerStatus:
    team: str
    player: str
    position: str
    status: str
    est_return_date: str
    comment: str
    impact_base: float
    impact_score: float

@dataclass
class TeamInjurySnapshot:
    team: str
    fetched_at_utc: str
    source_url: str
    players: list[dict[str, Any]]
    team_impact_score: float

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _utc_iso(dt: datetime | None = None) -> str:
    return (dt or _now_utc()).isoformat()

def _sleep_polite() -> None:
    time.sleep(random.uniform(MIN_SLEEP_SEC, MAX_SLEEP_SEC))

def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())

def _norm_team(team: str) -> str:
    s = _norm_text(team)
    low = s.lower()
    return TEAM_NAME_NORMALIZER.get(low, s)

def _norm_player(player: str) -> str:
    s = _norm_text(player).lower()
    s = s.replace(".", "")
    return s

def _cache_path(team: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", _norm_team(team).lower())
    return CACHE_DIR / f"{safe}.json"

def _is_cache_fresh(path: Path, ttl_minutes: int) -> bool:
    if not path.exists():
        return False
    age = _now_utc() - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return age <= timedelta(minutes=ttl_minutes)

def _load_cache(path: Path) -> TeamInjurySnapshot | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return TeamInjurySnapshot(**payload)
    except Exception:
        return None

def _save_cache(path: Path, data: TeamInjurySnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(data), indent=2), encoding="utf-8")

def _status_key(status: str) -> str:
    s = _norm_text(status).lower()
    if s in STATUS_MULTIPLIERS:
        return s
    if "out" in s:
        return "out"
    if "doubt" in s:
        return "doubtful"
    if "question" in s:
        return "questionable"
    if "day-to-day" in s or "day to day" in s:
        return "day-to-day"
    if "probable" in s:
        return "probable"
    return "questionable"

def _impact_base_for_player(player: str) -> float:
    return PLAYER_IMPACT_WEIGHTS.get(_norm_player(player), 0.04)

def _impact_score(player: str, status: str) -> tuple[float, float]:
    base = _impact_base_for_player(player)
    mult = STATUS_MULTIPLIERS.get(_status_key(status), 0.50)
    return base, round(base * mult, 4)

class InjuryScraper:
    def __init__(self, ttl_minutes: int = DEFAULT_TTL_MINUTES) -> None:
        self.ttl_minutes = ttl_minutes
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def fetch_or_cache_team(self, team: str, force_refresh: bool = False) -> TeamInjurySnapshot:
        team = _norm_team(team)
        path = _cache_path(team)

        if not force_refresh and _is_cache_fresh(path, self.ttl_minutes):
            cached = _load_cache(path)
            if cached is not None:
                return cached

        all_teams = self.fetch_all_teams(force_refresh=True)
        if team not in all_teams:
            snapshot = TeamInjurySnapshot(
                team=team,
                fetched_at_utc=_utc_iso(),
                source_url=ESPN_NBA_INJURIES_URL,
                players=[],
                team_impact_score=0.0,
            )
            _save_cache(path, snapshot)
            return snapshot

        snapshot = all_teams[team]
        _save_cache(path, snapshot)
        return snapshot

    def fetch_all_teams(self, force_refresh: bool = False) -> dict[str, TeamInjurySnapshot]:
        html = self._get(ESPN_NBA_INJURIES_URL)
        snapshots = self._parse_espn_injuries(html)
        for team, snap in snapshots.items():
            _save_cache(_cache_path(team), snap)
        return snapshots

    def _get(self, url: str) -> str:
        resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        _sleep_polite()
        return resp.text

    def _parse_espn_injuries(self, html: str) -> dict[str, TeamInjurySnapshot]:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)

        # ESPN renders injuries under repeated team headers followed by tabular rows.
        # This parser is intentionally resilient and text-driven.
        lines = [_norm_text(x) for x in text.splitlines() if _norm_text(x)]
        snapshots: dict[str, TeamInjurySnapshot] = {}

        nba_teams = [
            "Atlanta Hawks","Boston Celtics","Brooklyn Nets","Charlotte Hornets","Chicago Bulls",
            "Cleveland Cavaliers","Dallas Mavericks","Denver Nuggets","Detroit Pistons",
            "Golden State Warriors","Houston Rockets","Indiana Pacers","Los Angeles Clippers",
            "Los Angeles Lakers","Memphis Grizzlies","Miami Heat","Milwaukee Bucks",
            "Minnesota Timberwolves","New Orleans Pelicans","New York Knicks",
            "Oklahoma City Thunder","Orlando Magic","Philadelphia 76ers","Phoenix Suns",
            "Portland Trail Blazers","Sacramento Kings","San Antonio Spurs","Toronto Raptors",
            "Utah Jazz","Washington Wizards"
        ]
        team_set = set(nba_teams)

        current_team: str | None = None
        i = 0

        while i < len(lines):
            line = lines[i]

            if line in team_set:
                current_team = line
                if current_team not in snapshots:
                    snapshots[current_team] = TeamInjurySnapshot(
                        team=current_team,
                        fetched_at_utc=_utc_iso(),
                        source_url=ESPN_NBA_INJURIES_URL,
                        players=[],
                        team_impact_score=0.0,
                    )
                i += 1
                continue

            if current_team is not None:
                # Expected row pattern commonly resembles:
                # PlayerName POS Est.Return.Date STATUS COMMENT
                # We parse conservatively from the current line and nearby lines.
                maybe_player = line

                if maybe_player in team_set:
                    current_team = maybe_player
                    i += 1
                    continue

                # Skip generic headings
                if maybe_player.lower() in {"nba injuries", "name pos est. return date status comment", "name pos status comment"}:
                    i += 1
                    continue

                # Look ahead for position + status-like tokens
                if i + 2 < len(lines):
                    pos = lines[i + 1]
                    status = lines[i + 2]

                    if len(pos) <= 4 and any(k in status.lower() for k in ["out", "question", "doubt", "day-to-day", "probable"]):
                        est_return = ""
                        comment = ""
                        j = i + 3

                        if j < len(lines) and not any(x in lines[j] for x in team_set):
                            # optional estimated return line
                            est_return = lines[j]
                            j += 1

                        comment_parts = []
                        while j < len(lines) and lines[j] not in team_set:
                            # stop if next likely player row starts
                            nxt = lines[j]
                            if len(nxt.split()) >= 2 and len(comment_parts) > 0 and nxt.endswith(("G", "F", "C", "G-F", "F-C")):
                                break
                            comment_parts.append(nxt)
                            # keep comments short-ish per row
                            if len(" ".join(comment_parts)) > 220:
                                break
                            j += 1

                        comment = " ".join(comment_parts).strip()
                        base, score = _impact_score(maybe_player, status)

                        snapshots[current_team].players.append(
                            asdict(
                                PlayerStatus(
                                    team=current_team,
                                    player=maybe_player,
                                    position=pos,
                                    status=status,
                                    est_return_date=est_return,
                                    comment=comment,
                                    impact_base=base,
                                    impact_score=score,
                                )
                            )
                        )
                        i = j
                        continue

            i += 1

        # finalize team impact
        for team, snapshot in snapshots.items():
            total = sum(float(p.get("impact_score", 0.0) or 0.0) for p in snapshot.players)
            snapshot.team_impact_score = round(total, 4)

        return snapshots

def fetch_team_injuries(team: str, force_refresh: bool = False, ttl_minutes: int = DEFAULT_TTL_MINUTES) -> TeamInjurySnapshot:
    scraper = InjuryScraper(ttl_minutes=ttl_minutes)
    return scraper.fetch_or_cache_team(team, force_refresh=force_refresh)

if __name__ == "__main__":
    scraper = InjuryScraper(ttl_minutes=60)

    # Change this to any NBA team you want to test
    team = "Los Angeles Lakers"

    data = scraper.fetch_or_cache_team(team, force_refresh=True)
    print(json.dumps(asdict(data), indent=2))
