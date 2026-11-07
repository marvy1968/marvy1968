import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from marv import live_prop_engine as E
from marv.config import Settings
from marv.main import cmd_live_props, main

NOW = datetime(2026, 11, 8, 19, 0, tzinfo=timezone.utc)
FRESH = "2026-11-08T18:59:00Z"


def nfl_summary(rec_yds="40", rec="3", tgts="5", plays="30"):
    return {"boxscore": {
        "players": [{"team": {"displayName": "Dallas Cowboys"}, "statistics": [
            {"name": "passing", "labels": ["C/ATT", "YDS", "AVG", "TD", "INT"], "athletes": [
                {"athlete": {"displayName": "Dak Prescott"}, "stats": ["12/17", "151", "8.9", "1", "0"]}]},
            {"name": "receiving", "labels": ["REC", "YDS", "AVG", "TD", "LONG", "TGTS"], "athletes": [
                {"athlete": {"displayName": "CeeDee Lamb"}, "stats": [rec, rec_yds, "12.4", "0", "28", tgts]}]}]}],
        "teams": [{"team": {"displayName": "Dallas Cowboys"}, "statistics": [
            {"name": "totalOffensivePlays", "displayValue": plays},
            {"name": "completionAttempts", "displayValue": "12-17"}]},
            {"team": {"displayName": "New York Giants"}, "statistics": [
                {"name": "totalOffensivePlays", "displayValue": "28"}]}]}}


def odds(line=60.5, over=-115, under=-115, upd=FRESH, player="CeeDee Lamb", market="player_reception_yds"):
    return {"bookmakers": [{"key": "bovada", "markets": [{"key": market, "last_update": upd, "outcomes": [
        {"name": "Over", "description": player, "point": line, "price": over},
        {"name": "Under", "description": player, "point": line, "price": under}]}]}]}


class ParseTests(unittest.TestCase):
    def test_box_made_attempts_and_team_plays(self):
        box = E.parse_box(nfl_summary())
        dak = box["players"]["Dak Prescott"]["stats"]["passing"]
        self.assertEqual((dak["C/ATT"], dak["ATT"], dak["YDS"]), (12.0, 17.0, 151.0))
        self.assertEqual(E.team_plays("nfl", box["teams"]["Dallas Cowboys"]), 30.0)

    def test_basketball_possessions(self):
        t = {"fieldGoalsAttempted": 40, "freeThrowsAttempted": 10, "offensiveRebounds": 5, "totalTurnovers": 7}
        self.assertAlmostEqual(E.team_plays("nba", t), 46.4)

    def test_stale_and_one_sided_lines_dropped(self):
        self.assertEqual(len(E.bovada_lines(odds(), now=NOW)), 1)
        self.assertEqual(E.bovada_lines(odds(upd="2026-11-08T18:40:00Z"), now=NOW), [])
        one = odds()
        one["bookmakers"][0]["markets"][0]["outcomes"].pop()
        self.assertEqual(E.bovada_lines(one, now=NOW), [])
        self.assertEqual(E.bovada_lines({"bookmakers": [{**odds()["bookmakers"][0], "key": "fanduel"}]}, now=NOW), [])


class ProjectionTests(unittest.TestCase):
    def st(self, **kw):
        base = dict(sport="nfl", market="player_reception_yds", player="X", so_far=40.0, anchor=70.0,
                    elapsed=30.0, team_plays=31.0)
        base.update(kw)
        return E.PropState(**base)

    def test_faster_pace_projects_more(self):
        # same production per play; the faster game has more plays left to produce in
        slow, fast = E.project(self.st(team_plays=24, so_far=24.0)), E.project(self.st(team_plays=40, so_far=40.0))
        self.assertGreater(fast["rem_mean"], slow["rem_mean"])
        self.assertGreater(fast["pace"], 1.0)

    def test_trailing_team_passes_more_and_rout_rests_starters(self):
        trail = E.project(self.st(margin=-14))["rem_mean"]
        lead = E.project(self.st(margin=14))["rem_mean"]
        self.assertGreater(trail, lead)
        rush_trail = E.project(self.st(market="player_rush_yds", margin=-14))["rem_mean"]
        rush_lead = E.project(self.st(market="player_rush_yds", margin=14))["rem_mean"]
        self.assertLess(rush_trail, rush_lead)
        rout = E.project(self.st(margin=28, elapsed=48))
        self.assertLess(rout["script"], 0.7)

    def test_basketball_foul_out_and_blowout(self):
        b = dict(sport="nba", market="player_points", so_far=14, anchor=24, elapsed=36, team_plays=75)
        self.assertEqual(E.project(self.st(**b, fouls=6))["rem_mean"], 0.0)
        self.assertLess(E.project(self.st(**b, fouls=5))["rem_mean"], E.project(self.st(**b))["rem_mean"])
        self.assertLess(E.project(self.st(**b, margin=25))["rem_mean"], E.project(self.st(**b, margin=3))["rem_mean"])

    def test_p_over_monotone_and_bounded(self):
        hi = E.p_over("nfl", "player_reception_yds", 40, 30, 55.5)[0]
        lo = E.p_over("nfl", "player_reception_yds", 40, 30, 85.5)[0]
        self.assertGreater(hi, lo)
        self.assertEqual(E.p_over("nfl", "player_reception_yds", 60, 0, 55.5)[0], 1.0)
        self.assertEqual(E.p_over("nfl", "player_reception_yds", 40, 0, 55.5)[0], 0.0)
        # receptions: integer line has a push mass
        _, push = E.p_over("nfl", "player_receptions", 3, 2.5, 5)
        self.assertGreater(push, 0.1)

    def test_mean_of_simulation_matches_projection(self):
        import numpy as np
        rng = np.random.default_rng(1)
        spec = E.specs("nfl")["player_reception_yds"]
        lam = rng.gamma(spec.disp, 30 / spec.unit / spec.disp, 50000)
        n = rng.poisson(lam)
        shape = 1 / spec.cv ** 2
        tot = np.where(n > 0, rng.gamma(np.maximum(n, 1) * shape, spec.unit / shape), 0)
        self.assertAlmostEqual(float(tot.mean()), 30, delta=1.0)


class ScanTests(unittest.TestCase):
    def test_soft_line_recommended_fair_line_not(self):
        # Lamb: 40 yds on 5 targets at the half, pregame 70; Bovada hangs 55.5 -> clear Over
        priced, recs = E.scan_game("nfl", nfl_summary(), odds(line=55.5), {("ceedee lamb", "player_reception_yds"): 70.0},
                                   30.0, "Dallas Cowboys", "New York Giants", 14, 10, now=NOW)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["side"], "Over")
        self.assertGreater(r["edge"], 0.07)
        # a line at Marv's projection: no recommendation
        pr = priced[0]["proj"]
        _, recs2 = E.scan_game("nfl", nfl_summary(), odds(line=round(pr) + 0.5), {("ceedee lamb", "player_reception_yds"): 70.0},
                               30.0, "Dallas Cowboys", "New York Giants", 14, 10, now=NOW)
        self.assertEqual(recs2, [])

    def test_no_anchor_no_price_and_no_usage_blocked(self):
        priced, _ = E.scan_game("nfl", nfl_summary(), odds(line=55.5), {}, 30.0, "Dallas Cowboys", "New York Giants", 14, 10, now=NOW)
        self.assertEqual(priced, [])
        mem = {"_elapsed": 15.0, "ceedee lamb|player_reception_yds": 5.0}  # no new targets since the end of Q1
        _, recs = E.scan_game("nfl", nfl_summary(), odds(line=55.5), {("ceedee lamb", "player_reception_yds"): 70.0},
                              30.0, "Dallas Cowboys", "New York Giants", 14, 10, prev_usage=mem, now=NOW)
        self.assertEqual(recs, [])

    def test_gate_rejects_bad_prices_and_late(self):
        ev = {"side": "Over", "p": 0.7, "market_p": 0.5, "edge": 0.2, "ev": 0.3, "price": -250, "line": 50.5,
              "proj": 70, "left": 30, "so_far": 30}
        self.assertEqual(E.qualifies(ev, "nfl", "player_reception_yds"), (False, "price out of range"))
        self.assertEqual(E.qualifies({**ev, "price": -110, "left": 3}, "nfl", "player_reception_yds")[1], "too early/late")
        self.assertTrue(E.qualifies({**ev, "price": -110}, "nfl", "player_reception_yds")[0])
        with mock.patch.dict(os.environ, {"LIVE_PROP_MIN_EDGE": "0.25"}):
            self.assertEqual(E.qualifies({**ev, "price": -110}, "nfl", "player_reception_yds")[1], "edge")


class LedgerTests(unittest.TestCase):
    def test_record_dedupe_grade_and_record(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            rec = {"player": "CeeDee Lamb", "market": "player_reception_yds", "label": "Receiving yards", "side": "Over",
                   "line": 55.5, "price": -115, "p": 0.66, "market_p": 0.5, "edge": 0.16, "so_far": 40.0, "proj": 75.0}
            self.assertEqual(len(E.record_recs(d, "nfl", "g1", "NYG @ DAL", "end of Q2", [rec])), 1)
            self.assertEqual(E.record_recs(d, "nfl", "g1", "NYG @ DAL", "end of Q3", [rec]), [])  # never twice
            graded = E.grade_game(d, "nfl", "g1", nfl_summary(rec_yds="81"))
            self.assertEqual(graded[0]["result"], "win")
            r = E.record(d)
            self.assertEqual((r["wins"], r["losses"], r["proven"]), (1, 0, False))
            self.assertIn("1-0", E.final_text("NYG 10 – 24 DAL", graded, r))
            self.assertIn("all", E.report(d))

    def test_anchor_prefers_marv_projection(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            E.save_pregame(d, {"id": "ev1", "commence_time": "2026-11-08T18:00:00Z"},
                           [{"player": "CeeDee Lamb", "market": "player_reception_yds", "line": 72.5}])
            self.assertEqual(E.anchors(d, "nfl", "ev1")[("ceedee lamb", "player_reception_yds")], 72.5)
            (d / "props_priced_nfl.json").write_text('[{"player": "CeeDee Lamb", "market": "player_reception_yds", "proj": 66.0}]')
            self.assertEqual(E.anchors(d, "nfl", "ev1")[("ceedee lamb", "player_reception_yds")], 66.0)

    def test_alert_text_is_labelled_paper(self):
        r = {"player": "CeeDee Lamb", "side": "Over", "line": 55.5, "label": "Receiving yards", "price": -115,
             "so_far": 40.0, "proj": 75.0, "p": 0.66, "market_p": 0.5, "edge": 0.16, "pace": 1.05, "script": 1.0}
        t = E.alert_text("nfl", "NYG @ DAL", "end of Q2", "NYG 10 – 14 DAL", [r])
        self.assertIn("PAPER", t)
        self.assertIn("CLAUDE PROP 1", t)
        self.assertIn("Over 55.5", t)


def _files(root: Path) -> dict[str, bytes]:
    out = {}
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


def _changed(before: dict[str, bytes], after: dict[str, bytes]) -> set[str]:
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


def _live_odds(**kw):
    """A Bovada line timestamped at the test clock, so the freshness gate does not depend on a fixed date."""
    kw.setdefault("upd", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return odds(**kw)


def _game():
    return SimpleNamespace(id="g1", home="Dallas Cowboys", away="New York Giants",
                           info={"state": "in", "period": 2, "clock": "0:00", "live_home": 14, "live_away": 10},
                           home_score=14, away_score=10)


def _rec():
    return {"player": "CeeDee Lamb", "market": "player_reception_yds", "label": "Receiving yards", "side": "Over",
            "line": 55.5, "price": -115, "p": 0.66, "market_p": 0.5, "edge": 0.16, "so_far": 40.0, "proj": 75.0,
            "pace": 1.05, "script": 1.0, "recommended": True, "ev": 0.2}


class SendGateTests(unittest.TestCase):
    """LIVE_PROPS_SEND off: no sender, no recommendation ledger. On: mocks only, never Telegram HTTP."""

    def setUp(self):
        self._http = mock.patch("marv.telegram.requests.post", side_effect=AssertionError("telegram HTTP reachable"))
        self._http.start()

    def tearDown(self):
        self._http.stop()

    def _seed(self, d: Path, recs: bytes = b"NOT-JSON-SENTINEL\n") -> None:
        d.mkdir(parents=True, exist_ok=True)
        (d / "picks.json").write_bytes(b"NOT-JSON-PICKS\n")
        (d / "props_picks.json").write_bytes(b"NOT-JSON-PROPS\n")
        (d / "live_alerts.json").write_bytes(b"NOT-JSON-ALERTS\n")
        (d / "props_priced_nfl.json").write_text(
            '[{"player": "CeeDee Lamb", "market": "player_reception_yds", "proj": 70.0}]')
        live = d / "live_props"
        live.mkdir(parents=True, exist_ok=True)
        (live / "recs.json").write_bytes(recs)

    def _runner(self, d: Path) -> E.Runner:
        client = mock.Mock()
        client.summary.return_value = nfl_summary()
        return E.Runner(Settings(state_dir=str(d), odds_api_key="test-key-not-real"), client)

    def _period(self, runner, mem, send):
        with mock.patch.object(runner, "_event", return_value={"id": "ev1", "away_team": "NYG", "home_team": "DAL",
                                                               "commence_time": "2026-11-08T18:00:00Z"}), \
                mock.patch.object(runner, "_odds", return_value=_live_odds(line=55.5)):
            return runner.on_period("nfl", _game(), 30.0, "end of Q2", mem, send, "football/nfl")

    def test_flag_default_on_and_unrecognized_fails_closed(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LIVE_PROPS_SEND", None)
            self.assertTrue(E.live_props_send_enabled())
        for off in ("off", "OFF", "  off  ", "0", "false", "no", "", "   ", "maybe", "disabled", "yes please"):
            with mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": off}):
                self.assertFalse(E.live_props_send_enabled(), off)
        for on in ("on", "ON", "  true  ", "1", "yes"):
            with mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": on}):
                self.assertTrue(E.live_props_send_enabled(), on)

    def test_deliver_and_ledger_helpers_refuse_when_off(self):
        sent = []

        def boom(text):
            sent.append(text)
            raise AssertionError("prop sender reachable")

        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "off"}):
            d = Path(d)
            self._seed(d)
            before = (d / "live_props" / "recs.json").read_bytes()
            self.assertFalse(E.deliver_prop_message(boom, "hello"))
            self.assertEqual(sent, [])
            self.assertEqual(E.bind_prop_send(boom)("hello"), None)
            self.assertEqual(sent, [])
            self.assertEqual(E.record_recs(d, "nfl", "g1", "NYG @ DAL", "end of Q2", [_rec()]), [])
            self.assertEqual(E.grade_game(d, "nfl", "g1", nfl_summary()), [])
            E.save_recs(d, [{"tampered": True}])
            self.assertEqual((d / "live_props" / "recs.json").read_bytes(), before)

    def test_off_period_shadow_and_budget_only_including_refresh_and_dedup(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "off", "LIVE_PROPS_ENGINE": "on"}):
            d = Path(d)
            runner = self._runner(d)
            self.assertTrue(runner.enabled("nfl"))
            self._seed(d)
            before = _files(d)
            mem, sent = {}, []

            def boom(text):
                sent.append(text)
                raise AssertionError("prop sender reachable")

            self.assertEqual(self._period(runner, mem, boom), 0)
            self.assertEqual(self._period(runner, mem, boom), 0)  # refresh / second break: still no ledger
            self.assertEqual(sent, [])
            self.assertEqual(mem, {})  # usage and dedup stay on the runner, not on caller state
            changed = _changed(before, _files(d))
            self.assertEqual(changed, set(E.SEND_OFF_DISK_FILES))
            self.assertFalse((d / "live_props" / "pregame_lines.json").exists())
            shadow = (d / "live_props" / "shadow.jsonl").read_text().splitlines()
            self.assertEqual(len(shadow), 2)
            self.assertIn("CeeDee Lamb", shadow[0])
            self.assertEqual((d / "live_props" / "recs.json").read_bytes(), b"NOT-JSON-SENTINEL\n")

    def test_off_final_grade_report_and_pregame_anchor_stay_off_disk(self):
        flags = {"LIVE_PROPS_SEND": "off", "LIVE_PROPS_ENGINE": "on", "LIVE_PROPS_LOG": "on"}
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, flags):
            d = Path(d)
            runner = self._runner(d)
            self._seed(d)
            (d / "props_priced_nfl.json").unlink()  # the scan has to use the in-memory anchor
            runner.client.summary.side_effect = AssertionError("summary fetched while send is off")
            bare = SimpleNamespace(id="g1")  # no scores: grading would throw if it ran
            before = _files(d)
            self.assertEqual(runner.on_final("nfl", bare, lambda text: (_ for _ in ()).throw(AssertionError("sender")), "football/nfl"), 0)
            self.assertEqual(E.grade_game(d, "nfl", "g1", nfl_summary()), [])
            self.assertEqual(_changed(before, _files(d)), set())
            with mock.patch.object(runner, "_event", return_value={"id": "ev1", "away_team": "New York Giants",
                                                                   "home_team": "Dallas Cowboys",
                                                                   "commence_time": "2026-11-08T18:00:00Z"}), \
                    mock.patch.object(runner, "_odds", return_value=_live_odds(line=55.5)):
                self.assertEqual(runner.capture_pregame("nfl", "Dallas Cowboys", "New York Giants"), 1)
                self.assertEqual(_changed(before, _files(d)), {"live_props/credits.json"})
                self.assertFalse((d / "live_props" / "pregame_lines.json").exists())
                runner.client.summary.side_effect = None
                mem = {}
                self.assertEqual(self._period(runner, mem, lambda text: (_ for _ in ()).throw(AssertionError("sender"))), 0)
            self.assertEqual(mem, {})
            changed = _changed(before, _files(d))
            self.assertEqual(changed, set(E.SEND_OFF_DISK_FILES))
            self.assertIn("CeeDee Lamb", (d / "live_props" / "shadow.jsonl").read_text())
            self.assertFalse((d / "live_props_log.json").exists())
            self.assertEqual((d / "live_props" / "recs.json").read_bytes(), b"NOT-JSON-SENTINEL\n")

    def test_off_report_reads_without_writing(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "off", "LIVE_PROPS_LOG": "on"}):
            d = Path(d)
            (d / "live_props").mkdir()
            (d / "live_props" / "recs.json").write_text("[]")
            (d / "picks.json").write_bytes(b"NOT-JSON-PICKS\n")
            before = _files(d)
            text = E.report(d)
            self.assertIn("Claude Prop 1", text)
            self.assertEqual(E.grade_game(d, "nfl", "g1", nfl_summary()), [])
            self.assertEqual(_changed(before, _files(d)), set())

    def test_off_error_before_shadow_writes_no_ledger_and_does_not_send(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "off"}):
            d = Path(d)
            runner = self._runner(d)
            self._seed(d)
            before = _files(d)
            sent = []

            def boom(text):
                sent.append(text)
                raise AssertionError("prop sender reachable")

            with mock.patch.object(runner, "_event", side_effect=RuntimeError("odds down")):
                with self.assertRaises(RuntimeError):
                    runner.on_period("nfl", _game(), 30.0, "end of Q2", {}, boom, "football/nfl")
            self.assertEqual(sent, [])
            changed = _changed(before, _files(d))
            self.assertEqual(changed, {"live_props/credits.json"})
            self.assertFalse((d / "live_props" / "shadow.jsonl").exists())
            self.assertFalse((d / "live_props" / "pregame_lines.json").exists())
            self.assertFalse((d / "live_props_log.json").exists())
            self.assertEqual((d / "live_props" / "recs.json").read_bytes(), b"NOT-JSON-SENTINEL\n")

    def test_on_period_sends_once_via_mock_and_dedupes_without_http(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "on"}):
            d = Path(d)
            runner = self._runner(d)
            (d / "props_priced_nfl.json").write_text(
                '[{"player": "CeeDee Lamb", "market": "player_reception_yds", "proj": 70.0}]')
            (d / "picks.json").write_bytes(b"NOT-JSON-PICKS\n")
            mem, sent = {}, []
            self.assertEqual(self._period(runner, mem, sent.append), 1)
            self.assertEqual(self._period(runner, mem, sent.append), 0)  # same prop is not sent twice
            self.assertEqual(len(sent), 1)
            self.assertIn("CLAUDE PROP 1", sent[0])
            self.assertIn("PAPER", sent[0])
            book = json.loads((d / "live_props" / "recs.json").read_text())
            self.assertEqual(len(book), 1)
            self.assertEqual(mem["prop_recs"], 1)
            self.assertEqual((d / "picks.json").read_bytes(), b"NOT-JSON-PICKS\n")
            graded_send = []
            self.assertEqual(runner.on_final("nfl", _game(), graded_send.append, "football/nfl"), 1)
            self.assertEqual(len(graded_send), 1)
            self.assertIn("final", graded_send[0])

    def test_on_send_error_happens_after_ledger_off_never_calls_sender(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            runner = self._runner(d)
            (d / "props_priced_nfl.json").write_text(
                '[{"player": "CeeDee Lamb", "market": "player_reception_yds", "proj": 70.0}]')

            def explode(text):
                raise RuntimeError("sender reachable")

            with mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "off"}):
                self.assertEqual(self._period(runner, {}, explode), 0)
                self.assertFalse((d / "live_props" / "recs.json").exists())
            with mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "on"}):
                with self.assertRaises(RuntimeError):
                    self._period(runner, {}, explode)
            self.assertTrue((d / "live_props" / "recs.json").exists())


class CliSendGateTests(unittest.TestCase):
    def setUp(self):
        self._http = mock.patch("marv.telegram.requests.post", side_effect=AssertionError("telegram HTTP reachable"))
        self._http.start()

    def tearDown(self):
        self._http.stop()

    def _settings(self, d: Path) -> Settings:
        (d / "props_priced_nfl.json").write_text(
            '[{"player": "CeeDee Lamb", "market": "player_reception_yds", "proj": 70.0}]')
        (d / "picks.json").write_bytes(b"NOT-JSON-PICKS\n")
        return Settings(state_dir=str(d), odds_api_key="test-key-not-real",
                        telegram_bot_token="not-a-real-token", telegram_chat_id="1")

    def _args(self, **kw):
        base = dict(action="scan", sport="nfl", top=15, dry_run=False)
        base.update(kw)
        return SimpleNamespace(**base)

    def _run(self, s, args, scan=None):
        client = mock.Mock()
        client.scoreboard.return_value = [{}]
        client.summary.return_value = nfl_summary()
        patches = [
            mock.patch("marv.data.espn.ESPNClient", return_value=client),
            mock.patch("marv.data.espn.parse_event", return_value=_game()),
            mock.patch("marv.live_prop_engine.Runner._event", return_value={"id": "ev1"}),
            mock.patch("marv.live_prop_engine.Runner._odds", return_value=_live_odds(line=55.5)),
        ]
        if scan is not None:
            patches.append(mock.patch("marv.live_prop_engine.scan_game", side_effect=scan))
        started = [p.start() for p in patches]
        try:
            return cmd_live_props(s, args)
        finally:
            for p in patches:
                p.stop()
            del started

    def test_cli_off_scan_refresh_writes_only_shadow_and_cannot_send(self):
        flags = {"LIVE_PROPS_SEND": "off", "LIVE_PROPS_ENGINE": "on", "LIVE_PROPS_LOG": "on"}
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, flags):
            d = Path(d)
            s = self._settings(d)
            live = d / "live_props"
            live.mkdir()
            (live / "recs.json").write_bytes(b"NOT-JSON-SENTINEL\n")
            before = _files(d)
            sender = mock.Mock(side_effect=AssertionError("prop sender reachable"))
            with mock.patch("marv.main.send_message", sender), mock.patch("marv.telegram.send_message", sender):
                self.assertEqual(self._run(s, self._args()), 0)
                self.assertEqual(self._run(s, self._args()), 0)  # refresh
            self.assertEqual(sender.call_count, 0)
            changed = _changed(before, _files(d))
            self.assertEqual(changed, {"live_props/shadow.jsonl"})
            self.assertFalse((d / "live_props" / "credits.json").exists())
            self.assertFalse((d / "live_props" / "pregame_lines.json").exists())
            self.assertFalse((d / "live_props_log.json").exists())
            self.assertEqual(len((d / "live_props" / "shadow.jsonl").read_text().splitlines()), 2)
            self.assertEqual((d / "live_props" / "recs.json").read_bytes(), b"NOT-JSON-SENTINEL\n")
            self.assertEqual((d / "picks.json").read_bytes(), b"NOT-JSON-PICKS\n")

    def test_cli_dry_run_writes_nothing_when_send_on_or_off(self):
        for flag in ("on", "off"):
            with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": flag}):
                d = Path(d)
                s = self._settings(d)
                before = _files(d)
                sender = mock.Mock(side_effect=AssertionError("prop sender reachable"))
                with mock.patch("marv.main.send_message", sender), mock.patch("marv.telegram.send_message", sender):
                    self.assertEqual(self._run(s, self._args(dry_run=True)), 0)
                self.assertEqual(sender.call_count, 0)
                # Runner() creates the live_props directory; it must not create a file.
                self.assertEqual(_changed(before, _files(d)), set())
                self.assertFalse((d / "live_props" / "shadow.jsonl").exists())
                self.assertFalse((d / "live_props" / "recs.json").exists())

    def test_cli_error_and_report_do_not_send_or_write_ledger(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "off"}):
            d = Path(d)
            s = self._settings(d)
            sender = mock.Mock(side_effect=AssertionError("prop sender reachable"))
            with mock.patch("marv.main.send_message", sender), mock.patch("marv.telegram.send_message", sender):
                with self.assertRaises(RuntimeError):
                    self._run(s, self._args(), scan=RuntimeError("scan failed"))
                self.assertEqual(cmd_live_props(s, self._args(action="report")), 0)
            self.assertEqual(sender.call_count, 0)
            self.assertFalse((d / "live_props" / "shadow.jsonl").exists())
            self.assertFalse((d / "live_props" / "recs.json").exists())

    def test_cli_on_sends_once_through_mock_then_dedupes(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "on"}):
            d = Path(d)
            s = self._settings(d)
            sender = mock.Mock()
            with mock.patch("marv.main.send_message", sender), \
                    mock.patch("marv.telegram.send_message", side_effect=AssertionError("direct telegram sender")):
                self.assertEqual(self._run(s, self._args()), 0)
                self.assertEqual(self._run(s, self._args()), 0)
            self.assertEqual(sender.call_count, 1)
            self.assertIn("CLAUDE PROP 1", sender.call_args[0][2])
            book = json.loads((d / "live_props" / "recs.json").read_text())
            self.assertEqual(len(book), 1)
            self.assertTrue((d / "live_props" / "shadow.jsonl").exists())

    def test_cli_main_entrypoint_off_cannot_send(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {
                "LIVE_PROPS_SEND": "off", "ODDS_API_KEY": "test-key-not-real", "STATE_DIR": d,
                "TELEGRAM_BOT_TOKEN": "not-a-real-token", "TELEGRAM_CHAT_ID": "1", "SPORTS": "nfl"}):
            d = Path(d)
            (d / "props_priced_nfl.json").write_text(
                '[{"player": "CeeDee Lamb", "market": "player_reception_yds", "proj": 70.0}]')
            sender = mock.Mock(side_effect=AssertionError("prop sender reachable"))
            with mock.patch("marv.main.send_message", sender), mock.patch("marv.telegram.send_message", sender), \
                    mock.patch("marv.data.espn.ESPNClient") as espn, \
                    mock.patch("marv.data.espn.parse_event", return_value=_game()), \
                    mock.patch("marv.live_prop_engine.Runner._event", return_value={"id": "ev1"}), \
                    mock.patch("marv.live_prop_engine.Runner._odds", return_value=_live_odds(line=55.5)):
                espn.return_value.scoreboard.return_value = [{}]
                espn.return_value.summary.return_value = nfl_summary()
                self.assertEqual(main(["live-props", "scan", "--sport", "nfl"]), 0)
            self.assertEqual(sender.call_count, 0)
            self.assertTrue((d / "live_props" / "shadow.jsonl").exists())
            self.assertFalse((d / "live_props" / "recs.json").exists())


if __name__ == "__main__":
    unittest.main()
