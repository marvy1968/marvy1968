"""ESPN public scoreboard feed: schedules, results, posted odds and MLB probable pitchers.

Used for NBA, WNBA, NHL, MLB and soccer. The endpoint is public but undocumented, so
parsing is deliberately defensive: anything missing simply becomes None.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ..models import Game, Odds

log = logging.getLogger(__name__)
BASE = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _f(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().upper()
    if s in ("EVEN", "EV", "PK", "PICK"):
        return 0.0 if s in ("PK", "PICK") else 100.0
    m = _NUM.search(s)
    return float(m.group()) if m else None


def _price(value) -> float | None:
    v = _f(value)
    return None if v is None or v == 0 else v


def _parse_time(value: str) -> datetime:
    value = value.replace("Z", "+00:00")
    if len(value) == 22 and value[16] == "+":  # "2026-10-07T23:00+00:00" (no seconds)
        value = value[:16] + ":00" + value[16:]
    return datetime.fromisoformat(value)


def parse_odds(comp: dict, home_abbr: str, away_abbr: str) -> Odds | None:
    entries = comp.get("odds") or []
    if not entries:
        return None
    o = entries[0]
    odds = Odds(provider=(o.get("provider") or {}).get("name", "ESPN"))
    h_odds, a_odds = o.get("homeTeamOdds") or {}, o.get("awayTeamOdds") or {}

    # Spread: trust the "details" text ("BOS -5.5") for the sign, fall back to favorite flags.
    spread = _f(o.get("spread"))
    details = (o.get("details") or "").strip()
    if details:
        token = details.split(" ")[0].upper()
        num = _f(details.split(" ")[-1]) if " " in details else None
        if details.upper() in ("EVEN", "PK", "PICK"):
            spread = 0.0
        elif num is not None and token == home_abbr.upper():
            spread = num
        elif num is not None and token == away_abbr.upper():
            spread = -num
    if spread is not None and spread != 0 and not details:
        if h_odds.get("favorite") is True:
            spread = -abs(spread)
        elif a_odds.get("favorite") is True:
            spread = abs(spread)
    odds.spread = spread
    odds.total = _f(o.get("overUnder"))
    odds.home_ml = _price(h_odds.get("moneyLine"))
    odds.away_ml = _price(a_odds.get("moneyLine"))
    odds.home_spread_price = _price(h_odds.get("spreadOdds")) or -110
    odds.away_spread_price = _price(a_odds.get("spreadOdds")) or -110
    odds.over_price = _price(o.get("overOdds")) or -110
    odds.under_price = _price(o.get("underOdds")) or -110
    odds.draw_ml = _price((o.get("drawOdds") or {}).get("moneyLine"))

    # Newer feeds carry open/close blocks.
    ps = o.get("pointSpread") or {}
    home_ps = ps.get("home") or {}
    if (home_ps.get("open") or {}).get("line") is not None:
        odds.spread_open = _f(home_ps["open"]["line"])
    if odds.spread is None and (home_ps.get("close") or {}).get("line") is not None:
        odds.spread = _f(home_ps["close"]["line"])
    tot = (o.get("total") or {}).get("over") or {}
    if (tot.get("open") or {}).get("line") is not None:
        odds.total_open = _f(tot["open"]["line"])
    ml = o.get("moneyline") or {}
    if odds.home_ml is None:
        odds.home_ml = _price(((ml.get("home") or {}).get("close") or {}).get("odds"))
        odds.away_ml = _price(((ml.get("away") or {}).get("close") or {}).get("odds"))
    if odds.spread is None and odds.total is None and odds.home_ml is None:
        return None
    return odds


def _probable_era(competitor: dict) -> tuple[str | None, float | None]:
    for p in competitor.get("probables") or []:
        name = (p.get("athlete") or {}).get("displayName")
        era = None
        for stat in p.get("statistics") or []:
            if str(stat.get("abbreviation") or stat.get("name") or "").upper() == "ERA":
                era = _f(stat.get("displayValue") or stat.get("value"))
        if era is None:
            m = re.search(r"(\d+\.\d+)\s*ERA", json.dumps(p))
            era = float(m.group(1)) if m else None
        if name:
            return name, era
    return None, None


def parse_event(event: dict, sport: str, league: str = "") -> Game | None:
    comps = event.get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    if "home" not in sides or "away" not in sides:
        return None
    home, away = sides["home"], sides["away"]
    status = ((event.get("status") or comp.get("status") or {}).get("type") or {})
    completed = bool(status.get("completed")) and status.get("state") == "post"
    game = Game(
        id=str(event.get("id")),
        sport=sport,
        start=_parse_time(event.get("date") or comp.get("date")),
        home=home["team"]["displayName"],
        away=away["team"]["displayName"],
        neutral=bool(comp.get("neutralSite")),
        completed=completed,
        home_score=_f(home.get("score")) if completed else None,
        away_score=_f(away.get("score")) if completed else None,
        league=league,
        week=(event.get("week") or {}).get("number"),
    )
    game.odds = parse_odds(comp, home["team"].get("abbreviation", ""), away["team"].get("abbreviation", ""))
    for side, comp_side in (("home", home), ("away", away)):
        name, era = _probable_era(comp_side)
        if name:
            game.info[f"{side}_pitcher"] = name
            game.info[f"{side}_pitcher_era"] = era
    if (event.get("season") or {}).get("type") == 3:
        game.info["postseason"] = True
    status_name = str(status.get("name", ""))
    if status_name in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_SUSPENDED"):
        game.info["postponed"] = True
    return game


class ESPNClient:
    def __init__(self, cache_dir: Path | None = None, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "Mozilla/5.0 (marv-predict-bot)")
        self.cache_dir = cache_dir

    def scoreboard(self, path: str, start: datetime, end: datetime) -> list[dict]:
        dates = f"{start:%Y%m%d}-{end:%Y%m%d}"
        cache = None
        # Only cache ranges that finished more than two days ago (results are final).
        if self.cache_dir and end < datetime.now(timezone.utc) - timedelta(days=2):
            cache = self.cache_dir / f"espn_{path.replace('/', '_')}_{dates}.json"
            if cache.exists():
                return json.loads(cache.read_text())
        resp = self.session.get(BASE.format(path=path), params={"dates": dates, "limit": 1000}, timeout=30)
        resp.raise_for_status()
        events = resp.json().get("events", [])
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(events))
        return events

    def games(self, path: str, sport: str, start: datetime, end: datetime, league: str = "") -> list[Game]:
        out, seen = [], set()
        # Fixed half-month chunks (1st-15th, 16th-end) so finished chunks can be cached between runs.
        cursor = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
        while cursor <= end:
            if cursor.day == 1:
                chunk_end = cursor.replace(day=15)
            else:
                chunk_end = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            if chunk_end >= start - timedelta(days=1):
                try:
                    for ev in self.scoreboard(path, cursor, chunk_end):
                        gm = parse_event(ev, sport, league)
                        if gm and gm.id not in seen and start <= gm.start <= end:
                            seen.add(gm.id)
                            out.append(gm)
                except requests.RequestException as exc:
                    log.warning("ESPN %s %s..%s failed: %s", path, cursor.date(), chunk_end.date(), exc)
            cursor = chunk_end + timedelta(days=1)
        return out
