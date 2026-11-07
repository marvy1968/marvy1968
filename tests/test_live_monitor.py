import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from marv.config import Settings
from marv.live_monitor import LiveMonitor, minutes_left, period_name


def event(state, name, period, clock, hs, as_, completed=False):
    return {"id": "401", "date": "2026-01-10T00:00Z",
            "status": {"period": period, "displayClock": clock,
                       "type": {"state": state, "name": name, "completed": completed}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "score": str(hs), "team": {"displayName": "Duke Blue Devils", "abbreviation": "DUKE"}},
                {"homeAway": "away", "score": str(as_), "team": {"displayName": "North Carolina Tar Heels", "abbreviation": "UNC"}}],
                "odds": [{"provider": {"name": "ESPN BET"}, "details": "DUKE -6.5", "overUnder": 150.5,
                          "homeTeamOdds": {"moneyLine": -260}, "awayTeamOdds": {"moneyLine": 210}}]}]}


class FakeClient:
    def __init__(self):
        self.events = []

    def scoreboard(self, path, start, end):
        return self.events


class LiveMonitorTest(unittest.TestCase):
    def test_clock_math(self):
        self.assertEqual(minutes_left("ncaab", 1, "0:00", True), 20)
        self.assertAlmostEqual(minutes_left("nfl", 3, "2:47", False), 17.7833, places=3)
        self.assertEqual(period_name("ncaab", 1), "1st half")
        self.assertEqual(period_name("wnba", 5), "OT")

    def _monitor(self, d, live_prices):
        (Path(d) / "predictions.json").write_text(json.dumps({"ncaab:1": {
            "sport": "ncaab", "game_id": "1", "start": datetime.now(timezone.utc).isoformat(),
            "home": "Duke Blue Devils", "away": "North Carolina Tar Heels", "home_exp": 78, "away_exp": 72,
            "model_total": 150.0, "model_margin": 6.0, "home_win": 0.68, "qualified": [], "notes": []}}))
        mon = LiveMonitor(Settings(state_dir=d), ["ncaab"])
        mon.client = FakeClient()
        mon._live_odds = lambda sport, games: live_prices  # the ESPN event carries the "live" line here
        return mon

    def test_alerts_only_new_edges_then_announces_wins(self):
        with tempfile.TemporaryDirectory() as d:
            mon, sent = self._monitor(d, True), []
            mon.client.events = [event("in", "STATUS_IN_PROGRESS", 1, "8:00", 20, 18)]
            self.assertEqual(mon.tick(sent.append), 0)  # mid-period: nothing
            mon.client.events = [event("in", "STATUS_HALFTIME", 1, "0:00", 41, 30)]
            self.assertEqual(mon.tick(sent.append), 1)
            self.assertIn("Marv the Martian predicts live: EDGE", sent[-1])
            self.assertIn("Duke Blue Devils ML -260", sent[-1])  # 95% live vs 72% priced
            self.assertIn("win probability", sent[-1])
            self.assertIn("Under 150.5", sent[-1])
            self.assertEqual(mon.tick(sent.append), 0)  # same edges: no repeat
            mon.client.events = [event("post", "STATUS_FINAL", 2, "0:00", 80, 71, completed=True)]
            self.assertEqual(mon.tick(sent.append), 1)
            self.assertIn("WIN", sent[-1])
            self.assertIn("Duke Blue Devils ML", sent[-1])
            self.assertNotIn("Under", sent[-1])  # 151 total: the under lost, logged but not announced
            from marv.live_monitor import live_record
            self.assertIn("1-1", live_record(Path(d)))

    def test_silent_without_live_prices(self):
        with tempfile.TemporaryDirectory() as d:
            mon, sent = self._monitor(d, False), []
            mon.client.events = [event("in", "STATUS_HALFTIME", 1, "0:00", 41, 30)]
            self.assertEqual(mon.tick(sent.append), 0)
            mon.client.events = [event("post", "STATUS_FINAL", 2, "0:00", 80, 71, completed=True)]
            self.assertEqual(mon.tick(sent.append), 0)
            self.assertEqual(sent, [])

class LivePropsRecordingTest(unittest.TestCase):
    def test_each_period_and_the_final_are_recorded_once(self):
        with tempfile.TemporaryDirectory() as d:
            mon = LiveMonitor(Settings(state_dir=d, odds_api_key="x"), ["nfl"])
            mon.client = FakeClient()
            mon._live_odds = lambda sport, games: False
            calls = []
            # The prop engine is on whenever an odds key is set. Keep this test off the network.
            with mock.patch("marv.live_props.record_period", lambda s, g, ended, c: calls.append(("p", ended))), \
                    mock.patch("marv.live_props.record_final", lambda s, g, c: calls.append(("final",))), \
                    mock.patch("marv.live_prop_engine.Runner._event", return_value=None), \
                    mock.patch("marv.telegram.requests.post", side_effect=AssertionError("telegram HTTP reachable")):
                mon.client.events = [event("in", "STATUS_END_PERIOD", 1, "0:00", 7, 0)]
                mon.tick(lambda m: None)
                mon.tick(lambda m: None)  # same quarter again: no second record
                mon.client.events = [event("in", "STATUS_HALFTIME", 2, "0:00", 14, 7)]
                mon.tick(lambda m: None)
                mon.client.events = [event("post", "STATUS_FINAL", 4, "0:00", 24, 20, completed=True)]
                mon.tick(lambda m: None)
                mon.tick(lambda m: None)
            self.assertEqual(calls, [("p", 1), ("p", 2), ("final",)])


class PropSendGateMonitorTest(unittest.TestCase):
    """The live-monitor entrypoint cannot deliver prop text while LIVE_PROPS_SEND is off.

    Moneyline / total alerts are a different callback and still send. On-mode prop delivery
    is asserted only against an in-memory sender; Telegram HTTP is patched to fail.
    """

    def _nfl(self, d):
        from tests.test_live_prop_engine import nfl_summary
        mon = LiveMonitor(Settings(state_dir=d, odds_api_key="x"), ["nfl"])
        mon.client = FakeClient()
        mon.client.summary = lambda *a, **k: nfl_summary()
        mon._live_odds = lambda sport, games: False
        mon.client.events = [event("in", "STATUS_END_PERIOD", 1, "0:00", 7, 0)]
        return mon

    def _quiet_log(self):
        return [
            mock.patch("marv.live_props.record_period", lambda *a, **k: None),
            mock.patch("marv.live_props.record_final", lambda *a, **k: None),
            mock.patch("marv.telegram.requests.post", side_effect=AssertionError("telegram HTTP reachable")),
        ]

    def test_buggy_engine_cannot_reach_sender_and_dedup_does_not_retry(self):
        with tempfile.TemporaryDirectory() as d:
            mon, sent, calls = self._nfl(d), [], []

            def leak(self, sport, game, elapsed, label, mem, send, path):
                calls.append(label)
                send("PROP LEAK")
                return 1

            patches = self._quiet_log() + [mock.patch("marv.live_prop_engine.Runner.on_period", leak)]
            with mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "off", "LIVE_PROPS_ENGINE": "on"}):
                for p in patches:
                    p.start()
                try:
                    mon.tick(sent.append)
                    mon.tick(sent.append)  # same break: deduped, engine not called again
                finally:
                    for p in patches:
                        p.stop()
            self.assertEqual(calls, ["end of Q1"])
            self.assertFalse(any("PROP LEAK" in m for m in sent))
            self.assertFalse((Path(d) / "live_props" / "recs.json").exists())
            self.assertFalse((Path(d) / "picks.json").exists())

    def test_real_engine_off_tick_writes_shadow_not_recs_and_ml_still_sends_when_send_off(self):
        from tests.test_live_prop_engine import _changed, _files, _live_odds
        with tempfile.TemporaryDirectory() as d:
            # ncaab halftime still announces a live edge; prop send is off and must not add prop text.
            (Path(d) / "predictions.json").write_text(json.dumps({"ncaab:1": {
                "sport": "ncaab", "game_id": "1", "start": datetime.now(timezone.utc).isoformat(),
                "home": "Duke Blue Devils", "away": "North Carolina Tar Heels", "home_exp": 78, "away_exp": 72,
                "model_total": 150.0, "model_margin": 6.0, "home_win": 0.68, "qualified": [], "notes": []}}))
            mon = LiveMonitor(Settings(state_dir=d, odds_api_key="x"), ["ncaab"])
            mon.client = FakeClient()
            mon._live_odds = lambda sport, games: True
            mon.client.events = [event("in", "STATUS_HALFTIME", 1, "0:00", 41, 30)]
            sent = []

            def leak(self, sport, game, elapsed, label, mem, send, path):
                send("PROP LEAK")
                return 1

            with mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "off", "LIVE_PROPS_ENGINE": "on"}), \
                    mock.patch("marv.live_prop_engine.Runner.on_period", leak), \
                    mock.patch("marv.telegram.requests.post", side_effect=AssertionError("telegram HTTP reachable")):
                self.assertGreaterEqual(mon.tick(sent.append), 1)
            self.assertTrue(any("EDGE" in m for m in sent))
            self.assertFalse(any("PROP LEAK" in m for m in sent))

            # A real (mocked-network) NFL break appends shadow.jsonl and does not touch the ledger.
            mon = self._nfl(d)
            (Path(d) / "props_priced_nfl.json").write_text(
                '[{"player": "CeeDee Lamb", "market": "player_reception_yds", "proj": 70.0}]')
            (Path(d) / "picks.json").write_bytes(b"NOT-JSON-PICKS\n")
            (Path(d) / "live_props").mkdir(exist_ok=True)
            (Path(d) / "live_props" / "recs.json").write_bytes(b"NOT-JSON-SENTINEL\n")
            sent = []
            patches = self._quiet_log() + [
                mock.patch("marv.live_prop_engine.Runner._event", return_value={"id": "ev1"}),
                mock.patch("marv.live_prop_engine.Runner._odds", return_value=_live_odds(line=55.5)),
            ]
            log_calls = []

            def refuse_log(*a, **k):
                log_calls.append(a)
                raise AssertionError("stage-1 prop log reached while LIVE_PROPS_SEND is off")

            patches = [
                mock.patch("marv.live_props.record_period", refuse_log),
                mock.patch("marv.live_props.record_final", refuse_log),
                mock.patch("marv.telegram.requests.post", side_effect=AssertionError("telegram HTTP reachable")),
                mock.patch("marv.live_prop_engine.Runner._event", return_value={"id": "ev1"}),
                mock.patch("marv.live_prop_engine.Runner._odds", return_value=_live_odds(line=55.5)),
            ]
            flags = {"LIVE_PROPS_SEND": "off", "LIVE_PROPS_ENGINE": "on", "LIVE_PROPS_LOG": "on"}
            before = _files(Path(d))
            with mock.patch.dict(os.environ, flags):
                for p in patches:
                    p.start()
                try:
                    mon.tick(sent.append)
                    mon.tick(sent.append)  # in-memory dedup: second tick does not scan again
                finally:
                    for p in patches:
                        p.stop()
            self.assertEqual(sent, [])
            self.assertEqual(log_calls, [])
            changed = _changed(before, _files(Path(d)))
            # live_state.json is the ML/total book. It is rewritten every tick and must not gain prop keys.
            self.assertEqual(changed, {"live_props/shadow.jsonl", "live_props/credits.json", "live_state.json"})
            shadow = (Path(d) / "live_props" / "shadow.jsonl").read_text().strip()
            self.assertIn("CeeDee Lamb", shadow)
            self.assertFalse((Path(d) / "live_props" / "pregame_lines.json").exists())
            self.assertFalse((Path(d) / "live_props_log.json").exists())
            self.assertEqual((Path(d) / "live_props" / "recs.json").read_bytes(), b"NOT-JSON-SENTINEL\n")
            self.assertEqual((Path(d) / "picks.json").read_bytes(), b"NOT-JSON-PICKS\n")
            state = json.loads((Path(d) / "live_state.json").read_text())
            blob = json.dumps(state)
            for banned in ("prop_recs", "prop_scanned", "prop_usage", "prop_pregame", "props_recorded"):
                self.assertNotIn(banned, blob)
            self.assertIn("reported", blob)

    def test_engine_error_does_not_send_or_mutate_ledger(self):
        from tests.test_live_prop_engine import _changed, _files
        with tempfile.TemporaryDirectory() as d:
            mon = self._nfl(d)
            (Path(d) / "live_props").mkdir(parents=True)
            (Path(d) / "live_props" / "recs.json").write_bytes(b"NOT-JSON-SENTINEL\n")
            sent, log_calls = [], []

            def refuse_log(*a, **k):
                log_calls.append(a)
                raise AssertionError("stage-1 prop log reached while LIVE_PROPS_SEND is off")

            event = mock.Mock(side_effect=RuntimeError("odds down"))
            patches = [
                mock.patch("marv.live_props.record_period", refuse_log),
                mock.patch("marv.live_props.record_final", refuse_log),
                mock.patch("marv.telegram.requests.post", side_effect=AssertionError("telegram HTTP reachable")),
                mock.patch("marv.live_prop_engine.Runner._event", event),
            ]
            before = _files(Path(d))
            flags = {"LIVE_PROPS_SEND": "off", "LIVE_PROPS_ENGINE": "on", "LIVE_PROPS_LOG": "on"}
            with mock.patch.dict(os.environ, flags):
                for p in patches:
                    p.start()
                try:
                    mon.tick(sent.append)
                    mon.tick(sent.append)  # in-memory dedup: the failed break is not fetched again
                finally:
                    for p in patches:
                        p.stop()
            self.assertEqual(sent, [])
            self.assertEqual(log_calls, [])
            self.assertEqual(event.call_count, 1)
            changed = _changed(before, _files(Path(d)))
            self.assertEqual(changed, {"live_props/credits.json", "live_state.json"})
            self.assertEqual((Path(d) / "live_props" / "recs.json").read_bytes(), b"NOT-JSON-SENTINEL\n")
            self.assertFalse((Path(d) / "live_props" / "shadow.jsonl").exists())
            self.assertFalse((Path(d) / "live_props_log.json").exists())
            blob = (Path(d) / "live_state.json").read_text()
            for banned in ("prop_recs", "prop_scanned", "prop_usage", "prop_pregame", "props_recorded"):
                self.assertNotIn(banned, blob)

    def test_off_pregame_capture_does_not_persist_anchors_or_prop_state(self):
        from datetime import timedelta
        from tests.test_live_prop_engine import _changed, _files, _live_odds
        with tempfile.TemporaryDirectory() as d:
            mon = LiveMonitor(Settings(state_dir=d, odds_api_key="x"), ["nfl"])
            mon.client = FakeClient()
            mon._live_odds = lambda sport, games: False
            start = (datetime.now(timezone.utc) + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
            pre = event("pre", "STATUS_SCHEDULED", 1, "15:00", 0, 0)
            pre["date"] = start
            mon.client.events = [pre]
            log_calls = []

            def refuse_log(*a, **k):
                log_calls.append(1)
                raise AssertionError("stage-1 prop log reached")

            flags = {"LIVE_PROPS_SEND": "off", "LIVE_PROPS_ENGINE": "on", "LIVE_PROPS_LOG": "on"}
            before = _files(Path(d))
            with mock.patch.dict(os.environ, flags), \
                    mock.patch("marv.live_props.record_period", refuse_log), \
                    mock.patch("marv.live_props.record_final", refuse_log), \
                    mock.patch("marv.telegram.requests.post", side_effect=AssertionError("telegram HTTP reachable")), \
                    mock.patch("marv.live_prop_engine.Runner._event", return_value={
                        "id": "ev1", "away_team": "North Carolina Tar Heels", "home_team": "Duke Blue Devils",
                        "commence_time": start}), \
                    mock.patch("marv.live_prop_engine.Runner._odds", return_value=_live_odds(line=55.5)):
                mon.tick(lambda text: (_ for _ in ()).throw(AssertionError("sender")))
                mon.tick(lambda text: (_ for _ in ()).throw(AssertionError("sender")))
            self.assertEqual(log_calls, [])
            changed = _changed(before, _files(Path(d)))
            self.assertEqual(changed, {"live_props/credits.json", "live_state.json"})
            self.assertFalse((Path(d) / "live_props" / "pregame_lines.json").exists())
            self.assertFalse((Path(d) / "live_props" / "shadow.jsonl").exists())
            self.assertFalse((Path(d) / "live_props_log.json").exists())
            blob = (Path(d) / "live_state.json").read_text()
            for banned in ("prop_pregame", "prop_recs", "prop_scanned", "prop_usage", "props_recorded"):
                self.assertNotIn(banned, blob)
            self.assertEqual(len(mon._shadow_pregame), 1)


if __name__ == "__main__":
    unittest.main()
