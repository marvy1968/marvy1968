"""Synthetic US-schema live identity, plus the earlier Gamma 404 supplement.

Gamma slug lookups returned 404 and are not a wrong game. The later Polymarket
US /v1/events reconstruction is a separate archive and is tested there.
"""
import json
from datetime import datetime, timedelta, timezone

from poly_paper.identity import START_TOLERANCE_SECONDS
from poly_paper.integrated import arm_http, install_client, writer_session
from poly_paper.live_identity import LiveEvidence

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "provenance" / "espn-public-20261108"
START = "2030-11-07T21:00:00Z"


def _assessments():
    return json.loads((EVIDENCE / "example-assessments.json").read_text())


def _espn_games():
    return json.loads((EVIDENCE / "public-source-events.json").read_text())


def test_observed_claims_are_not_validated_and_404_is_not_a_mismatch():
    rows = _assessments()
    assert rows and all(row["valid_positive_parity_fixture"] is False for row in rows)
    assert {row["mapped_provider_lookup"]["http_status"] for row in rows} == {"404"}
    assert all(row["mapped_provider_event"] is None for row in rows)
    assert all(row["comparison"].startswith("UNVERIFIABLE_PROVIDER_SIDE") for row in rows)
    games = {game["canonical_event_id"]: game for game in _espn_games()}
    assert games["401909938"]["participants"][0]["name"] == "Denver Nuggets"
    assert games["401909938"]["actual_start"] is None
    assert games["401909938"]["start_semantics"].startswith("Published")
    evidence = LiveEvidence()
    built = evidence.build(
        key=rows[0]["observed_runtime_claim"]["key"],
        pick=rows[0]["observed_runtime_claim"]["pick"],
        side=rows[0]["observed_runtime_claim"]["side"],
        slug=rows[0]["observed_runtime_claim"]["market_slug"],
        lane_switch="NBA_LIVE",
    )
    assert built is None
    assert START_TOLERANCE_SECONDS == 180


def test_synthetic_us_schema_accepts_matching_scheduled_starts_only():
    """SYNTHETIC_US_SCHEMA_NOT_OBSERVED_MARKET. Not an observed November 7 fill."""
    evidence = LiveEvidence()
    evidence.note_http("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard", {
        "events": [{
            "id": "synthetic-espn-1",
            "date": START,
            "competitions": [{"competitors": [
                {"homeAway": "away", "team": {"displayName": "Synthetic Away Club", "abbreviation": "SAW"}},
                {"homeAway": "home", "team": {"displayName": "Synthetic Home Club", "abbreviation": "SHC"}},
            ]}],
        }],
    })
    evidence.note_http("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=synthetic-espn-1", {
        "boxscore": {"players": [{
            "team": {"displayName": "Synthetic Home Club", "abbreviation": "SHC"},
            "statistics": [{"athletes": [{"athlete": {"displayName": "Synthetic Player"}}]}],
        }]},
    })
    market = {
        "slug": "synthetic-us-prop-pts",
        "sportsMarketType": "basketball_player_points",
        "question": "Will Synthetic Player record at least 8 points",
        "line": 8,
        "closed": False,
        "marketSides": [
            {"long": True, "description": "Over"},
            {"long": False, "description": "Under"},
        ],
    }
    evidence.record_pm_event({
        "id": "synthetic-us-event",
        "slug": "synthetic-us-event-not-a-gamma-slug",
        "league": "nba",
        "startTime": "2030-11-07T21:00:30Z",
        "closed": False,
        "teams": [
            {"name": "Synthetic Away Club"},
            {"name": "Synthetic Home Club"},
        ],
        "markets": [market],
    })
    built = evidence.build(
        key="nbalive:synthetic-espn-1:synthetic player:PTS:8:long",
        pick="Synthetic Player 8+ PTS",
        side="long",
        slug="synthetic-us-prop-pts",
        lane_switch="NBA_LIVE",
    )
    assert built is not None
    assert built["entry"]["home"] == "Synthetic Home Club"
    assert built["entry"]["away"] == "Synthetic Away Club"
    assert built["entry"]["prop"]["player"] == "Synthetic Player"
    assert built["entry"]["prop"]["stat"] == "PTS"


def test_abbreviations_wrong_sport_masquerade_and_window_do_not_pass():
    evidence = LiveEvidence()
    evidence.note_http("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard", {
        "events": [{
            "id": "synthetic-espn-1",
            "date": START,
            "competitions": [{"competitors": [
                {"homeAway": "away", "team": {"abbreviation": "SAW"}},
                {"homeAway": "home", "team": {"abbreviation": "SHC"}},
            ]}],
        }],
    })
    assert evidence.build(
        key="nbalive:synthetic-espn-1:synthetic player:PTS:8:long",
        pick="Synthetic Player 8+ PTS", side="long", slug="synthetic-us-prop-pts", lane_switch="NBA_LIVE",
    ) is None


def _synthetic_runtime():
    today = datetime.now(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d")
    # The unchanged caller puts its UTC-4 date in the slug. Identity also reads
    # that slug date, so the scheduled start has to be the same calendar day.
    scheduled = f"{today}T21:00:00Z"
    scoreboard = {"events": [{
        "id": "synthetic-espn-1",
        "date": scheduled,
        "shortName": "SAW@SHC",
        "status": {"type": {"state": "in", "shortDetail": "1st"}, "period": 1, "displayClock": "10:00"},
        "competitions": [{"competitors": [
            {"homeAway": "away", "score": "0", "team": {"displayName": "Synthetic Away Club", "abbreviation": "SAW"}},
            {"homeAway": "home", "score": "2", "team": {"displayName": "Synthetic Home Club", "abbreviation": "SHC"}},
        ]}],
    }]}
    summary = {"boxscore": {"players": [{
        "team": {"displayName": "Synthetic Home Club", "abbreviation": "SHC"},
        "statistics": [{
            "names": ["MIN", "PTS", "REB", "AST", "STL", "BLK", "3PT", "PF"],
            "athletes": [{
                "athlete": {"id": "p1", "displayName": "Synthetic Player"},
                "starter": True, "active": True,
                "stats": ["2", "2", "1", "0", "0", "0", "0-1", "0"],
            }],
        }],
    }]}}
    event = {
        "id": "synthetic-us-event",
        "slug": f"aec-nba-saw-shc-{today}",
        "league": "nba",
        "startTime": scheduled.replace("T21:00:00Z", "T21:00:30Z"),
        "closed": False,
        "teams": [{"name": "Synthetic Away Club"}, {"name": "Synthetic Home Club"}],
        "markets": [{
            "slug": "synthetic-us-prop-pts",
            "sportsMarketType": "basketball_player_points",
            "question": "Will Synthetic Player record at least 8 points",
            "line": 8,
            "closed": False,
            "bestBidQuote": 0.43,
            "bestAskQuote": 0.45,
            "marketSides": [
                {"long": True, "description": "Over"},
                {"long": False, "description": "Under"},
            ],
        }],
    }
    bbo = {
        "state": "MARKET_STATE_OPEN",
        "bestBid": {"value": "0.43"},
        "bestAsk": {"value": "0.45"},
        "askShares": 500,
        "bidShares": 500,
        "lastPriceSample": {"ts": datetime.now(timezone.utc).isoformat()},
    }
    return scoreboard, summary, event, bbo


def _run_nba(session, scoreboard, summary, event, bbo):
    from poly_paper.integrated import _load
    install_client(session, [event], bbo)
    nba = _load(session.workspace / "pm_nba_live.py", "pm_nba_live")

    def get(url):
        if str(url).endswith("/scoreboard"):
            return scoreboard
        if "summary?event=" in str(url):
            return summary
        raise AssertionError(url)

    nba.get = get
    arm_http(session, nba)
    nba.rates = lambda aid, cache: {
        "d": "2030-11-07", "min": 34, "PTS": 0.9, "REB": 0.3, "AST": 0.2,
        "STL": 0.05, "BLK": 0.05, "3PM": 0.1, "PRA": 1.4,
    }
    nba.main()
    return nba


def test_synthetic_nba_live_fill_matches_ungated_writer_economics(tmp_path):
    scoreboard, summary, event, bbo = _synthetic_runtime()
    out = {}
    for gate, name in ((False, "writer"), (True, "gated")):
        with writer_session(tmp_path / name, identity_gate=gate) as session:
            _run_nba(session, scoreboard, summary, event, bbo)
            out[name] = session.fills()
            if gate:
                audit = session.audits()[-1]
                assert audit["action"] == "accept"
                assert audit["token_verified"] == 0
                assert "Synthetic Home Club" in audit["participants_json"]
                assert json.loads(audit["outcome_json"])["token_verified"] is False
    assert len(out["gated"]) == 1
    left, right = out["writer"][0], out["gated"][0]
    for field in ("price", "contracts", "fee", "cost", "slug", "side"):
        assert left[field] == right[field]
    assert right["lane"] == "nba_prop_live"
    assert right["lane"] != "nba_prop_live_pacer"


def test_malformed_policy_still_blocks_a_complete_synthetic_identity(tmp_path):
    scoreboard, summary, event, bbo = _synthetic_runtime()
    with writer_session(tmp_path / "bad", identity_gate=True) as session:
        (session.workspace / "type_gate.json").write_text("{")
        _run_nba(session, scoreboard, summary, event, bbo)
        assert session.fills() == []
        assert "policy_malformed" in session.audits()[-1]["reasons_json"]
