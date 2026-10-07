import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from marv import bridge
from marv.models import Game, Prediction


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.state = Path(self.dir.name)
        g = Game("x1", "nfl", datetime.now(timezone.utc), "Carolina Panthers", "Detroit Lions")
        bridge.export(self.state, "nfl", [Prediction(g, 22.0, 25.5, -3.0, 47.5, 0.39)])

    def tearDown(self):
        self.dir.cleanup()

    def test_live_moneyline_blocks_bad_longshot(self):
        v = bridge.check(self.state, "nfl", "Lions", "ml", "Lions", 360, home_score=29, away_score=19, minutes_left=14.9)
        self.assertTrue(v.found)
        self.assertFalse(v.agrees)
        self.assertLess(v.fair_prob, 0.15)

    def test_pregame_total_and_matching(self):
        over = bridge.check(self.state, "nfl", "Detroit", "total", "over", -110, line=40.5)
        under = bridge.check(self.state, "nfl", "Detroit", "total", "under", -110, line=40.5)
        self.assertAlmostEqual(over.fair_prob + under.fair_prob, 1.0)
        self.assertTrue(over.agrees)
        self.assertFalse(bridge.check(self.state, "nba", "Lions", "ml", "Lions", 100).found)

    def test_game_over_is_certain(self):
        v = bridge.check(self.state, "nfl", "Lions", "total", "under", -110, line=60.5,
                         home_score=30, away_score=20, minutes_left=0)
        self.assertGreater(v.fair_prob, 0.99)


if __name__ == "__main__":
    unittest.main()
