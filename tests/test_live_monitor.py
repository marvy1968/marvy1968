import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

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

if __name__ == "__main__":
    unittest.main()
