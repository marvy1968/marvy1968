"""Parse sports-event payloads into identity fields.

Supported shapes are the ones the baseline public client and the screened fixtures
actually use, plus the field aliases those payloads already vary on (token id name,
start-time name, teams on the event or only on market sides). A title or question
is not an identity source. Missing ids stay missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .canonical import ct_date, normalize_name, slug_date
from .mapper import LEAGUE, _SHARED_NICKS, _STOP

LEAGUE_CODES = set(LEAGUE.values())
STRONG_FIELDS = ("name", "alias", "safeName", "displayName", "shortName", "shortDisplayName")
TOKEN_KEYS = ("tokenId", "token_id", "clobTokenId")
DATE_FIELDS = ("startTime", "startDate", "eventDate", "gameStartTime")
EVENT_ID_FIELDS = ("id", "gameId", "providerGameId", "eventId")
TEAM_ID_FIELDS = ("id", "teamId")
_EXTRA_STOP = _STOP | {"and"}


def distinctive(norm: str) -> frozenset[str]:
    return frozenset(tok for tok in (norm or "").split() if tok not in _SHARED_NICKS and tok not in _EXTRA_STOP)


@dataclass
class ParsedTeam:
    provider_id: str | None
    id_status: str
    strong_tokens: frozenset[str]
    strong_norms: frozenset[str]
    aliases: tuple[str, ...]
    # One distinctive-token set per full alias. "Samford" and "Samford Bulldogs"
    # share {samford}. "North Samford" is {north, samford} and does not match.
    alias_signatures: frozenset[frozenset[str]]


@dataclass
class ParsedSide:
    long: bool | None
    team: ParsedTeam | None
    description_norm: str
    token_id: str | None
    token_status: str
    raw_type_notes: tuple[str, ...] = ()


@dataclass
class ParsedMarket:
    slug: str | None
    slug_status: str
    kind: str
    closed: bool | None
    sides: list[ParsedSide]
    player: str | None
    stat: str | None
    line: str | None
    raw_type: str | None


@dataclass
class ParsedEvent:
    primary_id: str | None
    provider_ids: tuple[str, ...]
    slug: str | None
    league: str | None
    league_status: str
    event_date: str | None
    date_status: str
    closed: bool | None
    teams: list[ParsedTeam]
    roster_status: str
    markets: list[ParsedMarket]
    start_instant: datetime | None = None
    start_status: str = "missing"
    notes: list[str] = field(default_factory=list)


def _strings(value) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _id_values(obj, keys) -> list[str]:
    found = []
    if not isinstance(obj, dict):
        return found
    for key in keys:
        if key not in obj:
            continue
        value = obj[key]
        if isinstance(value, bool) or value is None or value == "":
            continue
        if isinstance(value, (str, int)):
            found.append(str(value))
    return found


def parse_team(obj) -> ParsedTeam | None:
    if not isinstance(obj, dict):
        return None
    aliases: list[str] = []
    for key in STRONG_FIELDS:
        aliases.extend(_strings(obj.get(key)))
    # de-dupe, keep order
    seen = set()
    ordered = []
    for alias in aliases:
        if alias not in seen:
            seen.add(alias)
            ordered.append(alias)
    norms = []
    tokens: set[str] = set()
    signatures = []
    for alias in ordered:
        norm = normalize_name(alias)
        if not norm:
            continue
        norms.append(norm)
        tokens.update(norm.split())
        dist = distinctive(norm)
        if dist:
            signatures.append(dist)
    ids = _id_values(obj, TEAM_ID_FIELDS)
    if len(set(ids)) > 1:
        id_status, provider_id = "disagree", None
    elif len(ids) == 1:
        id_status, provider_id = "ok", ids[0]
    else:
        id_status, provider_id = "omitted", None
    if not tokens:
        return None
    return ParsedTeam(
        provider_id, id_status, frozenset(tokens), frozenset(norms), tuple(ordered),
        frozenset(signatures),
    )


def _closed_flag(value):
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return None


def _token(side: dict, market: dict) -> tuple[str | None, str]:
    if not isinstance(side, dict):
        return None, "malformed"
    if "clobTokenIds" in side or "clobTokenIds" in market:
        return None, "unbound"
    values = []
    for key in TOKEN_KEYS:
        if key not in side:
            continue
        value = side[key]
        if value is None or value == "":
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return None, "malformed"
        values.append(str(value))
    unique = set(values)
    if len(unique) > 1:
        return None, "disagree"
    if len(unique) == 1:
        return values[0], "ok"
    return None, "omitted"


def _market_kind(raw_type) -> str:
    text = raw_type.lower() if isinstance(raw_type, str) else ""
    if not text:
        return "unknown"
    if text.endswith("_full_game_winner") or text.endswith("moneyline") or text == "moneyline":
        return "winner"
    if text.endswith("_full_game_spread") or text.endswith("spread"):
        return "spread"
    if "team_points" in text:
        return "other"
    if text.endswith("_full_game_total") or text.endswith("total"):
        return "total"
    if "player" in text or text.endswith("prop") or "prop" in text:
        return "prop"
    return "other"


def _market_slug(market: dict) -> tuple[str | None, str]:
    values = []
    for key in ("slug", "marketSlug"):
        value = market.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    unique = set(values)
    if len(unique) > 1:
        return None, "disagree"
    if len(unique) == 1:
        return values[0], "ok"
    return None, "missing"


def parse_market(market: dict) -> ParsedMarket | None:
    if not isinstance(market, dict):
        return None
    slug, slug_status = _market_slug(market)
    closed = _closed_flag(market.get("closed"))
    sides = []
    raw_sides = market.get("marketSides")
    if raw_sides is None:
        raw_sides = []
    if not isinstance(raw_sides, list):
        return None
    for side in raw_sides:
        if not isinstance(side, dict):
            continue
        long_flag = side.get("long")
        long_value = long_flag if isinstance(long_flag, bool) else None
        description = side.get("description")
        description_norm = normalize_name(description) if isinstance(description, str) else ""
        token_id, token_status = _token(side, market)
        sides.append(ParsedSide(
            long=long_value,
            team=parse_team(side.get("team")) if isinstance(side.get("team"), dict) else None,
            description_norm=description_norm,
            token_id=token_id,
            token_status=token_status,
        ))
    player = market.get("player") if isinstance(market.get("player"), str) else None
    stat = market.get("stat") if isinstance(market.get("stat"), str) else None
    line_value = market.get("line")
    if isinstance(line_value, bool) or not isinstance(line_value, (int, float, str)) or line_value == "":
        line = None
    else:
        line = str(line_value)
    return ParsedMarket(
        slug=slug,
        slug_status=slug_status,
        kind=_market_kind(market.get("sportsMarketType")),
        closed=closed,
        sides=sides,
        player=player,
        stat=stat,
        line=line,
        raw_type=market.get("sportsMarketType") if isinstance(market.get("sportsMarketType"), str) else None,
    )


def _team_league_values(event: dict) -> list[str]:
    """League on every named team. A partial set is not a league."""
    teams = event.get("teams")
    if not isinstance(teams, list):
        return []
    found = []
    named = 0
    for team in teams:
        if not isinstance(team, dict):
            continue
        name = team.get("name") or team.get("displayName") or team.get("alias") or team.get("safeName")
        if not isinstance(name, str) or not name.strip():
            continue
        named += 1
        value = team.get("league")
        if isinstance(value, str) and value.strip():
            found.append(value.strip().lower())
    if named and len(found) == named:
        return found
    return []


def _league(event: dict) -> tuple[str | None, str]:
    fields = []
    for key in ("league", "sport"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            fields.append(value.strip().lower())
    # Documented US events put league on teams[], not on the event, and omit ordering.
    fields.extend(_team_league_values(event))
    slug = event.get("slug") if isinstance(event.get("slug"), str) else ""
    slug_codes = [tok for tok in slug.lower().split("-") if tok in LEAGUE_CODES]
    slug_unique = set(slug_codes)
    field_unique = set(fields)
    if len(field_unique) > 1:
        return None, "disagree"
    if len(slug_unique) > 1:
        return None, "disagree"
    if field_unique and slug_unique and field_unique != slug_unique:
        return None, "disagree"
    if field_unique:
        return next(iter(field_unique)), "ok"
    if len(slug_unique) == 1:
        return next(iter(slug_unique)), "ok"
    return None, "missing"


def _aware_instant(value):
    """Exact aware timestamp. A bare calendar date is not a start instant."""
    if not isinstance(value, str) or not value.strip():
        return None, "missing"
    text = value.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return None, "date_only"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, "malformed"
    if parsed.tzinfo is None:
        return None, "naive"
    return parsed.astimezone(timezone.utc), "ok"


def _start_instant(event: dict):
    instants = []
    saw_naive = False
    saw_bad = False
    saw_date_only = False
    for key in ("startTime", "startDate", "gameStartTime"):
        if key not in event or event[key] in (None, ""):
            continue
        instant, status = _aware_instant(event[key])
        if status == "ok":
            instants.append(instant)
        elif status == "naive":
            saw_naive = True
        elif status == "date_only":
            saw_date_only = True
        else:
            saw_bad = True
    unique = set(instants)
    if len(unique) > 1:
        return None, "disagree"
    if len(unique) == 1:
        return next(iter(unique)), "ok"
    if saw_naive:
        return None, "naive"
    if saw_bad:
        return None, "malformed"
    if saw_date_only:
        return None, "date_only"
    return None, "missing"


def _event_date(event: dict) -> tuple[str | None, str]:
    dates = []
    saw_naive = False
    saw_unparsed = False
    for key in DATE_FIELDS:
        if key not in event or event[key] in (None, ""):
            continue
        value = event[key]
        if not isinstance(value, str):
            saw_unparsed = True
            continue
        # A bare calendar date is already a date, not a timestamp.
        if len(value) == 10 and value[4] == "-" and value[7] == "-":
            dates.append(value)
            continue
        parsed = ct_date(value)
        if parsed is None:
            try:
                probe = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if probe.tzinfo is None:
                    saw_naive = True
                else:
                    saw_unparsed = True
            except ValueError:
                saw_unparsed = True
            continue
        dates.append(parsed)
    if saw_naive:
        return None, "naive"
    slug = slug_date(event.get("slug") if isinstance(event.get("slug"), str) else None)
    if slug:
        dates.append(slug)
    unique = set(dates)
    if len(unique) > 1:
        return None, "disagree"
    if len(unique) == 1:
        return next(iter(unique)), "ok"
    if saw_unparsed:
        return None, "malformed"
    return None, "missing"


def _roster(event: dict, markets: list[ParsedMarket]) -> tuple[list[ParsedTeam], str]:
    declared = []
    raw_teams = event.get("teams")
    if raw_teams is not None:
        if not isinstance(raw_teams, list):
            return [], "malformed"
        for item in raw_teams:
            team = parse_team(item)
            if team is not None:
                declared.append(team)
    derived = []
    for market in markets:
        if market.kind != "winner" or market.closed is None:
            continue
        for side in market.sides:
            if side.team is not None:
                derived.append(side.team)
    if declared and derived:
        declared_sig = {team.strong_tokens for team in declared}
        derived_sig = {team.strong_tokens for team in derived}
        # Sides may omit a team object. A side that names a school outside the
        # declared roster is a contradiction, not an extra alias.
        if not derived_sig <= declared_sig:
            return declared, "disagree"
    if len(declared) == 2:
        return declared, "ok"
    if len(declared) > 2:
        return declared, "ambiguous"
    # Fall back to distinct side teams when the event does not declare a roster.
    unique = []
    seen = set()
    for team in derived:
        key = team.strong_tokens
        if key not in seen:
            seen.add(key)
            unique.append(team)
    if len(unique) == 2:
        return unique, "ok"
    if len(unique) > 2:
        return unique, "ambiguous"
    return declared or unique, "missing"


def parse_event(event: dict) -> ParsedEvent | None:
    if not isinstance(event, dict):
        return None
    markets = []
    raw_markets = event.get("markets") if isinstance(event.get("markets"), list) else []
    for market in raw_markets:
        parsed = parse_market(market)
        if parsed is not None:
            markets.append(parsed)
    league, league_status = _league(event)
    event_date, date_status = _event_date(event)
    start_instant, start_status = _start_instant(event)
    teams, roster_status = _roster(event, markets)
    closed = _closed_flag(event.get("closed"))
    slug = event.get("slug") if isinstance(event.get("slug"), str) and event.get("slug").strip() else None
    primary_values = _id_values(event, ("id",))
    alias_values = _id_values(event, ("gameId", "providerGameId", "eventId"))
    primary_id = primary_values[0] if len(set(primary_values)) == 1 else None
    return ParsedEvent(
        primary_id=primary_id,
        provider_ids=tuple(dict.fromkeys([*primary_values, *alias_values])),
        slug=slug,
        league=league,
        league_status=league_status,
        event_date=event_date,
        date_status=date_status,
        closed=closed,
        teams=teams,
        roster_status=roster_status,
        markets=markets,
        start_instant=start_instant,
        start_status=start_status,
    )


def parse_snapshot(snapshot) -> list[ParsedEvent]:
    """Return events from a list, an {events: [...]} body, or one event object."""
    if isinstance(snapshot, list):
        raw = snapshot
    elif isinstance(snapshot, dict) and isinstance(snapshot.get("events"), list):
        raw = snapshot["events"]
    elif isinstance(snapshot, dict) and (
        "markets" in snapshot or "teams" in snapshot or "slug" in snapshot
    ):
        raw = [snapshot]
    else:
        return []
    events = []
    for item in raw:
        parsed = parse_event(item)
        if parsed is not None:
            events.append(parsed)
    return events
