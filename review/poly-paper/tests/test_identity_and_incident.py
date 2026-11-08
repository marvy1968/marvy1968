"""Identity cases, including the screened Samford/Boyce incident shape."""
import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path

from poly_paper.identity import assess
from poly_paper.mapper import map_entry

from helpers import FIXTURES, exact_request, load_json, samford_entry, samford_event, team

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "provenance" / "baseline" / "polymarket-paper"


def _load(path, name):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class _Client:
    def __init__(self, events, bbo=None):
        self._events = events
        self.bbo_calls = []
        self._bbo = bbo or {
            "state": "MARKET_STATE_OPEN",
            "bestAsk": {"value": "0.40"},
            "bestBid": {"value": "0.39"},
        }

    def league_events(self, league):
        return self._events

    def bbo(self, slug):
        self.bbo_calls.append(slug)
        return self._bbo


def _boyce_events():
    payload = load_json(FIXTURES / "synthetic-provider-policy-cases.json")
    return payload["league_event_response"]["events"]


def _observed_entry():
    observed = load_json(FIXTURES / "observed-case619-minimal.json")
    meta = observed["proven_source_game_metadata"]
    return {
        "key": observed["key"],
        "sport": meta["sport"],
        "market": "ml",
        "pick": observed["pick"],
        "home": meta["home"],
        "away": meta["away"],
        "start": meta["scheduled_start"],
        "source_game_id": meta["provider_game_id"],
        "me_type": "marv",
    }


def test_retained_mapper_rejects_boyce_and_backup_accepts_it():
    events = _boyce_events()
    # The synthetic fixture has no start clock; the name bug does not depend on one.
    entry = samford_entry(start="2030-11-07T21:00:00Z")
    assert map_entry(entry, _Client(events)) is None
    backup = _load(BASE / "mapper.py.baseline-20261107", "baseline_mapper_backup")
    mapped = backup.map_entry(entry, _Client(events))
    assert mapped["slug"] == "synthetic-boyce-liberty-winner"
    assert mapped["team"] == "Bulldogs"
    assert mapped["side"] == "long"


def test_retained_mapper_accepts_full_alias_event():
    event = samford_event()
    mapped = map_entry(samford_entry(), _Client([event]))
    assert mapped["slug"] == "synthetic-samford-unlv-winner"
    assert mapped["side"] == "long"
    assert mapped["event"] == event["slug"]


def test_boundary_rejects_synthetic_boyce_feed():
    entry = samford_entry()
    result = assess(
        entry,
        {"events": _boyce_events()},
        claimed_slug="synthetic-boyce-liberty-winner",
        claimed_side="long",
    )
    assert not result.ok
    assert "no_unique_event" in result.reasons
    assert "cross_game" in result.reasons
    assert result.token_id is None


def test_boundary_rejects_observed_teams_without_inventing_a_token():
    boyce = json.loads(json.dumps(_boyce_events()[0]))
    boyce["slug"] = "synthetic-cbb-boyce-liberty-2026-11-07"
    boyce["markets"][0]["slug"] = "synthetic-boyce-liberty-winner-2026"
    boyce["gameId"] = "401924912"
    result = assess(
        _observed_entry(),
        {"events": [boyce]},
        claimed_slug="synthetic-boyce-liberty-winner-2026",
        claimed_side="long",
    )
    assert not result.ok
    assert "provider_id_without_participants" in result.reasons
    assert result.token_id is None
    assert result.outcome is None


def test_legitimate_aliases_and_hyphen_and_swapped_listing():
    aliases = samford_event(
        away_team=team("synthetic-samford", "Samford", "Samford Bulldogs", "SAM"),
        home_team=team("synthetic-unlv", "Nevada-Las Vegas Rebels", "UNLV Rebels"),
    )
    # Roster name uses a hyphen; the entry uses the spaced alias.
    result = assess(samford_entry(home="UNLV Rebels"), {"events": [aliases]},
                    claimed_slug="synthetic-samford-unlv-winner", claimed_side="long",
                    claimed_token="synthetic-token-samford")
    assert result.ok, result.reasons
    swapped = samford_event()
    swapped["teams"] = list(reversed(swapped["teams"]))
    result = assess(samford_entry(), {"events": [swapped]},
                    claimed_slug="synthetic-samford-unlv-winner", claimed_side="long",
                    claimed_token="synthetic-token-samford")
    assert result.ok, result.reasons


def test_abbreviation_only_and_mascot_only_do_not_match():
    event = samford_event(
        away_team={"id": "synthetic-samford", "name": "Bulldogs", "abbreviation": "SAM"},
        home_team=team("synthetic-unlv", "UNLV Rebels", "UNLV"),
    )
    result = assess(samford_entry(), {"events": [event]},
                    claimed_slug="synthetic-samford-unlv-winner", claimed_side="long")
    assert not result.ok
    mascot_entry = samford_entry(pick="Bulldogs ML", away="Bulldogs")
    result = assess(mascot_entry, {"events": [samford_event()]},
                    claimed_slug="synthetic-samford-unlv-winner", claimed_side="long")
    assert "mascot_only" in result.reasons


def test_ambiguous_events_and_duplicate_listing_collapses():
    one = samford_event()
    other = samford_event()
    other["id"] = "synthetic-samford-unlv-event-2"
    other["slug"] = "synthetic-cbb-samford-unlv-2030-11-07-b"
    result = assess(samford_entry(), {"events": [one, other]},
                    claimed_slug="synthetic-samford-unlv-winner", claimed_side="long",
                    claimed_token="synthetic-token-samford")
    assert result.reasons == ["ambiguous_event"]
    again = json.loads(json.dumps(one))
    result = assess(samford_entry(), {"events": [one, again]},
                    claimed_slug="synthetic-samford-unlv-winner", claimed_side="long",
                    claimed_token="synthetic-token-samford")
    assert result.ok, result.reasons


def test_wrong_side_sport_date_and_token():
    event = samford_event()
    entry = samford_entry()
    wrong_side = assess(entry, {"events": [event]}, claimed_slug="synthetic-samford-unlv-winner",
                        claimed_side="short", claimed_token="synthetic-token-samford")
    assert "side_disagrees" in wrong_side.reasons
    other_team = samford_entry(pick="UNLV Rebels ML")
    backed = assess(other_team, {"events": [event]}, claimed_slug="synthetic-samford-unlv-winner",
                    claimed_side="long", claimed_token="synthetic-token-unlv")
    assert "side_disagrees" in backed.reasons
    wrong_token = assess(entry, {"events": [event]}, claimed_slug="synthetic-samford-unlv-winner",
                         claimed_side="long", claimed_token="synthetic-token-unlv")
    assert "token_disagrees" in wrong_token.reasons
    nfl = samford_event(league="nfl", slug="synthetic-nfl-samford-unlv-2030-11-07")
    assert "wrong_sport" in assess(entry, {"events": [nfl]}, claimed_slug="synthetic-samford-unlv-winner",
                                   claimed_side="long").reasons
    later = samford_event(slug="synthetic-cbb-samford-unlv-2030-11-08", startTime="2030-11-08T21:00:00Z")
    assert "wrong_date" in assess(entry, {"events": [later]}, claimed_slug="synthetic-samford-unlv-winner",
                                  claimed_side="long").reasons


def test_schema_variations_and_abstentions():
    event = samford_event()
    event.pop("teams")
    event["markets"][0]["sportsMarketType"] = "moneyline"
    for side in event["markets"][0]["marketSides"]:
        side["token_id"] = side.pop("tokenId")
    result = assess(samford_entry(), event, claimed_slug="synthetic-samford-unlv-winner",
                    claimed_side="long", claimed_token="synthetic-token-samford")
    assert result.ok, result.reasons
    assert result.token_id == "synthetic-token-samford"

    omitted = samford_event(include_token=False)
    result = assess(samford_entry(), {"events": [omitted]}, claimed_slug="synthetic-samford-unlv-winner",
                    claimed_side="long", claimed_token=None)
    assert result.ok, result.reasons
    assert result.token_id is None
    assert "token_omitted_by_provider" in result.notes
    invented = assess(samford_entry(), {"events": [omitted]}, claimed_slug="synthetic-samford-unlv-winner",
                      claimed_side="long", claimed_token="synthetic-made-up")
    assert "token_not_in_provider" in invented.reasons

    disagree = samford_event()
    disagree["markets"][0]["marketSides"][0]["token_id"] = "synthetic-other-token"
    result = assess(samford_entry(), {"events": [disagree]}, claimed_slug="synthetic-samford-unlv-winner",
                    claimed_side="long", claimed_token="synthetic-token-samford")
    assert "token_disagree" in result.reasons

    titled = _boyce_events()[0]
    titled = json.loads(json.dumps(titled))
    titled["markets"][0]["question"] = "Samford Bulldogs at UNLV Rebels"
    result = assess(samford_entry(), {"events": [titled]}, claimed_slug="synthetic-boyce-liberty-winner",
                    claimed_side="long")
    assert not result.ok
    assert result.token_id is None

    broken = {"question": "Samford", "outcomes": ["Yes", "No"], "clobTokenIds": ["1", "2"]}
    result = assess(samford_entry(), broken, claimed_slug="synthetic-samford-unlv-winner", claimed_side="long")
    assert "snapshot_unparsed" in result.reasons

    naive = samford_event(startTime="2030-11-07T21:00:00")
    result = assess(samford_entry(), {"events": [naive]}, claimed_slug="synthetic-samford-unlv-winner",
                    claimed_side="long")
    assert not result.ok

    both_long = samford_event()
    both_long["markets"][0]["marketSides"][1]["long"] = True
    result = assess(samford_entry(), {"events": [both_long]}, claimed_slug="synthetic-samford-unlv-winner",
                    claimed_side="long", claimed_token="synthetic-token-samford")
    assert "side_flags_ambiguous" in result.reasons

    string_long = samford_event()
    string_long["markets"][0]["marketSides"][0]["long"] = "true"
    result = assess(samford_entry(), {"events": [string_long]}, claimed_slug="synthetic-samford-unlv-winner",
                    claimed_side="long", claimed_token="synthetic-token-samford")
    assert "side_unknown" in result.reasons


def test_ct_date_crosses_utc_midnight_and_conflicting_provider_dates():
    late = samford_event(startTime="2030-11-08T02:30:00Z", slug="synthetic-cbb-samford-unlv-2030-11-07")
    entry = samford_entry(start="2030-11-08T02:30:00Z")
    result = assess(entry, {"events": [late]}, claimed_slug="synthetic-samford-unlv-winner",
                    claimed_side="long", claimed_token="synthetic-token-samford")
    assert result.ok, result.reasons
    assert result.event_date == "2030-11-07"
    clash = samford_event(startTime="2030-11-08T21:00:00Z", slug="synthetic-cbb-samford-unlv-2030-11-07")
    result = assess(samford_entry(), {"events": [clash]}, claimed_slug="synthetic-samford-unlv-winner",
                    claimed_side="long")
    assert "wrong_date" in result.reasons or "provider_date_disagree" in result.reasons


def test_provider_id_does_not_select_or_block_a_real_participant_match():
    event = samford_event()
    event["gameId"] = "not-the-espn-id"
    entry = samford_entry(source_game_id="401924912")
    result = assess(entry, {"events": [event]}, claimed_slug="synthetic-samford-unlv-winner",
                    claimed_side="long", claimed_token="synthetic-token-samford")
    assert result.ok, result.reasons
    assert result.event_id == "synthetic-samford-unlv-event"


def test_homoglyph_does_not_match():
    event = samford_event(away_team=team("synthetic-samford", "Samf\u043erd Bulldogs", "Bulldogs"))
    result = assess(samford_entry(), {"events": [event]}, claimed_slug="synthetic-samford-unlv-winner",
                    claimed_side="long")
    assert not result.ok


def test_spread_and_total_stay_on_the_identified_event():
    event = samford_event()
    event["markets"].append({
        "slug": "synthetic-samford-unlv-spread",
        "sportsMarketType": "basketball_full_game_spread",
        "closed": False,
        "marketSides": [
            {"long": True, "description": "-2.5", "tokenId": "synthetic-token-spread",
             "team": event["teams"][0]},
            {"long": False, "description": "2.5", "team": event["teams"][1],
             "tokenId": "synthetic-token-spread-other"},
        ],
    })
    event["markets"].append({
        "slug": "synthetic-samford-unlv-total",
        "sportsMarketType": "basketball_full_game_total",
        "closed": False,
        "marketSides": [
            {"long": True, "description": "Over", "tokenId": "synthetic-token-over"},
            {"long": False, "description": "Under", "tokenId": "synthetic-token-under"},
        ],
    })
    spread = samford_entry(market="spread", pick="Samford Bulldogs -2.5", key="ncaab:synthetic:spread")
    result = assess(spread, {"events": [event]}, claimed_slug="synthetic-samford-unlv-spread",
                    claimed_side="long", claimed_token="synthetic-token-spread")
    assert result.ok, result.reasons
    total = samford_entry(market="total", pick="Over 145.5", key="ncaab:synthetic:total")
    result = assess(total, {"events": [event]}, claimed_slug="synthetic-samford-unlv-total",
                    claimed_side="long", claimed_token="synthetic-token-over")
    assert result.ok, result.reasons
    leaked = assess(spread, {"events": [event]}, claimed_slug="synthetic-boyce-liberty-winner",
                    claimed_side="long", claimed_token="synthetic-token-spread")
    assert not leaked.ok
