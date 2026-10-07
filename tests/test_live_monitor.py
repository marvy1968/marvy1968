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

    def test_halftime_update_then_final(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "predictions.json").write_text(json.dumps({"ncaab:1": {
                "sport": "ncaab", "game_id": "1", "start": datetime.now(timezone.utc).isoformat(),
                "home": "Duke Blue Devils", "away": "North Carolina Tar Heels", "home_exp": 78, "away_exp": 72,
                "model_total": 150.0, "model_margin": 6.0, "home_win": 0.68, "qualified": [], "notes": []}}))
            mon = LiveMonitor(Settings(state_dir=d), ["ncaab"])
            mon.client = FakeClient()
            sent = []
            mon.client.events = [event("in", "STATUS_IN_PROGRESS", 1, "8:00", 20, 18)]
            self.assertEqual(mon.tick(sent.append), 0)
            mon.client.events = [event("in", "STATUS_HALFTIME", 1, "0:00", 41, 30)]
            self.assertEqual(mon.tick(sent.append), 1)
            self.assertIn("End of 1st half", sent[-1])
            self.assertIn("Duke Blue Devils win", sent[-1])
            self.assertIn("Total 150.5", sent[-1])
            self.assertEqual(mon.tick(sent.append), 0)  # no repeat for the same half
            mon.client.events = [event("post", "STATUS_FINAL", 2, "0:00", 80, 71, completed=True)]
            self.assertEqual(mon.tick(sent.append), 1)
            self.assertIn("FINAL", sent[-1])
            self.assertIn("✅", sent[-1])


if __name__ == "__main__":
    unittest.main()
