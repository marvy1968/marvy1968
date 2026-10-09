"""CollegeFootballData.com API client (https://api.collegefootballdata.com) and converters."""

import json
import time
from pathlib import Path
from datetime import datetime, timezone

import requests

from ..models import Game, Odds

BASE_URL = "https://api.collegefootballdata.com"


class CFBDClient:
    def __init__(self, api_key: str, session: requests.Session | None = None, cache_dir=None, ttl_hours: float = 3.0):
        if not api_key:
            raise ValueError("CFBD_API_KEY is not set (get a free key at collegefootballdata.com/key)")
        self.cache_dir, self.ttl = cache_dir, ttl_hours * 3600  # free tier has a monthly call cap: reuse recent answers
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}", "Accept": "application/json"})

    def _get(self, path: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        cached = None
        if self.cache_dir:
            key = path.strip("/").replace("/", "_") + "_" + "_".join(f"{k}{v}" for k, v in sorted(params.items()))
            cached = Path(self.cache_dir) / f"{key}.json"
            if cached.exists() and time.time() - cached.stat().st_mtime < self.ttl:
                return json.loads(cached.read_text())
        for attempt in range(3):  # the API is slow at times: retry timeouts and 5xx before giving up
            try:
                resp = self.session.get(f"{BASE_URL}{path}", params=params, timeout=60)
                if resp.status_code < 500:
                    resp.raise_for_status()
                    data = resp.json()
                    if cached is not None:
                        cached.parent.mkdir(parents=True, exist_ok=True)
                        cached.write_text(json.dumps(data))
                    return data
                err = requests.HTTPError(f"{resp.status_code} from CFBD {path}")
            except (requests.Timeout, requests.ConnectionError) as exc:
                err = exc
            if attempt == 2:
                raise err
            time.sleep(3 * (attempt + 1))

    def calendar(self, year: int) -> list[dict]:
        return self._get("/calendar", year=year)

    def games(self, year: int, week: int | None = None, season_type: str = "regular") -> list[dict]:
        return self._get("/games", year=year, week=week, seasonType=season_type)

    def lines(self, year: int, week: int, season_type: str = "regular") -> list[dict]:
        return self._get("/lines", year=year, week=week, seasonType=season_type)

    def season_stats(self, year: int, end_week: int | None = None) -> list[dict]:
        return self._get("/stats/season", year=year, endWeek=end_week)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def current_week(calendar: list[dict], now: datetime | None = None) -> tuple[int, str] | None:
    """Return (week, seasonType) of the first calendar week that has not ended yet."""
    now = now or datetime.now(timezone.utc)
    for entry in sorted(calendar, key=lambda e: e.get("startDate", "")):
        end = entry.get("endDate") or entry.get("lastGameStart")
        if end and _parse_time(end) >= now:
            return int(entry["week"]), entry.get("seasonType", "regular")
    return None


def to_games(raw_games: list[dict], raw_lines: list[dict] | None = None) -> list[Game]:
    """Convert CFBD games (and optional lines) into shared Game objects."""
    lines_by_id = {ln.get("id"): ln for ln in raw_lines or []}
    out = []
    for gm in raw_games:
        def g(camel, snake, default=None):
            return gm.get(camel, gm.get(snake, default))
        start = g("startDate", "start_date")
        if not start:
            continue
        hp, ap = g("homePoints", "home_points"), g("awayPoints", "away_points")
        game = Game(
            id=str(gm.get("id")), sport="cfb", start=_parse_time(start),
            home=g("homeTeam", "home_team"), away=g("awayTeam", "away_team"),
            neutral=bool(g("neutralSite", "neutral_site", False)),
            completed=bool(gm.get("completed")) and hp is not None and ap is not None,
            home_score=hp, away_score=ap, week=gm.get("week"),
            info={"home_fbs": str(g("homeClassification", "home_division", "fbs")).lower() == "fbs",
                  "away_fbs": str(g("awayClassification", "away_division", "fbs")).lower() == "fbs"},
        )
        entry = lines_by_id.get(gm.get("id"))
        if entry:
            game.odds = _pick_line(entry)
        out.append(game)
    return out


def _num(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _pick_line(entry: dict) -> Odds | None:
    lines = [ln for ln in entry.get("lines", []) if ln.get("spread") is not None]
    if not lines:
        return None
    preferred = ("consensus", "draftkings", "espn bet", "bovada")
    lines.sort(key=lambda ln: preferred.index((ln.get("provider") or "").lower())
               if (ln.get("provider") or "").lower() in preferred else len(preferred))
    ln = lines[0]
    return Odds(
        provider=ln.get("provider", ""),
        spread=_num(ln.get("spread")), spread_open=_num(ln.get("spreadOpen", ln.get("spread_open"))),
        total=_num(ln.get("overUnder", ln.get("over_under"))),
        total_open=_num(ln.get("overUnderOpen", ln.get("over_under_open"))),
        home_ml=_num(ln.get("homeMoneyline", ln.get("home_moneyline"))),
        away_ml=_num(ln.get("awayMoneyline", ln.get("away_moneyline"))),
    )


def pace_factors(season_stats: list[dict]) -> dict[str, float]:
    """Offensive plays per game relative to the league average (1.0 = average tempo)."""
    totals: dict[str, dict[str, float]] = {}
    for row in season_stats:
        name = row.get("statName", row.get("stat_name"))
        if name in ("rushingAttempts", "passAttempts", "games"):
            totals.setdefault(row["team"], {})[name] = float(row.get("statValue", row.get("stat_value")) or 0)
    per_game = {t: (s.get("rushingAttempts", 0) + s.get("passAttempts", 0)) / s["games"]
                for t, s in totals.items() if s.get("games")}
    per_game = {t: v for t, v in per_game.items() if v > 0}
    if not per_game:
        return {}
    avg = sum(per_game.values()) / len(per_game)
    return {t: v / avg for t, v in per_game.items()}
