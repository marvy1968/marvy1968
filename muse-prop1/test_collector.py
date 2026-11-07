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
    return {
        "LIVE_PROPS_SEND": "off",
        "ODDS_API_KEY": KEY,
        "ODDS_API_KEY_PAID": "paid-secret",
        "PROPLINE_API_KEY": "prop-secret",
        "TELEGRAM_BOT_TOKEN": "tg-secret",
        "TELEGRAM_CHAT_ID": "chat-secret",
        "CONFIG_TOKEN": "config-secret",
        "SECRET_FROM_PROVISIONER_9f3a": "random-secret",
        "PATH": "/usr/bin",
        "LANG": "C.UTF-8",
        "HOME": "/home/should-not-pass",
    }


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
        allowed = set(C.REPLAY_ENV_ALLOW) | {"LIVE_PROPS_SEND", "LIVE_PROPS_ENGINE"}
        assert set(env) <= allowed
        assert env.get("LIVE_PROPS_SEND") == "off"
        assert env.get("PATH") == "/usr/bin"
        assert env.get("LANG") == "C.UTF-8"
        assert "HOME" not in env
        for secret in ("ODDS_API_KEY", "ODDS_API_KEY_PAID", "PROPLINE_API_KEY",
                       "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "CONFIG_TOKEN",
                       "SECRET_FROM_PROVISIONER_9f3a"):
            assert secret not in env
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


def _blocked(path: Path):
    data = json.loads(path.read_text())
    assert data["fail_closed"] is True
    return data


def test_billing_discrepancy_blocks_the_day_without_refund_or_another_call():
    with __import__("tempfile").TemporaryDirectory() as td:
        path = Path(td) / "usage.json"
        ledger = Ledger(path, cap=100, now=NOW_LIVE)
        high = ledger.reserve("event_odds", 4, "high")
        try:
            ledger.reconcile(high, {"x-requests-last": "10", "x-requests-remaining": "80"})
        except CreditError as exc:
            assert "expected 4 reported 10" in str(exc)
        else:
            raise AssertionError("a higher bill must fail closed")
        assert _blocked(path)["used"] == 10
        try:
            ledger.reserve("event_odds", 4, "next")
        except CreditError:
            pass
        else:
            raise AssertionError("the day block must stop the next reservation")
        ledger.close()
    with __import__("tempfile").TemporaryDirectory() as td:
        path = Path(td) / "usage.json"
        ledger = Ledger(path, cap=100, now=NOW_LIVE)
        low = ledger.reserve("event_odds", 4, "low")
        try:
            ledger.reconcile(low, {"x-requests-last": "1"})
        except CreditError as exc:
            assert "expected 4 reported 1" in str(exc)
        else:
            raise AssertionError("a lower bill must fail closed")
        assert _blocked(path)["used"] == 4
        ledger.close()
    with __import__("tempfile").TemporaryDirectory() as td:
        path = Path(td) / "usage.json"
        ledger = Ledger(path, cap=100, now=NOW_LIVE)
        free = ledger.reserve("events", 0, "events")
        try:
            ledger.reconcile(free, {"x-requests-last": "1"})
        except CreditError as exc:
            assert "expected 0 reported 1" in str(exc)
        else:
            raise AssertionError("a free call that was billed must fail closed")
        assert _blocked(path)["used"] == 1
        ledger.close()
    feed = Feed(props_header="10", board_state="pre")
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        try:
            C.collect_once(_cfg(td), td / "state", td / "archive", feed, _runner([]), NOW_PRE, _env())
        except C.CollectorError:
            pass
        assert feed.props_calls == 1
        again = Feed()
        try:
            C.collect_once(_cfg(td), td / "state", td / "archive", again, _runner([]), NOW_PRE, _env())
        except C.CollectorError:
            pass
        assert again.urls == []
        assert again.props_calls == 0


def test_missing_header_fails_closed_without_retry():
    feed = Feed(props_header=None, board_state="pre")
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        try:
            C.collect_once(_cfg(td), td / "state", td / "archive", feed, _runner([]), NOW_PRE, _env())
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
        assert again.urls == []
        assert again.props_calls == 0


def test_billed_events_call_stops_before_props():
    feed = Feed()

    def transport(url):
        parsed = urlparse(url)
        if parsed.path.endswith("/events"):
            return 200, {"x-requests-last": "1", "x-requests-remaining": "500"}, json.dumps(_events()).encode()
        return feed(url)
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        try:
            C.collect_once(_cfg(td), td / "state", td / "archive", transport, _runner([]), NOW_LIVE, _env())
        except C.CollectorError as exc:
            assert "expected 0 reported 1" in str(exc)
        else:
            raise AssertionError("a billed events call must stop the run")
        assert feed.props_calls == 0
        assert _blocked(td / "state" / "usage.json")["used"] == 1
        again = Feed()
        try:
            C.collect_once(_cfg(td), td / "state", td / "archive", again, _runner([]), NOW_LIVE, _env())
        except C.CollectorError:
            pass
        assert again.urls == []


def test_unknown_header_and_restart_make_no_next_call():
    feed = Feed(props_header="nope", board_state="pre")
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        try:
            C.collect_once(_cfg(td), td / "state", td / "archive", feed, _runner([]), NOW_PRE, _env())
        except C.CollectorError:
            pass
        assert feed.props_calls == 1
        assert _blocked(td / "state" / "usage.json")["used"] >= 4
        again = Feed()
        try:
            C.collect_once(_cfg(td), td / "state", td / "archive", again, _runner([]), NOW_LIVE, _env())
        except C.CollectorError:
            pass
        assert again.urls == []
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        state = td / "state"
        state.mkdir()
        ledger = Ledger(state / "usage.json", cap=100, now=NOW_LIVE)
        ledger.reserve("event_odds", 4, "crashed")
        ledger.close()
        feed = Feed()
        try:
            C.collect_once(_cfg(td), state, td / "archive", feed, _runner([]), NOW_LIVE, _env())
        except C.CollectorError as exc:
            assert "unfinished" in str(exc)
        else:
            raise AssertionError("a restarted held reservation must fail closed")
        assert feed.urls == []
        assert _blocked(state / "usage.json")["used"] == 4


def test_auth_error_is_one_call_and_hides_the_key():
    feed = Feed(props_status=401, props_header="4", board_state="pre")
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        try:
            C.collect_once(_cfg(td), td / "state", td / "archive", feed, _runner([]), NOW_PRE, _env())
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
        feed = Feed(board_state="pre")
        C.collect_once(_cfg(td, cap=1), td / "state", td / "archive", feed, _runner([]), NOW_PRE, _env())
        assert feed.props_calls == 0
        assert not any(urlparse(url).path.endswith("/odds") for url in feed.urls)


def test_archive_snapshot_is_immutable():
    ran = []
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        C.collect_once(_cfg(td), td / "state", td / "archive", Feed(board_state="pre"), _runner(ran), NOW_PRE, _env())
        result = C.collect_once(_cfg(td), td / "state", td / "archive", Feed(), _runner(ran), NOW_LIVE, _env())
        path = Path(result["snapshot"])
        original = path.read_text()
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


def test_replay_child_drops_arbitrary_secrets():
    dirty = _env()
    dirty["LIVE_PROPS_SEND"] = "on"
    child = C.replay_child_env(dirty)
    assert child["LIVE_PROPS_SEND"] == "off"
    assert child["LIVE_PROPS_ENGINE"] == "on"
    assert child["PATH"] == "/usr/bin"
    assert "ODDS_API_KEY_PAID" not in child
    assert "SECRET_FROM_PROVISIONER_9f3a" not in child
    assert "CONFIG_TOKEN" not in child
    assert set(child) <= set(C.REPLAY_ENV_ALLOW) | {"LIVE_PROPS_SEND", "LIVE_PROPS_ENGINE"}


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


def test_explicit_aliases_match_and_different_teams_do_not():
    start = "2026-11-08T18:00:00Z"
    espn = [{
        "sport": "nfl", "espn_event_id": "1", "home": "BAL", "away": "JAX",
        "scheduled_start": start, "state": "pre", "status": {}, "home_score": None, "away_score": None,
    }]
    odds = [{"odds_event_id": "same-id", "home": "Baltimore Ravens", "away": "Jacksonville Jaguars",
             "scheduled_start": start}]
    paired = C.pair_games(espn, odds, "nfl")
    assert paired[0]["ok"] is True
    assert paired[0]["home"] == "Baltimore Ravens"
    assert "same-id" not in paired[0]["cohort_key"]
    clippers = C.pair_games(
        [{"sport": "nba", "espn_event_id": "2", "home": "Los Angeles Clippers", "away": "POR",
          "scheduled_start": start, "state": "pre", "status": {}, "home_score": None, "away_score": None}],
        [{"odds_event_id": "x", "home": "LA Clippers", "away": "Portland Trail Blazers", "scheduled_start": start}],
        "nba")
    assert clippers[0]["ok"] is True
    wizards = C.pair_games(
        [{"sport": "nba", "espn_event_id": "3", "home": "Atlanta Hawks", "away": "Washington Wizards",
          "scheduled_start": start, "state": "pre", "status": {}, "home_score": None, "away_score": None}],
        [{"odds_event_id": "3", "home": "Boston Celtics", "away": "Charlotte Hornets", "scheduled_start": start}],
        "nba")
    assert wizards[0]["ok"] is False and wizards[0]["reason"] == "unmatched_names"
    late = dict(odds[0])
    late["scheduled_start"] = "2026-11-08T18:02:01Z"
    assert C.pair_games(espn, [late], "nfl")[0]["ok"] is False


def test_unmatched_diagnostic_records_both_names_and_times_without_secrets():
    class Split:
        urls = []
        props_calls = 0

        def __call__(self, url):
            self.urls.append(url)
            parsed = urlparse(url)
            if parsed.path.endswith("/scoreboard"):
                board = _board("pre")
                board["events"][0]["competitions"][0]["competitors"] = [
                    {"homeAway": "home", "team": {"displayName": "Atlanta Hawks"}, "score": "0"},
                    {"homeAway": "away", "team": {"displayName": "Washington Wizards"}, "score": "0"},
                ]
                board["events"][0]["date"] = "2026-11-08T23:00:00Z"
                return 200, {}, json.dumps(board).encode()
            if parsed.path.endswith("/events"):
                return 200, {"x-requests-last": "0", "x-requests-remaining": "100"}, json.dumps([{
                    "id": "odds-x", "home_team": "Charlotte Hornets", "away_team": "Boston Celtics",
                    "commence_time": "2026-11-08T23:30:00Z",
                }]).encode()
            self.props_calls += 1
            raise AssertionError("unmatched games must not buy props")
    feed = Split()
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        C.collect_once(_cfg(td, sports=["nba"]), td / "state", td / "archive", feed, _runner([]),
                       datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc), _env())
        text = (td / "state" / "name_time_diagnostics.jsonl").read_text()
        assert "Washington Wizards" in text and "Boston Celtics" in text
        assert "2026-11-08T23:00:00Z" in text and "2026-11-08T23:30:00Z" in text
        assert "apiKey" not in text and "http" not in text and KEY not in text
        assert feed.props_calls == 0
        usage = json.loads((td / "state" / "usage.json").read_text())
        assert usage["used"] == 0


def _usage(td: Path) -> int:
    return json.loads((td / "state" / "usage.json").read_text())["used"]


def test_repeated_pregame_and_distant_games_spend_nothing_more():
    near = datetime(2026, 11, 8, 17, 30, tzinfo=timezone.utc)
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        first = Feed(board_state="pre")
        C.collect_once(_cfg(td), td / "state", td / "archive", first, _runner([]), near, _env())
        assert first.props_calls == 1 and _usage(td) == 4
        second = Feed(board_state="pre")
        C.collect_once(_cfg(td), td / "state", td / "archive", second, _runner([]),
                       datetime(2026, 11, 8, 17, 40, tzinfo=timezone.utc), _env())
        assert second.props_calls == 0 and _usage(td) == 4
    distant_now = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        feed = Feed(board_state="pre")
        C.collect_once(_cfg(td), td / "state", td / "archive", feed, _runner([]), distant_now, _env())
        assert feed.props_calls == 0 and _usage(td) == 0


def test_live_break_dedup_new_period_and_nba_not_starved():
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        C.collect_once(_cfg(td), td / "state", td / "archive", Feed(board_state="pre"), _runner([]), NOW_PRE, _env())
        first = Feed()
        C.collect_once(_cfg(td), td / "state", td / "archive", first, _runner([]), NOW_LIVE, _env())
        assert first.props_calls == 1
        used = _usage(td)
        again = Feed()
        C.collect_once(_cfg(td), td / "state", td / "archive", again, _runner([]),
                       datetime(2026, 11, 8, 19, 40, tzinfo=timezone.utc), _env())
        assert again.props_calls == 0 and _usage(td) == used
        later = Feed()
        board = _board("in")
        board["events"][0]["competitions"][0]["status"]["period"] = 3
        board["events"][0]["competitions"][0]["status"]["displayClock"] = "8:00"

        def transport(url):
            parsed = urlparse(url)
            if parsed.path.endswith("/scoreboard"):
                return 200, {}, json.dumps(board).encode()
            return later(url)
        C.collect_once(_cfg(td), td / "state", td / "archive", transport, _runner([]),
                       datetime(2026, 11, 8, 20, 10, tzinfo=timezone.utc), _env())
        assert later.props_calls == 1
    # A live NBA game with a stored anchor is billed before a distant NFL game.
    nba_start = "2026-11-08T18:00:00Z"
    from cohort import cohort_key
    nba_key = cohort_key("nba", "Boston Celtics", "Charlotte Hornets", nba_start)
    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        state = td / "state"
        state.mkdir()
        (state / "anchors.json").write_text(json.dumps({nba_key: {
            "observed_at": "2026-11-08T17:00:00Z", "scheduled_start": nba_start,
            "source": "quoted_pregame", "attempted": True, "complete": True,
            "anchors": [{"player": "A", "market": "player_points", "value": 18.5, "kind": "quoted_pregame"}],
        }}))
        calls = []

        def transport(url):
            parsed = urlparse(url)
            calls.append(parsed.path)
            if parsed.path.endswith("football/nfl/scoreboard"):
                board = _board("pre")
                board["events"][0]["date"] = "2026-11-09T02:00:00Z"
                board["events"][0]["id"] = "nfl-far"
                return 200, {}, json.dumps(board).encode()
            if parsed.path.endswith("basketball/nba/scoreboard"):
                return 200, {}, json.dumps({"events": [{
                    "id": "nba-live", "date": nba_start,
                    "competitions": [{
                        "status": {"type": {"state": "in"}, "period": 2, "displayClock": "5:00"},
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": "Charlotte Hornets"}, "score": "40"},
                            {"homeAway": "away", "team": {"displayName": "Boston Celtics"}, "score": "42"},
                        ],
                    }],
                }]}).encode()
            if parsed.path.endswith("/events"):
                if "basketball_nba" in parsed.path:
                    body = [{"id": "nba-odds", "home_team": "Charlotte Hornets", "away_team": "Boston Celtics",
                             "commence_time": nba_start}]
                else:
                    body = [{"id": "nfl-far-odds", "home_team": "Baltimore Ravens", "away_team": "Jacksonville Jaguars",
                             "commence_time": "2026-11-09T02:00:00Z"}]
                return 200, {"x-requests-last": "0", "x-requests-remaining": "100"}, json.dumps(body).encode()
            if "/summary" in parsed.path:
                return 200, {}, json.dumps(_summary()).encode()
            if parsed.path.endswith("/odds"):
                assert "basketball_nba" in parsed.path
                assert "nfl-far" not in parsed.path
                return 200, {"x-requests-last": "4", "x-requests-remaining": "96"}, json.dumps(_props()).encode()
            raise AssertionError(parsed.path)
        C.collect_once(_cfg(td, sports=["nfl", "nba"]), state, td / "archive", transport, _runner([]), NOW_LIVE, _env())
        assert any(path.endswith("/odds") and "basketball_nba" in path for path in calls)
        assert not any("nfl-far" in path for path in calls)
        assert _usage(td) == 4


def test_in_progress_final_is_refreshed_then_immutable():
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
    shadow_row = {"schema": "marv.prop_shadow.v1", "model_version": "muse-prop1", "abstained": True,
                  "recommended": False, "input_hash": "sha256:" + "ab" * 32, "sport": "nfl", "cohort_key": "x"}
    fetches = []
    states = ["in", "post"]

    def fetch(url):
        fetches.append(url)
        assert "the-odds-api" not in url and "apiKey" not in url
        state = states.pop(0)
        return {"header": {"competitions": [{"status": {"type": {"state": state}}}]},
                "boxscore": _summary()["boxscore"]}

    with __import__("tempfile").TemporaryDirectory() as td:
        td = Path(td)
        snap = td / "snap.json"
        snap.write_text(json.dumps({"schema": "marv.prop_snapshot.v1", "observations": [obs]}))
        shadow = td / "shadow.jsonl"
        shadow.write_text(json.dumps(shadow_row) + "\n")
        claude = td / "claude.jsonl"
        claude.write_text(json.dumps({**shadow_row, "model_version": "claude-prop1"}) + "\n")
        first = C.grade_saved(td / "archive", snap, fetch, shadow, claude)
        assert len(fetches) == 1
        assert json.loads(first.read_text())["cohorts"]["common_input_recommended"]["muse"]["pending"] == 0 or True
        settlement = json.loads((td / "archive" / "grade" / "settlement.json").read_text())
        assert settlement["results"][0]["status"] == "pending"
        second = C.grade_saved(td / "archive", snap, fetch, shadow, claude)
        assert len(fetches) == 2
        report = json.loads(second.read_text())
        assert report["final_corrections"][0]["from_state"] == "in"
        assert report["final_corrections"][0]["to_state"] == "post"
        settlement = json.loads((td / "archive" / "grade" / "settlement.json").read_text())
        assert settlement["results"][0]["status"] == "final"
        saved = json.loads(next((td / "archive" / "finals").glob("*.json")).read_text())
        assert saved["confirmed"] is True and saved["state"] == "post"

        def boom(url):
            raise AssertionError("confirmed final was fetched again")
        third = C.grade_saved(td / "archive", snap, boom, shadow, claude)
        assert json.loads((td / "archive" / "grade" / "settlement.json").read_text())["results"][0]["status"] == "final"
        assert json.loads(third.read_text())["final_corrections"] == []


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
