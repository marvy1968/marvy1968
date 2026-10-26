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
from bs4 import BeautifulSoup, Comment

NBA_BASE_URL = "https://www.basketball-reference.com/"
NCAAB_BASE_URL = "https://www.sports-reference.com/cbb/"
NCAAW_BASE_URL = "https://www.sports-reference.com/cbb/"
CACHE_DIR = Path("storage/team_cache")
DEFAULT_TTL_HOURS = 6
REQUEST_TIMEOUT = 20
MIN_SLEEP_SEC = 1.5
MAX_SLEEP_SEC = 3.5
MAX_RECENT_GAMES = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    )
}

TEAM_NAME_MAP: dict[str, str] = {
    # NBA
    "atlanta hawks": "ATL",
    "boston celtics": "BOS",
    "brooklyn nets": "BRK",
    "charlotte hornets": "CHO",
    "chicago bulls": "CHI",
    "cleveland cavaliers": "CLE",
    "dallas mavericks": "DAL",
    "denver nuggets": "DEN",
    "detroit pistons": "DET",
    "golden state warriors": "GSW",
    "houston rockets": "HOU",
    "indiana pacers": "IND",
    "los angeles clippers": "LAC",
    "los angeles lakers": "LAL",
    "memphis grizzlies": "MEM",
    "miami heat": "MIA",
    "milwaukee bucks": "MIL",
    "minnesota timberwolves": "MIN",
    "new orleans pelicans": "NOP",
    "new york knicks": "NYK",
    "oklahoma city thunder": "OKC",
    "orlando magic": "ORL",
    "philadelphia 76ers": "PHI",
    "phoenix suns": "PHO",
    "portland trail blazers": "POR",
    "sacramento kings": "SAC",
    "san antonio spurs": "SAS",
    "toronto raptors": "TOR",
    "utah jazz": "UTA",
    "washington wizards": "WAS",
    "la clippers": "LAC",
    "la lakers": "LAL",
    "okc thunder": "OKC",
    "sixers": "PHI",
    "76ers": "PHI",
    "trail blazers": "POR",
    "blazers": "POR",

    # NCAAM / NCAAW shared school slugs
    "purdue boilermakers": "purdue",
    "texas longhorns": "texas",
    "nebraska cornhuskers": "nebraska",
    "iowa hawkeyes": "iowa",
    "arizona wildcats": "arizona",
    "arkansas razorbacks": "arkansas",
    "houston cougars": "houston",
    "illinois fighting illini": "illinois",
    "duke blue devils": "duke",
    "st johns red storm": "st-johns-ny",
    "st. john's red storm": "st-johns-ny",
    "michigan wolverines": "michigan",
    "alabama crimson tide": "alabama",
    "uconn huskies": "connecticut",
    "connecticut huskies": "connecticut",
    "michigan state spartans": "michigan-state",
    "michigan st spartans": "michigan-state",
    "iowa state cyclones": "iowa-state",
    "tennessee volunteers": "tennessee",
    "dayton flyers": "dayton",
    "auburn tigers": "auburn",
    "kentucky wildcats": "kentucky",
    "louisville cardinals": "louisville",
    "oklahoma sooners": "oklahoma",
    "south carolina gamecocks": "south-carolina",
    "virginia cavaliers": "virginia",
    "tcu horned frogs": "tcu",
    "baylor bears": "baylor",
    "minnesota golden gophers": "minnesota",
    "stanford cardinal": "stanford",
    "west virginia mountaineers": "west-virginia",
    "rutgers scarlet knights": "rutgers",
    "creighton bluejays": "creighton",
    "notre dame fighting irish": "notre-dame",
    "north carolina tar heels": "north-carolina",
    "ucla bruins": "ucla",
    "lsu tigers": "louisiana-state",
    "colorado buffaloes": "colorado",
    "new mexico lobos": "new-mexico",
    "tulsa golden hurricane": "tulsa",
    "florida state seminoles": "florida-state",
    "clemson tigers": "clemson",
    "kansas jayhawks": "kansas",
    "brigham young cougars": "brigham-young",
    "byu cougars": "brigham-young",
    "vanderbilt commodores": "vanderbilt",
}

@dataclass
class TeamGameLogRow:
    game_no: int
    date: str
    opponent: str
    home_away: str
    result: str
    team_points: int
    opp_points: int
    wins: int
    losses: int
    streak: str
    overtime: bool
    raw: dict[str, Any]

@dataclass
class TeamRecentData:
    team_name: str
    team_key: str
    league: str
    season: int
    fetched_at_utc: str
    source_url: str
    recent_games: list[dict[str, Any]]
    metadata: dict[str, Any]

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _utc_iso(dt: datetime | None = None) -> str:
    return (dt or _now_utc()).isoformat()

def _normalize_team_name(name: str) -> str:
    s = " ".join(str(name or "").strip().lower().split())
    s = s.replace(".", "")
    s = s.replace("&", "and")
    s = s.replace("-", " ")
    return " ".join(s.split())

def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        text = str(value).strip()
        if text in {"nan", "None"}:
            return default
        return int(float(text))
    except Exception:
        return default

def _sleep_polite() -> None:
    time.sleep(random.uniform(MIN_SLEEP_SEC, MAX_SLEEP_SEC))

def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _cache_path(team_key: str, league: str, season: int) -> Path:
    safe_team = re.sub(r"[^a-zA-Z0-9_]+", "_", str(team_key))
    safe_league = re.sub(r"[^a-zA-Z0-9_]+", "_", str(league).lower())
    return CACHE_DIR / f"{safe_league}_{safe_team}_{season}.json"

def _is_cache_fresh(path: Path, ttl_hours: int) -> bool:
    if not path.exists():
        return False
    age = _now_utc() - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return age <= timedelta(hours=ttl_hours)

def _load_cache(path: Path) -> TeamRecentData | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return TeamRecentData(**payload)
    except Exception:
        return None

def _save_cache(path: Path, data: TeamRecentData) -> None:
    path.write_text(json.dumps(asdict(data), indent=2), encoding="utf-8")

def _resolve_team_key(team_name: str) -> str:
    norm = _normalize_team_name(team_name)
    if norm in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[norm]
    raise KeyError(f"No mapping found for team '{team_name}' (normalized='{norm}')")

def _default_season_for_date(dt: datetime | None = None) -> int:
    dt = dt or _now_utc()
    return dt.year + 1 if dt.month >= 10 else dt.year

def _extract_hidden_tables_from_comments(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    comment_bits: list[str] = []
    for item in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment_bits.append(str(item))
    if comment_bits:
        return html + "\n" + "\n".join(comment_bits)
    return html

class TeamStatsScraper:
    def __init__(
        self,
        cache_ttl_hours: int = DEFAULT_TTL_HOURS,
        session: requests.Session | None = None,
    ) -> None:
        self.cache_ttl_hours = cache_ttl_hours
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        _ensure_cache_dir()

    def fetch_or_cache(
        self,
        team_name: str,
        league: str,
        season: int | None = None,
        force_refresh: bool = False,
        recent_n: int = 10,
    ) -> TeamRecentData:
        season = season or _default_season_for_date()
        team_key = _resolve_team_key(team_name)
        cache_file = _cache_path(team_key, league, season)

        if not force_refresh and _is_cache_fresh(cache_file, self.cache_ttl_hours):
            cached = _load_cache(cache_file)
            if cached is not None:
                return cached

        fresh = self.fetch_team_recent_games(
            team_name=team_name,
            league=league,
            season=season,
            recent_n=recent_n,
        )
        _save_cache(cache_file, fresh)
        return fresh

    def fetch_team_recent_games(
        self,
        team_name: str,
        league: str,
        season: int,
        recent_n: int = 10,
    ) -> TeamRecentData:
        team_key = _resolve_team_key(team_name)
        source_url = self._build_team_gamelog_url(team_key, league, season)
        html = self._get(source_url)
        html = _extract_hidden_tables_from_comments(html)

        league_norm = str(league or "").strip().lower()
        if league_norm == "nba":
            recent_games = self._parse_nba_recent_games_from_html(html, recent_n=min(recent_n, MAX_RECENT_GAMES))
        elif league_norm in {"ncaab", "ncaaw"}:
            recent_games = self._parse_college_recent_games_from_html(html, recent_n=min(recent_n, MAX_RECENT_GAMES))
        else:
            raise NotImplementedError(f"League '{league}' not implemented.")

        return TeamRecentData(
            team_name=team_name,
            team_key=team_key,
            league=league,
            season=season,
            fetched_at_utc=_utc_iso(),
            source_url=source_url,
            recent_games=[asdict(x) for x in recent_games],
            metadata={
                "recent_n_requested": recent_n,
                "recent_n_returned": len(recent_games),
            },
        )

    def _get(self, url: str) -> str:
        resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        _sleep_polite()
        return resp.text

    def _build_team_gamelog_url(self, team_key: str, league: str, season: int) -> str:
        league_norm = str(league or "").strip().lower()

        if league_norm == "nba":
            return f"{NBA_BASE_URL}teams/{team_key}/{season}/gamelog/"
        if league_norm == "ncaab":
            return f"{NCAAB_BASE_URL}schools/{team_key}/men/{season}-schedule.html"
        if league_norm == "ncaaw":
            return f"{NCAAW_BASE_URL}schools/{team_key}/women/{season}-schedule.html"

        raise NotImplementedError(f"League '{league}' not implemented.")

    def _parse_nba_recent_games_from_html(self, html: str, recent_n: int) -> list[TeamGameLogRow]:
        soup = BeautifulSoup(html, "html.parser")

        table = soup.find("table", {"id": "team_game_log_reg"})
        if table is None:
            table = soup.find("table", {"id": "tgl_basic"})

        if table is None:
            raise ValueError("Could not find NBA game log table (team_game_log_reg or tgl_basic).")

        body = table.find("tbody")
        if body is None:
            raise ValueError("NBA game log table missing tbody.")

        rows: list[TeamGameLogRow] = []
        wins = 0
        losses = 0
        streak_state = ""

        for idx, tr in enumerate(body.find_all("tr"), start=1):
            if "thead" in (tr.get("class") or []):
                continue

            raw = self._all_row_cells(tr)

            date = raw.get("date", "") or raw.get("date_game", "")
            opponent = (
                raw.get("opp_name_abbr", "")
                or raw.get("opp_name", "")
                or raw.get("opp_id", "")
                or raw.get("opp_team_id", "")
            )
            result = raw.get("team_game_result", "") or raw.get("game_result", "") or raw.get("wl", "")
            game_location = raw.get("game_location", "")
            team_points = _safe_int(raw.get("team_game_score", "") or raw.get("pts", ""), 0)
            opp_points = _safe_int(raw.get("opp_team_game_score", "") or raw.get("opp_pts", ""), 0)
            overtime_text = raw.get("overtimes", "")

            if not date or not opponent:
                continue
            if team_points == 0 and opp_points == 0:
                continue

            home_away = "away" if game_location == "@" else "home"

            if result.upper().startswith("W"):
                wins += 1
                streak_state = f"W1" if not streak_state.startswith("W") else f"W{_safe_int(streak_state[1:], 0) + 1}"
            elif result.upper().startswith("L"):
                losses += 1
                streak_state = f"L1" if not streak_state.startswith("L") else f"L{_safe_int(streak_state[1:], 0) + 1}"

            rows.append(
                TeamGameLogRow(
                    game_no=idx,
                    date=date,
                    opponent=opponent,
                    home_away=home_away,
                    result=result,
                    team_points=team_points,
                    opp_points=opp_points,
                    wins=wins,
                    losses=losses,
                    streak=streak_state,
                    overtime=bool(overtime_text and str(overtime_text).strip()),
                    raw=raw,
                )
            )

        if not rows:
            raise ValueError("NBA parser found no completed game rows.")

        return rows[-recent_n:]

    def _parse_college_recent_games_from_html(self, html: str, recent_n: int) -> list[TeamGameLogRow]:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", {"id": "schedule"})
        if table is None:
            raise ValueError("Could not find college schedule table 'schedule'.")

        body = table.find("tbody")
        if body is None:
            raise ValueError("College schedule table missing tbody.")

        rows: list[TeamGameLogRow] = []
        wins = 0
        losses = 0
        streak_state = ""

        for idx, tr in enumerate(body.find_all("tr"), start=1):
            if "thead" in (tr.get("class") or []):
                continue

            date = self._cell_text(tr, "date_game")
            opponent = self._cell_text(tr, "opp_name")
            location = self._cell_text(tr, "game_location")
            home_away = "away" if location == "@" else "home"
            result_short = self._cell_text(tr, "game_result")
            pts = _safe_int(self._cell_text(tr, "pts"), 0)
            opp_pts = _safe_int(self._cell_text(tr, "opp_pts"), 0)
            raw = self._all_row_cells(tr)

            if not date or not opponent:
                continue
            if pts == 0 and opp_pts == 0:
                continue

            if result_short.upper().startswith("W"):
                wins += 1
                streak_state = f"W1" if not streak_state.startswith("W") else f"W{_safe_int(streak_state[1:], 0) + 1}"
            elif result_short.upper().startswith("L"):
                losses += 1
                streak_state = f"L1" if not streak_state.startswith("L") else f"L{_safe_int(streak_state[1:], 0) + 1}"

            rows.append(
                TeamGameLogRow(
                    game_no=idx,
                    date=date,
                    opponent=opponent,
                    home_away=home_away,
                    result=result_short,
                    team_points=pts,
                    opp_points=opp_pts,
                    wins=wins,
                    losses=losses,
                    streak=streak_state,
                    overtime="OT" in result_short.upper(),
                    raw=raw,
                )
            )

        if not rows:
            raise ValueError("College parser found no completed game rows.")

        return rows[-recent_n:]

    @staticmethod
    def _cell_text(tr: Any, data_stat: str) -> str:
        cell = tr.find(["td", "th"], {"data-stat": data_stat})
        if cell is None:
            return ""
        return " ".join(cell.get_text(" ", strip=True).split())

    @staticmethod
    def _all_row_cells(tr: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for cell in tr.find_all(["td", "th"]):
            key = cell.get("data-stat")
            if not key:
                continue
            out[key] = " ".join(cell.get_text(" ", strip=True).split())
        return out

def fetch_team_recent_data(
    team_name: str,
    league: str,
    season: int | None = None,
    force_refresh: bool = False,
    recent_n: int = 10,
    cache_ttl_hours: int = DEFAULT_TTL_HOURS,
) -> TeamRecentData:
    scraper = TeamStatsScraper(cache_ttl_hours=cache_ttl_hours)
    return scraper.fetch_or_cache(
        team_name=team_name,
        league=league,
        season=season,
        force_refresh=force_refresh,
        recent_n=recent_n,
    )

if __name__ == "__main__":
    season = _default_season_for_date()

    tests = [
        ("Los Angeles Lakers", "nba"),
        ("Duke Blue Devils", "ncaaw"),
        ("Houston Cougars", "ncaab"),
    ]

    for team_name, league in tests:
        try:
            data = fetch_team_recent_data(
                team_name=team_name,
                league=league,
                season=season,
                force_refresh=True,
                recent_n=5,
            )
            print(f"\n=== {team_name} ({league}) ===")
            print("team_key:", data.team_key)
            print("source_url:", data.source_url)
            print("games:", len(data.recent_games))
            for g in data.recent_games[:3]:
                print(g["date"], "|", g["opponent"], "|", g["team_points"], "-", g["opp_points"])
        except Exception as e:
            print(f"\nFAILED: {team_name} ({league}) -> {type(e).__name__}: {e}")
