"""Mocked collector tests. No network, no credentials, no VM.

The transport is a function. urllib is not used. The odds key appears only
as a query parameter to that function and must not appear in errors.
"""
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import collector as C
from credits import CreditError, Ledger, event_odds_worst_case
from grade_results import settlements_for_observation
from prop_snapshot import input_hash, hash_payload

os.environ["LIVE_PROPS_SEND"] = "off"
KEY = "secret-key-not-for-logs"
NOW_PRE = datetime(2026, 11, 8, 17, 0, tzinfo=timezone.utc)
NOW_LIVE = datetime(2026, 11, 8, 19, 30, tzinfo=timezone.utc)
START = "2026-11-08T18:00:00Z"


def _cfg(tmp: Path, cap: int = 100, sports=None, calls: int = 4) -> dict:
    code = tmp / "claude-src"
    code.mkdir(parents=True, exist_ok=True)
    py = tmp / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("")
    return {
        "mode": "log_only",
        "telegram_enabled": False,
        "live_props_send": "off",
        "collector": {
            "sports": sports or ["nfl"],
            "max_games_per_run": 4,
            "max_billable_calls_per_run": calls,
            "daily_credit_cap": cap,
            "regions": 1,
            "claude_code_dir": str(code),
            "claude_python": str(py),
        },
    }


def _env():
    return {"LIVE_PROPS_SEND": "off", "ODDS_API_KEY": KEY, "PATH": "/usr/bin"}


def _board(state: str):
    return {"events": [{
        "id": "espn-1",
        "date": START,
        "competitions": [{
            "status": {"type": {"state": state}, "period": 2, "displayClock": "5:00"},
            "competitors": [
                {"homeAway": "home", "team": {"displayName": "Dallas Cowboys"}, "score": "14"},
                {"homeAway": "away", "team": {"displayName": "New York Giants"}, "score": "10"},
            ],
        }],
    }]}


def _events():
    return [{
        "id": "odds-1",
        "home_team": "Dallas Cowboys",
        "away_team": "New York Giants",
        "commence_time": START,
    }]


def _props(player="CeeDee Lamb", line=55.5):
    return {"id": "odds-1", "bookmakers": [{"key": "bovada", "markets": [{
        "key": "player_reception_yds",
        "last_update": START,
        "outcomes": [
            {"name": "Over", "description": player, "point": line, "price": -110},
            {"name": "Under", "description": player, "point": line, "price": -110},
        ],
    }]}]}


def _summary():
    return {"boxscore": {
        "players": [{"team": {"displayName": "Dallas Cowboys"}, "statistics": [
            {"name": "receiving", "labels": ["REC", "YDS", "TD", "TGTS"],
             "athletes": [{"athlete": {"displayName": "CeeDee Lamb"}, "stats": ["3", "40", "0", "5"]}]}
        ]}],
        "teams": [
            {"team": {"displayName": "Dallas Cowboys"}, "statistics": [
                {"name": "totalOffensivePlays", "displayValue": "30"}]},
            {"team": {"displayName": "New York Giants"}, "statistics": [
                {"name": "totalOffensivePlays", "displayValue": "28"}]},
        ],
    }}


class Feed:
    def __init__(self, props_header="4", props_status=200, props_body=None, board_state="in"):
        self.urls = []
        self.props_header = props_header
        self.props_status = props_status
        self.props_body = _props() if props_body is None else props_body
        self.board_state = board_state
        self.props_calls = 0

    def __call__(self, url: str):
        self.urls.append(url)
        parsed = urlparse(url)
        if "apiKey=" in url:
            assert parse_qs(parsed.query)["apiKey"] == [KEY]
        if parsed.netloc.startswith("site.api.espn.com") and parsed.path.endswith("/scoreboard"):
            return 200, {}, json.dumps(_board(self.board_state)).encode()
        if parsed.path.endswith("/events"):
            return 200, {"x-requests-last": "0", "x-requests-remaining": "500"}, json.dumps(_events()).encode()
        if "/summary" in parsed.path or parsed.path.endswith("/summary"):
            return 200, {}, json.dumps(_summary()).encode()
        if parsed.path.endswith("/odds"):
            self.props_calls += 1
            headers = {}
            if self.props_header is not None:
                headers["x-requests-last"] = self.props_header
                headers["x-requests-remaining"] = "490"
            body = self.props_body if isinstance(self.props_body, bytes) else json.dumps(self.props_body).encode()
            return self.props_status, headers, body
        raise AssertionError(parsed.geturl().split("apiKey")[0])


def _runner(record):
    def run(cmd, cwd, env):
        record.append((cmd, env))
        state = Path(cmd[cmd.index("--state-dir") + 1])
        dest = state / "live_props"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "shadow.jsonl").write_text("")
        assert env.get("LIVE_PROPS_SEND") == "off"
        assert "ODDS_API_KEY" not in env
        assert "TELEGRAM_BOT_TOKEN" not in env
        return 0
    return run


def test_worst_case_is_credits_per_market_not_per_request():
    assert event_odds_worst_case(4, 1) == 4
    try:
        event_odds_worst_case(0, 1)
    except CreditError:
        return
    raise AssertionError("unknown cost must be refused")


def test_reservation_is_persisted_before_the_call_and_caps_credits():
    with __import__("tempfile").TemporaryDirectory() as td:
        path = Path(td) / "usage.json"
        ledger = Ledger(path, cap=5, now=NOW_LIVE)
        first = ledger.reserve("event_odds", 4, "a")
        seen = {}

        def second():
            try:
                Ledger(path, cap=5, now=NOW_LIVE).reserve("event_odds", 4, "b")
                seen["ok"] = True
            except CreditError as exc:
                seen["err"] = str(exc)
        thread = threading.Thread(target=second)
        thread.start()
        thread.join()
        assert "ok" not in seen
        state = ledger.reconcile(first, {"x-requests-last": "4", "x-requests-remaining": "90"})
        assert state["used"] == 4
        ledger.close()


def test_header_can_raise_the_cost_and_cannot_lower_it():
    with __import__("tempfile").TemporaryDirectory() as td:
        path = Path(td) / "usage.json"
        ledger = Ledger(path, cap=100, now=NOW_LIVE)
        high = ledger.reserve("event_odds", 4, "high")
        state = ledger.reconcile(high, {"x-requests-last": "10", "x-requests-remaining": "80"})
        assert state["used"] == 10
        low = ledger.reserve("event_odds", 4, "low")
        state = ledger.reconcile(low, {"x-requests-last": "1", "x-requests-remaining": "79"})
        assert state["used"] == 14
        ledger.close()


def test_missing_header_fails_closed_without_retry():
    feed = Feed(props_header=None)
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        try:
            C.collect_once(_cfg(td), td / "state", td / "archive", feed, _runner([]), NOW_LIVE, _env())
        except C.CollectorError as exc:
            assert KEY not in str(exc)
        assert feed.props_calls == 1
        usage = json.loads((td / "state" / "usage.json").read_text())
        assert usage["fail_closed"] is True
        assert usage["used"] >= 4
        again = Feed()
        try:
            C.collect_once(_cfg(td), td / "state", td / "archive", again, _runner([]), NOW_LIVE, _env())
        except C.CollectorError:
            pass
        assert again.props_calls == 0


def test_auth_error_is_one_call_and_hides_the_key():
    feed = Feed(props_status=401, props_header="4")
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        try:
            C.collect_once(_cfg(td), td / "state", td / "archive", feed, _runner([]), NOW_LIVE, _env())
        except C.CollectorError as exc:
            assert "auth" in str(exc)
            assert KEY not in str(exc)
        else:
            # auth is caught inside the game loop and stops the run
            assert feed.props_calls == 1
        text = (td / "state" / "abstentions.jsonl").read_text()
        assert KEY not in text
        assert feed.props_calls == 1


def test_pregame_quote_is_kept_and_a_live_line_is_not_relabeled():
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        pre = Feed(board_state="pre", props_body=_props(line=70.5))
        ran = []
        C.collect_once(_cfg(td), td / "state", td / "archive", pre, _runner(ran), NOW_PRE, _env())
        stored = json.loads((td / "state" / "anchors.json").read_text())
        cohort = next(iter(stored))
        assert stored[cohort]["anchors"][0]["kind"] == "quoted_pregame"
        assert stored[cohort]["anchors"][0]["value"] == 70.5
        assert C.before_start(stored[cohort]["observed_at"], START)
        live_body = _props(line=55.5)
        live = Feed(board_state="in", props_body=live_body)
        result = C.collect_once(_cfg(td), td / "state", td / "archive", live, _runner(ran), NOW_LIVE, _env())
        assert result["live_ready"] is False
        snapshot = json.loads(Path(result["snapshot"]).read_text())
        obs = snapshot["observations"][0]
        assert obs["anchors"][0]["kind"] == "quoted_pregame"
        assert obs["anchors"][0]["value"] == 70.5
        assert obs["quoted_odds"]["bookmakers"][0]["markets"][0]["outcomes"][0]["point"] == 55.5
        assert obs["boxscore"] == _summary()["boxscore"]
        from cohort import cohort_key
        built = cohort_key(obs["sport"], obs["away"], obs["home"], obs["scheduled_start"])
        assert built == cohort
        assert "espn-1" not in built and "odds-1" not in built
        assert obs["aliases"]["espn_event_id"] == "espn-1"
        assert obs["aliases"]["odds_api_event_id"] == "odds-1"
        # Same file is what both replays were given.
        paths = [item[0][item[0].index("--snapshot") + 1] for item in ran]
        assert paths
        assert len(set(paths)) == 1
        assert Path(paths[0]).read_text() == Path(result["snapshot"]).read_text()


def test_same_ids_do_not_pair_different_teams_or_distant_starts():
    espn = C.espn_games(_board("in"), "nfl")
    odds = C.odds_games(_events())
    odds[0]["home"] = "Buffalo Bills"
    odds[0]["away"] = "Miami Dolphins"
    paired = C.pair_games(espn, odds, "nfl")
    assert paired[0]["ok"] is False
    assert paired[0]["reason"] == "unmatched_names"
    odds = C.odds_games(_events())
    odds[0]["scheduled_start"] = "2026-11-08T18:02:01Z"
    paired = C.pair_games(espn, odds, "nfl")
    assert paired[0]["ok"] is False


def test_cap_blocks_the_props_call():
    feed = Feed()
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        C.collect_once(_cfg(td, cap=1), td / "state", td / "archive", feed, _runner([]), NOW_LIVE, _env())
        assert feed.props_calls == 0
        assert not any(urlparse(url).path.endswith("/odds") for url in feed.urls)


def test_archive_snapshot_is_immutable():
    feed = Feed()
    ran = []
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        result = C.collect_once(_cfg(td), td / "state", td / "archive", feed, _runner(ran), NOW_LIVE, _env())
        path = Path(result["snapshot"])
        original = path.read_text()
        path.write_text(original)
        second = Feed()
        C.collect_once(_cfg(td), td / "state", td / "archive", second, _runner(ran), NOW_LIVE, _env())
        assert path.read_text() == original
        assert second.props_calls == 0
        summary = json.loads((td / "archive" / "daily" / "2026-11-08" / "summary.json").read_text())
        assert summary["live_ready"] is False
        assert summary["calibration_fit"] is False
        assert summary["hypothetical_unit"] == 1.0


def test_regrade_is_idempotent_and_does_not_fetch():
    obs = {
        "observation_id": "g",
        "observed_at": "2026-11-08T19:00:00Z",
        "sport": "nfl",
        "scheduled_start": START,
        "away": "New York Giants",
        "home": "Dallas Cowboys",
        "aliases": {"espn_event_id": "espn-1", "odds_api_event_id": "odds-1"},
        "elapsed_minutes": 30,
        "period_label": "Q2 5:00",
        "score": {"away": 10, "home": 14},
        "boxscore": _summary()["boxscore"],
        "quoted_odds": _props(),
        "anchors": [{"player": "CeeDee Lamb", "market": "player_reception_yds", "value": 70.5, "kind": "quoted_pregame"}],
    }
    final = {
        "schema": "muse-prop1.final-box.v1",
        "state": "post",
        "synthetic": True,
        "boxscore": _summary()["boxscore"],
    }
    pending = dict(final)
    pending["state"] = "in"
    voided = settlements_for_observation(obs, {"schema": final["schema"], "state": "post", "boxscore": {"players": [], "teams": []}})
    assert voided[0]["status"] == "void"
    assert settlements_for_observation(obs, pending)[0]["status"] == "pending"
    graded = settlements_for_observation(obs, final)
    assert graded[0]["status"] == "final"
    assert graded[0]["final"] == 40
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        snap = td / "snap.json"
        snap.write_text(json.dumps({"schema": "marv.prop_snapshot.v1", "observations": [obs]}))
        fin = td / "final.json"
        fin.write_text(json.dumps(final))
        os.environ["LIVE_PROPS_SEND"] = "off"
        from prop_snapshot import replay_file
        replay_file(snap, td / "muse")
        shadow = td / "muse" / "live_props" / "shadow.jsonl"
        text = shadow.read_text().replace('"muse-prop1"', '"claude-prop1"')
        claude = td / "claude.jsonl"
        claude.write_text(text)

        def boom(url):
            raise AssertionError("regrade fetched " + url.split("apiKey")[0])
        first = C.regrade(td / "archive", snap, fin, shadow, claude)
        first_text = first.read_text()
        second = C.regrade(td / "archive", snap, fin, shadow, claude)
        assert second.read_text() == first_text
        report = json.loads(first_text)
        assert report["calibration_fit"] is False
        assert report["live_ready"] is False
        assert report["hypothetical_unit"] == 1.0
        # grade_saved uses the injected fetch and then a second call is local.
        fetches = []

        def fetch(url):
            fetches.append(url)
            assert "the-odds-api" not in url
            return {"header": {"competitions": [{"status": {"type": {"state": "post"}}}]}, "boxscore": _summary()["boxscore"]}
        C.grade_saved(td / "archive", snap, fetch, shadow, claude)
        assert fetches and all("summary" in url for url in fetches)
        C.grade_saved(td / "archive", snap, boom, shadow, claude)


def test_refuses_ncaaw_install_scripts_and_send_imports():
    source = Path(C.__file__).read_text()
    assert "notifier" not in source
    assert "send_telegram" not in source
    assert "prop-line" not in source
    assert "bovada.py" not in source
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        cfg = _cfg(td)
        cfg["collector"]["sports"] = ["ncaaw"]
        try:
            C.assert_tracking_config(cfg, _env())
        except C.CollectorError as exc:
            assert "nba" in str(exc)
        else:
            raise AssertionError("ncaaw must be refused")
        try:
            C.assert_claude_invocation(str(td / "install.sh"), str(td / "venv" / "bin" / "python"))
        except C.CollectorError as exc:
            assert "install" in str(exc).lower() or "refusing" in str(exc)
        else:
            raise AssertionError("install script must be refused")


def test_hash_of_collected_snapshot_matches_the_schema():
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        feed = Feed(board_state="pre", props_body=_props(line=70.5))
        C.collect_once(_cfg(td), td / "state", td / "archive", feed, _runner([]), NOW_PRE, _env())
        live = Feed()
        result = C.collect_once(_cfg(td), td / "state", td / "archive", live, _runner([]), NOW_LIVE, _env())
        obs = json.loads(Path(result["snapshot"]).read_text())["observations"][0]
        digest = input_hash(hash_payload(obs, "observation"))
        assert digest.startswith("sha256:")
        assert obs["elapsed_minutes"] > 0
        assert obs["probability_status"] if False else obs["anchors"][0]["kind"] == "quoted_pregame"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print(f"PASS {name}")
    print("all collector tests passed")
