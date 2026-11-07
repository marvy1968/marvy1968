"""Replay, identity, cap, send, and comparison tests.

Fixtures are synthetic or the vendored schema sample. No network and no secrets.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
REPO = ROOT.parent

os.environ["LIVE_PROPS_SEND"] = "off"

from cohort import cohort_key, team_key, utc_stamp
from compare_shadow import compare
from orchestrate import orchestrate
from priors import get_prior
from prop_snapshot import (
    ReplayError, hash_payload, input_hash, load_snapshot, replay_file, replay_rows,
)
from runner import main as runner_main

SAMPLE = ROOT / "fixtures" / "prop_snapshot_v1.sample.json"


def _obs():
    return json.loads(SAMPLE.read_text())["observations"][0]


def _claude_hash(obs):
    code = subprocess.check_output(
        ["git", "-C", str(REPO), "show", "0155dbca20e5143c4d92e7ff2b47726f716e891d:marv/prop_snapshot.py"],
        text=True)
    code = code.replace("from . import live_prop_engine as E\n", "E = None\n")
    ns = {"__name__": "claude_prop_snapshot"}
    exec(code, ns)
    ns["E"] = type("E", (), {"GAME": {"nfl", "cfb", "nba", "wnba", "ncaab", "ncaaw"}})()
    payload = ns["hash_payload"](obs, "observation")
    return ns["input_hash"](payload), ns["cohort_key"](
        payload["sport"], payload["away"], payload["home"], payload["scheduled_start"])


def test_input_hash_matches_claude_schema():
    obs = _obs()
    ours = input_hash(hash_payload(obs, "observation"))
    theirs, their_key = _claude_hash(obs)
    assert ours == theirs
    payload = hash_payload(obs, "observation")
    assert cohort_key(payload["sport"], payload["away"], payload["home"], payload["scheduled_start"]) == their_key
    # 30 and 30.0 are the same hash. Aliases are not hashed.
    changed = json.loads(json.dumps(obs))
    changed["elapsed_minutes"] = 30.0
    changed["aliases"] = {"espn_event_id": "different"}
    assert input_hash(hash_payload(changed, "observation")) == ours


def test_cohort_is_teams_sport_and_start_not_provider_id():
    start = utc_stamp("2026-11-08T13:00:00-05:00", "start")
    assert start == "2026-11-08T18:00:00Z"
    one = cohort_key("nfl", "New York Giants", "Dallas Cowboys", start)
    spaced = cohort_key("NFL", "new york   giants", "dallas cowboys", start)
    assert one == spaced
    assert team_key("Dallas Cowboys") == team_key("dallas  cowboys")
    later = cohort_key("nfl", "New York Giants", "Dallas Cowboys", "2026-11-08T21:00:00Z")
    assert later != one
    swapped = cohort_key("nfl", "Dallas Cowboys", "New York Giants", start)
    assert swapped != one
    # Display names that do not normalize together still need an alias. They do not share a key.
    assert team_key("LA Clippers") != team_key("Los Angeles Clippers")
    assert team_key("St. John's") != team_key("Saint Johns")


def test_bare_pregame_kind_writes_nothing():
    obs = _obs()
    obs["anchors"] = [{"player": "CeeDee Lamb", "market": "player_reception_yds", "value": 70, "kind": "pregame"}]
    doc = {"schema": "marv.prop_snapshot.v1", "observations": [obs]}
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "snap.json"
        path.write_text(json.dumps(doc))
        try:
            replay_file(path, Path(td) / "state")
        except ReplayError as exc:
            assert "pregame" in str(exc)
        else:
            raise AssertionError("bare pregame kind must be refused")
        assert not (Path(td) / "state" / "live_props" / "shadow.jsonl").exists()


def test_sample_replay_labels_anchor_and_does_not_invent_pregame():
    with tempfile.TemporaryDirectory() as td:
        n_obs, n_priced, n_abs, dest = replay_file(SAMPLE, Path(td))
        assert n_obs == 1 and n_priced == 1 and n_abs == 1
        rows = [json.loads(line) for line in dest.read_text().splitlines()]
        priced = [r for r in rows if not r["abstained"]][0]
        missing = [r for r in rows if r["abstained"]][0]
        assert priced["schema"] == "marv.prop_shadow.v1"
        assert priced["model_version"] == "muse-prop1"
        assert priced["anchor_kind"] == "model_projection"
        assert priced["quoted_before_start"] is False
        assert priced["probability_status"] == "uncalibrated"
        assert priced["empirical_calibration"] is False
        assert priced["elapsed_minutes"] == 30
        assert priced["cohort_key"].startswith("nfl|new york giants|dallas cowboys|")
        assert priced["game_id"] == priced["cohort_key"]
        assert priced["aliases"]["espn_event_id"] == "401772969"
        assert "p" in priced and "edge" in priced and "price" in priced
        assert missing["abstain_reason"] == "player_not_in_box"
        assert missing["player"] == "Missing Player"
        # Replacing the file is the contract. A second run does not append.
        dest.write_text("SENTINEL\n")
        replay_file(SAMPLE, Path(td))
        assert "SENTINEL" not in dest.read_text()
        assert len(dest.read_text().splitlines()) == 2


def test_inferred_anchor_is_not_called_pregame_and_missing_anchor_abstains():
    obs = _obs()
    obs["anchors"] = [{"player": "CeeDee Lamb", "market": "player_reception_yds", "value": 70, "kind": "inferred"}]
    rows = replay_rows({"schema": "marv.prop_snapshot.v1", "observations": [obs]})
    priced = [r for r in rows if r["player"] == "CeeDee Lamb" and not r["abstained"]][0]
    assert priced["anchor_kind"] == "inferred"
    assert priced["quoted_before_start"] is False
    assert priced["mean_source"] == "inferred"
    obs["anchors"] = []
    rows = replay_rows({"schema": "marv.prop_snapshot.v1", "observations": [obs]})
    lamb = [r for r in rows if r["player"] == "CeeDee Lamb"][0]
    assert lamb["abstained"] is True and lamb["abstain_reason"] == "no_anchor"
    assert lamb["anchor_kind"] is None


def test_missing_pace_abstains_instead_of_inventing_plays():
    obs = _obs()
    obs["boxscore"]["teams"] = []
    rows = replay_rows({"schema": "marv.prop_snapshot.v1", "observations": [obs]})
    lamb = [r for r in rows if r["player"] == "CeeDee Lamb"][0]
    assert lamb["abstained"] is True and lamb["abstain_reason"] == "missing_real_pace"
    assert "p" not in lamb


def test_player_rates_come_from_that_players_anchor():
    low = get_prior("nfl", "A", "player_reception_yds", 40.0, {}, opportunities=63)
    high = get_prior("nfl", "B", "player_reception_yds", 140.0, {}, opportunities=63)
    assert low["rate"] != high["rate"]
    assert abs(low["rate"] - 3.8) > 0.5
    assert abs(high["rate"] - (140 / 63)) < 1e-9
    assert get_prior("nfl", "A", "player_reception_yds", None, {}, opportunities=63)["rate"] is None
    named = get_prior("nfl", "A", "player_reception_yds", 40.0,
                      {"A": {"player_reception_yds": {"per_play": 1.25}}}, opportunities=63)
    assert named["rate"] == 1.25 and named["rate_source"] == "player_prior_file"


def test_recommendation_cap_is_three_and_durable():
    obs = _obs()
    # Four markets, all far over a short line, with real plays and quoted anchors.
    labels = {
        "passing": (["YDS", "TD"], ["400", "3"], "player_pass_yds"),
    }
    obs["boxscore"]["players"][0]["statistics"] = [
        {"name": "passing", "labels": ["C/ATT", "YDS", "TD"], "athletes": [
            {"athlete": {"displayName": "CeeDee Lamb"}, "stats": ["20/30", "400", "3"]}]},
        {"name": "rushing", "labels": ["CAR", "YDS", "TD"], "athletes": [
            {"athlete": {"displayName": "CeeDee Lamb"}, "stats": ["10", "80", "1"]}]},
        {"name": "receiving", "labels": ["REC", "YDS", "TD", "TGTS"], "athletes": [
            {"athlete": {"displayName": "CeeDee Lamb"}, "stats": ["8", "120", "1", "10"]}]},
    ]
    markets = ["player_pass_yds", "player_rush_yds", "player_reception_yds", "player_receptions"]
    obs["anchors"] = [{"player": "CeeDee Lamb", "market": m, "value": 10.0, "kind": "quoted_pregame"} for m in markets]
    obs["quoted_odds"] = {"bookmakers": [{"key": "bovada", "markets": [
        {"key": m, "last_update": obs["observed_at"], "outcomes": [
            {"name": "Over", "description": "CeeDee Lamb", "point": 5.5, "price": -110},
            {"name": "Under", "description": "CeeDee Lamb", "point": 5.5, "price": -110},
        ]} for m in markets
    ]}]}
    rows = replay_rows({"schema": "marv.prop_snapshot.v1", "observations": [obs]}, cap=3)
    recommended = [r for r in rows if r.get("recommended")]
    priced = [r for r in rows if not r["abstained"]]
    assert len(priced) == 4, [(r["market"], r.get("abstain_reason"), r.get("reason")) for r in rows]
    assert len(recommended) == 3
    blocked = [r for r in priced if not r["recommended"]]
    assert blocked[0]["reason"] == "max_picks_per_game"
    assert all(r["quoted_before_start"] is True for r in priced)
    # A later observation of the same game does not add a fourth recommendation.
    later = json.loads(json.dumps(obs))
    later["observation_id"] = "later"
    later["observed_at"] = "2026-11-08T20:00:00Z"
    from prop_snapshot import recommendation_id
    already = {recommendation_id(r) for r in recommended}
    again = replay_rows({"schema": "marv.prop_snapshot.v1", "observations": [later]}, already=already, cap=3)
    assert not any(r.get("recommended") for r in again)


def test_send_off_is_required_and_does_not_touch_the_network():
    import requests
    called = {"n": 0}
    real = requests.get
    def boom(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("network")
    requests.get = boom
    requests.post = boom
    try:
        old = os.environ.pop("LIVE_PROPS_SEND")
        with tempfile.TemporaryDirectory() as td:
            rc = runner_main(["--snapshot", str(SAMPLE), "--state-dir", td])
            assert rc == 2
            assert not (Path(td) / "live_props" / "shadow.jsonl").exists()
        os.environ["LIVE_PROPS_SEND"] = "yes"
        with tempfile.TemporaryDirectory() as td:
            assert runner_main(["--snapshot", str(SAMPLE), "--state-dir", td]) == 2
        os.environ["LIVE_PROPS_SEND"] = "off"
        with tempfile.TemporaryDirectory() as td:
            assert runner_main(["--dry-run", "--state-dir", td, "--mode", "log_only"]) == 0
            assert (Path(td) / "live_props" / "shadow.jsonl").exists()
        from notifier import send_telegram
        assert send_telegram("nope") is False
        assert called["n"] == 0
    finally:
        requests.get = real
        requests.post = real
        os.environ["LIVE_PROPS_SEND"] = old if "old" in dir() else "off"


def test_compare_joins_common_inputs_and_keeps_forecasts_apart():
    with tempfile.TemporaryDirectory() as td:
        dest = replay_file(SAMPLE, Path(td))[3]
        muse = [json.loads(line) for line in dest.read_text().splitlines()]
    claude = []
    for row in muse:
        copy = dict(row)
        copy["model"] = "claude-prop1"
        copy["model_version"] = "claude-prop1"
        copy["p"] = 0.61
        copy["recommended"] = bool(row.get("recommended"))
        claude.append(copy)
    # A second Claude row with another hash is coverage, not a common input.
    extra = dict(claude[0])
    extra["input_hash"] = "sha256:" + "ab" * 32
    extra["observation_id"] = "other-capture"
    claude.append(extra)
    settlement = {"results": [{
        "cohort_key": muse[0]["cohort_key"],
        "player": "CeeDee Lamb",
        "market": "player_reception_yds",
        "status": "final",
        "final": 80,
    }]}
    # Force both recommended so the picked cohort is graded. The sample may or may not recommend.
    for row in muse + claude:
        if row.get("player") == "CeeDee Lamb" and not row.get("abstained") and row["input_hash"] == muse[0]["input_hash"]:
            row["recommended"] = True
            row["side"] = "Over"
            row["price"] = -110
    report = compare(muse, claude, settlement, unit=1.0)
    assert report["comparable"] is True
    assert report["coverage"]["common_input_hashes"] == 1
    assert report["coverage"]["claude_only_input_hashes"] == 1
    assert report["coverage"]["common_forecast_props"] == 1
    assert report["coverage"]["common_recommended_props"] == 1
    assert report["significance_test"] is None
    assert report["independent_observations"] is False
    picked = report["cohorts"]["common_input_recommended"]["muse"]
    assert picked["wins"] == 1
    assert picked["paper_roi"]["real_fills"] is False
    assert picked["paper_roi"]["hypothetical_fixed_unit_stake"] == 1.0
    # No settlement match must stay pending rather than a fabricated loss.
    bare = compare(muse, claude, None, unit=1.0)
    assert bare["cohorts"]["common_input_recommended"]["muse"]["pending"] == 1


def test_archive_keeps_history_when_shadow_is_replaced():
    obs = _obs()
    second = json.loads(json.dumps(obs))
    second["observation_id"] = "other-game"
    second["away"] = "Miami Dolphins"
    second["home"] = "Buffalo Bills"
    second["scheduled_start"] = "2026-11-09T18:00:00Z"
    second["aliases"] = {"espn_event_id": "999", "odds_api_event_id": "ev-2"}
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        first = td / "first.json"
        both = td / "both.json"
        first.write_text(json.dumps({"schema": "marv.prop_snapshot.v1", "observations": [obs]}))
        both.write_text(json.dumps({"schema": "marv.prop_snapshot.v1", "observations": [obs, second]}))
        claude_state = td / "claude-state" / "live_props"
        claude_state.mkdir(parents=True)
        # Claude's file is replaced. The archive must keep the first hash.
        manifest1 = orchestrate(first, td / "archive", muse_state=td / "muse-state")
        assert len(manifest1["hashes"]) == 1
        kept = Path(manifest1["hashes"][0]["muse_shadow"])
        first_text = kept.read_text()
        orchestrate(both, td / "archive", muse_state=td / "muse-state")
        assert kept.read_text() == first_text
        assert len(list((td / "archive" / "by-hash").iterdir())) == 2
        # Replacing the live state file does not remove the archive.
        (td / "muse-state" / "live_props" / "shadow.jsonl").write_text("")
        assert kept.read_text() == first_text


def test_alias_disagreement_is_coverage_not_a_common_input():
    with tempfile.TemporaryDirectory() as td:
        dest = replay_file(SAMPLE, Path(td))[3]
        muse = [json.loads(line) for line in dest.read_text().splitlines() if line.strip()]
    claude = []
    for row in muse:
        copy = dict(row)
        copy["model_version"] = "claude-prop1"
        copy["cohort_key"] = "nfl|other name|still other|2026-11-08T18:00:00Z"
        copy["game_id"] = copy["cohort_key"]
        copy["input_hash"] = "sha256:" + "cd" * 32
        claude.append(copy)
    report = compare(muse, claude, None)
    assert report["comparable"] is False
    assert report["coverage"]["alias_join_without_same_cohort"]
    assert report["coverage"]["common_input_hashes"] == 0


def test_refuses_the_old_aj_shadow_path():
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "muse_nfl_shadow"
        bad.mkdir()
        try:
            replay_file(SAMPLE, bad)
        except ReplayError as exc:
            assert "AJ" in str(exc) or "muse_nfl_shadow" in str(exc)
        else:
            raise AssertionError("AJ shadow path must be refused")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print(f"PASS {name}")
    print("all replay tests passed")
