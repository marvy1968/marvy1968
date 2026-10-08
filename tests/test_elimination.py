import unittest
from datetime import datetime, timezone

from marv.models import Game, Odds, Pick, Prediction
from marv.stats.live import StatsProjection, StatsRules
from marv.stats.registry import RULES


def setup(odds=None, availability=None, wind=None, rules=None):
    g = Game("g", "nfl", datetime(2026, 10, 11, tzinfo=timezone.utc), "Buffalo Bills", "Miami Dolphins", odds=odds)
    pred = Prediction(g, 28, 17, 11, 45, 0.85)
    proj = StatsProjection(28, 17, {"formula": (28, 17), "forest": (27, 18), "ratings": (29, 16)},
                           rules or RULES["nfl"], min_gp=5)
    proj.availability, proj.wind = availability or {}, wind
    pick = Pick("ml", "Buffalo Bills", -500, -500, 0.85, 0.02, 0.02)
    return proj.vetoes_for(pick, pred)


class EliminationTests(unittest.TestCase):
    def test_clean_pick_passes(self):
        self.assertEqual(setup(Odds(home_ml=-500, away_ml=380), {"Buffalo Bills": 0.4}, wind=8), [])

    def test_each_elimination(self):
        self.assertTrue(any("market only" in v for v in setup(Odds(home_ml=-150, away_ml=130))))
        self.assertTrue(any("starters out" in v for v in setup(availability={"Buffalo Bills": 2.2})))
        self.assertTrue(any("wind" in v for v in setup(wind=22)))
        bb = StatsRules(ml_min_prob=0.8, ml_max_missing=0.15)
        self.assertTrue(any("regular minutes" in v for v in setup(availability={"Buffalo Bills": 0.3}, rules=bb)))
        self.assertFalse(any("regular minutes" in v for v in setup(availability={"Buffalo Bills": 0.05}, rules=bb)))
        road = StatsRules(ml_no_road_fav=True)
        self.assertEqual(setup(rules=road), [])  # home favorite is fine


if __name__ == "__main__":
    unittest.main()
