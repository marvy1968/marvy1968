"""Thin client for the CollegeFootballData.com API (https://api.collegefootballdata.com)."""

from datetime import datetime, timezone

import requests

BASE_URL = "https://api.collegefootballdata.com"


class CFBDClient:
    def __init__(self, api_key: str, session: requests.Session | None = None):
        if not api_key:
            raise ValueError("CFBD_API_KEY is not set (get a free key at collegefootballdata.com/key)")
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}", "Accept": "application/json"})

    def _get(self, path: str, **params):
        resp = self.session.get(f"{BASE_URL}{path}", params={k: v for k, v in params.items() if v is not None}, timeout=30)
        resp.raise_for_status()
        return resp.json()

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
