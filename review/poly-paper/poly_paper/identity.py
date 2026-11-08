"""Final event/outcome identity. A mapper proposal is not trusted.

A fill is identified only when one open event matches both participants, the
sport league, and the America/Chicago game date, and exactly one outcome side
matches the requested team or total direction. Shared nicknames, titles, and
provider ids do not select an event.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .canonical import ct_date, normalize_name
from .mapper import LEAGUE
from .provider import ParsedEvent, ParsedMarket, ParsedSide, ParsedTeam, distinctive, parse_snapshot

_ML = {"h2h", "ml", "moneyline"}
_SPREAD = {"spread", "ats"}
_TOTAL = {"total"}


@dataclass
class IdentityResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sport: str | None = None
    league: str | None = None
    event_id: str | None = None
    event_slug: str | None = None
    event_date: str | None = None
    participants: list | None = None
    outcome: dict | None = None
    token_id: str | None = None
    token_status: str | None = None
    side: str | None = None


def _fail(reasons, **kwargs) -> IdentityResult:
    clean = []
    for reason in reasons:
        if reason not in clean:
            clean.append(reason)
    return IdentityResult(ok=False, reasons=clean, **kwargs)


def _team_matches(name: str, team: ParsedTeam) -> bool:
    """True when one full alias has the same distinctive tokens as this name.

    Substring and extra-school tokens do not match: "Samford" matches "Samford
    Bulldogs" because both reduce to {samford}, and does not match "North Samford".
    """
    dist = distinctive(normalize_name(name))
    if not dist:
        return False
    return dist in team.alias_signatures


def _which_teams(name: str, teams: list[ParsedTeam]) -> list[ParsedTeam]:
    return [team for team in teams if _team_matches(name, team)]


def _entry_game_date(entry: dict):
    start = entry.get("start")
    start_date = ct_date(start) if isinstance(start, str) else None
    bare = entry.get("date") if isinstance(entry.get("date"), str) else None
    if isinstance(start, str) and start.strip() and start_date is None:
        return None, "naive_or_unparsed"
    if bare and start_date and bare != start_date:
        return None, "disagree"
    if start_date:
        return start_date, "ok"
    if bare and len(bare) == 10:
        return bare, "ok"
    return None, "missing"


def _pick_team(pick: str) -> str:
    text = normalize_name(pick)
    text = re.sub(r"\b(ml|moneyline)\b", " ", text)
    text = re.sub(r"\s+[+-]?\d+(?:\.\d+)?\s*$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _pick_total(pick: str):
    norm = normalize_name(pick)
    if norm.startswith("over"):
        return "over"
    if norm.startswith("under"):
        return "under"
    return None


def _side_name(side: ParsedSide) -> str:
    if side.long is True:
        return "long"
    if side.long is False:
        return "short"
    return ""


def _refers_to(side: ParsedSide, teams: list[ParsedTeam]):
    """Which roster team a market side names. None if it does not name exactly one."""
    by_team = _which_teams_from_parsed(side.team, teams) if side.team is not None else []
    by_desc = []
    if side.description_norm:
        desc = distinctive(side.description_norm)
        by_desc = [team for team in teams if desc and desc in team.alias_signatures]
    if by_team and by_desc:
        if {id(team) for team in by_team} != {id(team) for team in by_desc}:
            return "contradiction"
        return by_team[0] if len(by_team) == 1 else "ambiguous"
    chosen = by_team or by_desc
    if len(chosen) == 1:
        return chosen[0]
    if len(chosen) > 1:
        return "ambiguous"
    return None


def _which_teams_from_parsed(team: ParsedTeam, teams: list[ParsedTeam]) -> list[ParsedTeam]:
    """Match a side's team object onto the roster by a shared full-alias signature."""
    if not team.alias_signatures:
        return []
    return [roster for roster in teams if team.alias_signatures & roster.alias_signatures]


def _direction_side(market: ParsedMarket, direction: str) -> ParsedSide | None | str:
    hits = [side for side in market.sides if side.description_norm == direction]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return "ambiguous"
    return None


def _usable_market(market: ParsedMarket, kind: str) -> bool:
    return market.kind == kind and market.closed is False and market.slug_status == "ok" and bool(market.slug)


def _token_gate(side: ParsedSide, claimed_token):
    if side.token_status in ("disagree", "malformed", "unbound"):
        return False, [f"token_{side.token_status}"], side.token_status, None
    if side.token_status == "ok":
        if claimed_token is not None and str(claimed_token) != side.token_id:
            return False, ["token_disagrees"], side.token_status, side.token_id
        return True, [], "ok", side.token_id
    # omitted
    if claimed_token not in (None, ""):
        return False, ["token_not_in_provider"], "omitted", None
    return True, [], "omitted_by_provider", None


def _participant_blob(entry, home_team: ParsedTeam, away_team: ParsedTeam):
    def one(role, label, team: ParsedTeam):
        return {
            "role": role,
            "entry": label,
            "provider_id": team.provider_id,
            "id_status": team.id_status,
            "aliases": list(team.aliases),
        }
    return [one("away", entry.get("away"), away_team), one("home", entry.get("home"), home_team)]


def _duplicate_events(events: list[ParsedEvent]) -> list[ParsedEvent]:
    """Collapse exact repeats (same id set, slug, date, league, roster tokens)."""
    seen = set()
    out = []
    for event in events:
        roster = tuple(sorted(tuple(sorted(team.strong_tokens)) for team in event.teams))
        key = (tuple(sorted(event.provider_ids)), event.slug, event.event_date, event.league, roster)
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out


def assess(entry: dict, snapshot, claimed_slug=None, claimed_side=None, claimed_token=None) -> IdentityResult:
    if not isinstance(entry, dict):
        return _fail(["request_malformed"])
    sport = str(entry.get("sport") or "").lower()
    league = LEAGUE.get(sport)
    market_name = str(entry.get("market") or "").lower()
    if market_name not in _ML | _SPREAD | _TOTAL | {"prop"}:
        return _fail(["unknown_market"], sport=sport or None)
    if not league:
        return _fail(["unknown_sport"], sport=sport or None)
    home = entry.get("home")
    away = entry.get("away")
    if not isinstance(home, str) or not home.strip() or not isinstance(away, str) or not away.strip():
        return _fail(["participants_missing"], sport=sport, league=league)
    if not distinctive(normalize_name(home)) or not distinctive(normalize_name(away)):
        return _fail(["mascot_only"], sport=sport, league=league)
    game_date, date_state = _entry_game_date(entry)
    if game_date is None:
        return _fail(["date_unknown" if date_state != "disagree" else "entry_date_disagree"], sport=sport, league=league)

    events = _duplicate_events(parse_snapshot(snapshot))
    if snapshot in (None, "", []) or (isinstance(snapshot, dict) and not events and "events" not in snapshot and "markets" not in snapshot and "slug" not in snapshot and "teams" not in snapshot):
        return _fail(["snapshot_unparsed"], sport=sport, league=league)
    if not events:
        return _fail(["snapshot_unparsed"], sport=sport, league=league)

    source_id = entry.get("source_game_id")
    source_id = None if source_id in (None, "") else str(source_id)
    claimed = claimed_slug.strip() if isinstance(claimed_slug, str) else None

    participant_hits = []
    wrong_sport = False
    wrong_date = False
    closed_hit = False
    id_without_participants = False
    malformed_hit = False
    for event in events:
        home_hits = _which_teams(home, event.teams)
        away_hits = _which_teams(away, event.teams)
        names_ok = (
            event.roster_status == "ok"
            and len(home_hits) == 1
            and len(away_hits) == 1
            and home_hits[0] is not away_hits[0]
        )
        if source_id and source_id in event.provider_ids and not names_ok:
            id_without_participants = True
        if not names_ok:
            continue
        if event.league_status != "ok" or event.league != league:
            if event.league_status == "disagree":
                malformed_hit = True
            else:
                wrong_sport = True
            continue
        if event.date_status != "ok" or event.event_date != game_date:
            if event.date_status == "disagree":
                wrong_date = True
                malformed_hit = True
            elif event.date_status in ("naive", "malformed", "missing"):
                malformed_hit = True
            else:
                wrong_date = True
            continue
        if event.closed is None:
            malformed_hit = True
            continue
        if event.closed:
            closed_hit = True
            continue
        participant_hits.append((event, away_hits[0], home_hits[0]))

    base = dict(sport=sport, league=league, event_date=game_date)
    if len(participant_hits) == 0:
        reasons = ["no_unique_event"]
        if id_without_participants:
            reasons.append("provider_id_without_participants")
        if wrong_sport:
            reasons.append("wrong_sport")
        if wrong_date:
            reasons.append("wrong_date")
        if malformed_hit and wrong_date:
            reasons.append("provider_date_disagree")
        if closed_hit:
            reasons.append("event_closed")
        if malformed_hit:
            reasons.append("provider_identity_malformed")
        if claimed and _claimed_on_other(events, claimed, set()):
            reasons.append("cross_game")
        return _fail(reasons, **base)
    if len(participant_hits) > 1:
        return _fail(["ambiguous_event"], **base)

    event, away_team, home_team = participant_hits[0]
    if away_team.id_status == "disagree" or home_team.id_status == "disagree":
        return _fail(["team_id_disagree"], **base, event_slug=event.slug, event_id=_single_id(event))
    participants = _participant_blob(entry, home_team, away_team)
    located = dict(
        sport=sport,
        league=league,
        event_id=_single_id(event),
        event_slug=event.slug,
        event_date=event.event_date,
        participants=participants,
    )
    if market_name in _ML:
        return _finish_side(
            entry, event, away_team, home_team, "winner", _pick_team(str(entry.get("pick") or "")),
            claimed, claimed_side, claimed_token, located,
        )
    if market_name in _SPREAD:
        return _finish_side(
            entry, event, away_team, home_team, "spread", _pick_team(str(entry.get("pick") or "")),
            claimed, claimed_side, claimed_token, located,
        )
    if market_name in _TOTAL:
        return _finish_total(entry, event, claimed, claimed_side, claimed_token, located)
    return _finish_prop(entry, event, claimed, claimed_side, claimed_token, located)


def _single_id(event: ParsedEvent):
    return event.primary_id


def _claimed_on_other(events, claimed, allowed_slugs) -> bool:
    for event in events:
        for market in event.markets:
            if market.slug == claimed and claimed not in allowed_slugs:
                return True
    return False


def _finish_side(entry, event, away_team, home_team, kind, pick_norm, claimed, claimed_side, claimed_token, located):
    if not distinctive(pick_norm):
        return _fail(["mascot_only"], **located)
    pick_hits = _which_teams(pick_norm, [away_team, home_team])
    if len(pick_hits) != 1:
        return _fail(["outcome_not_found" if not pick_hits else "ambiguous_outcome"], **located)
    pick_team = pick_hits[0]
    matches = []
    bad_flags = False
    contradiction = False
    for market in event.markets:
        if not _usable_market(market, kind):
            if market.kind == kind and market.closed is None:
                contradiction = True
            continue
        longs = [side.long for side in market.sides]
        if len(longs) >= 2 and longs[0] == longs[1] and longs[0] is not None:
            bad_flags = True
            continue
        for side in market.sides:
            ref = _refers_to(side, [away_team, home_team])
            if ref == "contradiction":
                contradiction = True
                continue
            if ref == "ambiguous" or ref is None:
                continue
            if ref is pick_team:
                matches.append((market, side))
    if contradiction:
        return _fail(["side_identity_contradiction"], **located)
    if bad_flags and not matches:
        return _fail(["side_flags_ambiguous"], **located)
    if len(matches) == 0:
        return _fail(["outcome_not_found"], **located)
    if len(matches) > 1:
        return _fail(["ambiguous_outcome"], **located)
    market, side = matches[0]
    return _bind(entry, event, market, side, pick_team, claimed, claimed_side, claimed_token, located, kind)


def _finish_total(entry, event, claimed, claimed_side, claimed_token, located):
    direction = _pick_total(str(entry.get("pick") or ""))
    if direction is None:
        return _fail(["outcome_not_found"], **located)
    matches = []
    for market in event.markets:
        if not _usable_market(market, "total"):
            continue
        found = _direction_side(market, direction)
        if found == "ambiguous":
            return _fail(["ambiguous_outcome"], **located)
        if isinstance(found, ParsedSide):
            matches.append((market, found))
    if len(matches) == 0:
        return _fail(["outcome_not_found"], **located)
    if len(matches) > 1:
        return _fail(["ambiguous_outcome"], **located)
    market, side = matches[0]
    return _bind(entry, event, market, side, None, claimed, claimed_side, claimed_token, located, "total", direction=direction)


def _finish_prop(entry, event, claimed, claimed_side, claimed_token, located):
    prop = entry.get("prop") if isinstance(entry.get("prop"), dict) else None
    if not prop:
        return _fail(["identity_unknown"], **located)
    player = prop.get("player")
    stat = prop.get("stat")
    line = prop.get("line")
    if not isinstance(player, str) or not isinstance(stat, str) or isinstance(line, bool) or not isinstance(line, (int, float, str)):
        return _fail(["identity_unknown"], **located)
    want_player = normalize_name(player)
    want_stat = str(stat).strip().upper()
    want_line = str(line)
    if not distinctive(want_player) and not want_player:
        return _fail(["identity_unknown"], **located)
    matches = []
    for market in event.markets:
        if not _usable_market(market, "prop"):
            continue
        if market.player is None or market.stat is None or market.line is None:
            continue
        if normalize_name(market.player) != want_player:
            continue
        if str(market.stat).strip().upper() != want_stat or str(market.line) != want_line:
            continue
        direction = _pick_total(str(entry.get("pick") or "")) or str(prop.get("side") or "").lower()
        found = _direction_side(market, direction) if direction in ("over", "under") else None
        if not isinstance(found, ParsedSide):
            # A prop side may be the player over, carried as long/short only.
            longs = [s for s in market.sides if s.long is True]
            if direction in ("over", "long") and len(longs) == 1:
                found = longs[0]
            else:
                continue
        matches.append((market, found))
    if len(matches) != 1:
        return _fail(["identity_unknown" if not matches else "ambiguous_outcome"], **located)
    market, side = matches[0]
    return _bind(entry, event, market, side, None, claimed, claimed_side, claimed_token, located, "prop")


def _bind(entry, event, market, side, pick_team, claimed, claimed_side, claimed_token, located, kind, direction=None):
    if side.long is None:
        return _fail(["side_unknown"], **located)
    side_name = _side_name(side)
    if claimed_side != side_name:
        return _fail(["side_disagrees"], **located, side=side_name)
    if not claimed or claimed != market.slug:
        return _fail(["slug_disagrees"], **located, side=side_name)
    ok_token, token_reasons, token_status, token_id = _token_gate(side, claimed_token)
    if not ok_token:
        return _fail(token_reasons, **located, side=side_name, token_id=token_id, token_status=token_status)
    notes = []
    if token_status == "omitted_by_provider":
        notes.append("token_omitted_by_provider")
    outcome = {
        "kind": kind,
        "pick": entry.get("pick"),
        "market_slug": market.slug,
        "market_type": market.raw_type,
        "side": side_name,
        "direction": direction,
        "team_provider_id": None if pick_team is None else pick_team.provider_id,
        "token_id": token_id,
        "token_status": token_status,
    }
    event_id = located.get("event_id")
    return IdentityResult(
        ok=True,
        reasons=[],
        notes=notes,
        sport=located["sport"],
        league=located["league"],
        event_id=event_id,
        event_slug=event.slug,
        event_date=located["event_date"],
        participants=located["participants"],
        outcome=outcome,
        token_id=token_id,
        token_status=token_status,
        side=side_name,
    )
