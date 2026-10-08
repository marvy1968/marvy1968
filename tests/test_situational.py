import tempfile
import unittest
from pathlib import Path

import pandas as pd

from marv import situational as S


def games():
    rows = [  # week 1 prime-time blowout by KC; week 2 KC on the road at a home dog; a West Coast early game
        dict(game_id="w1", season=2026, week=1, gameday="2026-09-10", weekday="Thursday", gametime="20:20", home_team="KC",
             away_team="BAL", result=24, spread_line=3.0, home_rest=7, away_rest=7, location="Home", div_game=0),
        dict(game_id="w2", season=2026, week=2, gameday="2026-09-20", weekday="Sunday", gametime="13:00", home_team="NYJ",
             away_team="KC", result=None, spread_line=-6.5, home_rest=7, away_rest=10, location="Home", div_game=0),
        dict(game_id="w3", season=2026, week=2, gameday="2026-09-20", weekday="Sunday", gametime="13:00", home_team="MIA",
             away_team="SEA", result=None, spread_line=3.0, home_rest=11, away_rest=7, location="Home", div_game=0),
    ]
    return pd.DataFrame(rows)


class SituationalTests(unittest.TestCase):
    def test_tags_and_grading(self):
        t = S.tags_for(games())
        got = {(r.game_id, r.tag, r.side) for r in t.itertuples()}
        self.assertIn(("w2", "PT-WIN", "KC"), got)        # KC beat the number by 21 in prime time
        self.assertIn(("w2", "HOMEDOG", "NYJ"), got)
        self.assertIn(("w3", "WEST@1PM", "MIA"), got)
        self.assertIn(("w3", "REST+4", "MIA"), got)
        self.assertIn(("w3", "REST+TZ", "MIA"), got)
        self.assertIn(("w3", "KEY3", "SEA"), got)
        with tempfile.TemporaryDirectory() as d:
            S.log(Path(d), t)
            done = games().assign(result=[24, -10, 7])  # KC wins by 10 (covers 6.5); MIA wins by 7 (covers 3)
            rec = S.grade(Path(d), done)
            self.assertEqual(rec["PT-WIN"], [1, 0])
            self.assertEqual(rec["HOMEDOG"], [0, 1])
            self.assertEqual(rec["WEST@1PM"], [1, 0])
            self.assertEqual(rec["KEY3"], [0, 1])
            self.assertIn("PT-WIN: 1-0", S.report(rec))


if __name__ == "__main__":
    unittest.main()
