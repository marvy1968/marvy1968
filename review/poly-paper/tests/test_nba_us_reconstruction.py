"""Observed reconstruction of three nba_prop_live fills.

The Polymarket US bodies are the current public /v1/events response, not the
payload stored at fill time. ESPN dates are scheduled starts, not tipoff.
Prices, stakes, and fees are the recorded paper rows. No token, player id, or
home/away order is added.
"""
import json
from pathlib import Path

from poly_paper.fee import contracts_from_dollars, cost as cost_of, fee as fee_of
from poly_paper.integrated import writer_session

ROOT = Path(__file__).resolve().parents[1]
PAIRS = json.loads((ROOT / "provenance" / "us-public-20261108" / "reconstructed-pairs.json").read_text())


def _us_event(pair, reverse=False):
    src = pair["independent_provider"]["event"]
    market = src["market"]
    teams = [
        {"id": team["id"], "name": team["name"], "abbreviation": team["abbreviation"], "league": team["league"]}
        for team in src["participants"]
    ]
    if reverse:
        teams = list(reversed(teams))
    sides = []
    for side in market["marketSides"]:
        sides.append({
            "id": side["id"],
            "marketSideType": side["marketSideType"],
            "identifier": side["identifier"],
            "description": side["description"],
            "long": side["long"],
            "tradable": side["tradable"],
        })
        assert "tokenId" not in side and "token_id" not in side and "clobTokenId" not in side
    assert market["subject"] is None
    assert market["token_ids"] is None
    assert "ordering" not in teams[0] and "ordering" not in teams[1]
    return {
        "id": src["id"],
        "slug": src["slug"],
        "title": src["title"],
        "startTime": src["scheduled_or_listed_start_CT"],
        "gameStartTime": market["gameStartTime_CT"],
        "teams": teams,
        "markets": [{
            "id": market["id"],
            "slug": market["slug"],
            "question": market["question"],
            "sportsMarketType": market["sportsMarketType"],
            "line": market["line"],
            "status": market["status"],
            "subject": None,
            "marketSides": sides,
        }],
    }


def _arm(session, pairs, reverse=False):
    seen = set()
    for pair in pairs:
        event = _us_event(pair, reverse=reverse)
        if event["id"] not in seen:
            session.gate.live.record_pm_event(event)
            seen.add(event["id"])
        source = pair["independent_source"]
        competitors = [{
            "homeAway": "home" if team["home"] else "away",
            "team": {"displayName": team["name"], "abbreviation": team["abbreviation"]},
        } for team in source["participants"]]
        session.gate.live.note_http("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard", {
            "events": [{
                "id": source["canonical_event_id"],
                "date": source["scheduled_start_CT"],
                "competitions": [{"competitors": competitors}],
            }],
        })
        by_team = {}
        for player in source["players"]:
            by_team.setdefault(player["team_name"], []).append(player["name"])
        session.gate.live.note_http(
            f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={source['canonical_event_id']}",
            {"boxscore": {"players": [{
                "team": {"displayName": team_name},
                "statistics": [{"athletes": [{"athlete": {"displayName": name}} for name in names]}],
            } for team_name, names in by_team.items()]}},
        )


def _fill(session, pair, side=None, slug=None, group_prefix=None):
    observed = pair["observed_fill"]
    parts = observed["key"].split(":")
    prefix = group_prefix if group_prefix is not None else ":".join(parts[:3]) + ":"
    rec = {
        "key": observed["key"],
        "pick": observed["pick"],
        "slug": slug or observed["slug"],
        "side": side or observed["side"],
        "price": observed["price"],
        "fair": 0.55,
        "sport": "nba",
        "market": "prop",
    }
    return session.pm_fill.fill(
        "NBA_LIVE", rec, "nba_prop_live", group_prefix=prefix,
        gate={"cap": 0.60, "p": 0.55, "min_edge": 0.03},
    )


def test_three_reconstructions_match_recorded_economics(tmp_path):
    assert {row["comparison"]["scheduled_start_delta_seconds"] for row in PAIRS} == {0.0}
    assert all(row["independent_source"]["actual_start"] is None for row in PAIRS)
    assert all(row["comparison"]["assessment"].startswith("MATCH_CURRENT_RECONSTRUCTION") for row in PAIRS)
    for pair in PAIRS:
        observed = pair["observed_fill"]
        expected_contracts = contracts_from_dollars(pair["tracker_stake"], observed["price"])
        assert expected_contracts == int(observed["contracts"])
        assert fee_of(expected_contracts, observed["price"]) == observed["fee"]
        assert cost_of(expected_contracts, observed["price"], observed["fee"]) == observed["cost"]
        for reverse in (False, True):
            with writer_session(tmp_path / f"{pair['synthetic_row_id']}-{reverse}", identity_gate=True) as session:
                _arm(session, [pair], reverse=reverse)
                ok, why = _fill(session, pair)
                assert ok and why == "filled", why
                row = session.fills()[0]
                assert row["lane"] == "nba_prop_live"
                assert row["price"] == observed["price"]
                assert row["contracts"] == expected_contracts
                assert row["fee"] == observed["fee"]
                assert row["cost"] == observed["cost"]
                audit = session.audits()[-1]
                assert audit["action"] == "accept"
                assert audit["token_verified"] == 0
                assert audit["mapped_event_id"] == pair["independent_provider"]["event"]["id"]
                assert audit["mapped_event_id"] != pair["independent_source"]["canonical_event_id"]
                reasons = audit["reasons_json"]
                assert "token_omitted_by_provider" in reasons
                assert "token_not_verified" in reasons
                assert "scheduled_start_not_tipoff" in reasons
                assert "provider_home_away_not_in_payload" in reasons
                assert "provider_player_id_absent" in reasons
                outcome = json.loads(audit["outcome_json"])
                assert outcome["token_id"] is None
                assert outcome["token_verified"] is False
                for side in pair["independent_provider"]["event"]["market"]["marketSides"]:
                    assert outcome["token_id"] != side["id"]
                participants = json.loads(audit["participants_json"])
                names = {item["entry"] for item in participants}
                assert names == {team["name"] for team in pair["independent_source"]["participants"]}
                home = next(team["name"] for team in pair["independent_source"]["participants"] if team["home"])
                assert next(item["entry"] for item in participants if item["role"] == "home") == home


def test_writer_keeps_dedupe_and_live_prop_cap(tmp_path):
    pair = PAIRS[0]
    with writer_session(tmp_path / "dedupe", identity_gate=True) as session:
        _arm(session, PAIRS)
        assert _fill(session, pair)[1] == "filled"
        ok, why = _fill(session, pair)
        assert ok is False and why == "already hold this market"
        other = dict(pair)
        ok, why = _fill(session, other, slug="synthetic-second-slug-same-player")
        assert ok is False and why == "one prop per player per game"
        assert len(session.fills()) == 1
    with writer_session(tmp_path / "cap", identity_gate=True) as session:
        session.runner.CFG["max_daily_live_props_usd"] = 10
        _arm(session, [pair])
        ok, why = _fill(session, pair)
        assert ok is False and why == "live props daily paper budget reached"
        assert session.fills() == []


def test_wrong_roster_side_stat_cross_game_and_two_markets_abstain(tmp_path):
    jokic, siakam, mccollum = PAIRS
    with writer_session(tmp_path / "roster", identity_gate=True) as session:
        _arm(session, [jokic])
        session.gate.live.espn["401909938"]["players"] = [
            {"name": "Nikola Jokic", "team": "Los Angeles Lakers"}
        ]
        ok, why = _fill(session, jokic)
        assert ok is False and why == "identity:identity_context_missing"
        assert session.fills() == []
    with writer_session(tmp_path / "side", identity_gate=True) as session:
        event = _us_event(jokic)
        event["markets"][0]["marketSides"][0]["long"] = False
        event["markets"][0]["marketSides"][1]["long"] = True
        _arm(session, [jokic])
        session.gate.live.pm_events = [event]
        ok, why = _fill(session, jokic)
        assert ok is False and why == "identity:ambiguous_outcome"
        assert session.fills() == []
    with writer_session(tmp_path / "stat", identity_gate=True) as session:
        event = _us_event(jokic)
        event["markets"][0]["sportsMarketType"] = "basketball_player_assists"
        _arm(session, [jokic])
        session.gate.live.pm_events = [event]
        ok, why = _fill(session, jokic)
        assert ok is False and why == "identity:identity_unknown"
        assert session.fills() == []
    with writer_session(tmp_path / "cross", identity_gate=True) as session:
        event = _us_event(mccollum)
        event["markets"][0]["slug"] = jokic["observed_fill"]["slug"]
        event["markets"][0]["question"] = jokic["independent_provider"]["event"]["market"]["question"]
        event["markets"][0]["sportsMarketType"] = "basketball_player_steals"
        event["markets"][0]["line"] = 3
        _arm(session, [jokic])
        session.gate.live.pm_events = [event]
        ok, why = _fill(session, jokic)
        assert ok is False and why.startswith("identity:")
        assert "cross_game" in session.audits()[-1]["reasons_json"]
        assert session.fills() == []
    with writer_session(tmp_path / "ambiguous", identity_gate=True) as session:
        event = _us_event(jokic)
        extra = dict(event["markets"][0])
        extra["slug"] = "synthetic-second-jokic-steals"
        extra["id"] = "synthetic-market"
        event["markets"] = [event["markets"][0], extra]
        _arm(session, [jokic])
        session.gate.live.pm_events = [event]
        ok, why = _fill(session, jokic)
        assert ok is False and why == "identity:ambiguous_outcome"
        assert session.fills() == []
    assert siakam["observed_fill"]["side"] == "short"
