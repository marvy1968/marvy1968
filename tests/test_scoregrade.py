import unittest
from datetime import datetime, timezone
from unittest import mock

import numpy as np
import pandas as pd

from marv import ou_tags, scoregrade
from marv.models import Game, Odds


def synthetic(seasons=(2024, 2025, 2026), teams=12, weeks=8, seed=0):
    rng = np.random.default_rng(seed)
    strength = {f"T{i}": rng.normal() for i in range(teams)}
    rows, gid = [], 0
    for s in seasons:
        for w in range(weeks):
            order = rng.permutation(teams)
            for k in range(0, teams, 2):
                a, b = f"T{order[k]}", f"T{order[k + 1]}"
                date = pd.Timestamp(f"{s}-09-{1 + w * 3:02d}")
                gid += 1
                upcoming = s == 2026 and w == weeks - 1
                for team, opp in ((a, b), (b, a)):
                    ypp = 5.5 + 0.4 * strength[team] - 0.3 * strength[opp] + rng.normal(0, 0.2)
                    pts = 28 + 5 * strength[team] - 4 * strength[opp] + rng.normal(0, 7)
                    rows.append({"game_id": str(gid), "team": team, "opp": opp, "date": date, "season": s,
                                 "points": np.nan if upcoming else pts, "home": 1.0 if team == a else 0.0,
                                 "ypp": np.nan if upcoming else ypp, "epa_play": np.nan if upcoming else 0.1 * strength[team] + rng.normal(0, 0.05),
                                 "success_rate": np.nan if upcoming else 0.45 + 0.03 * strength[team] + rng.normal(0, 0.02)})
    return pd.DataFrame(rows)


class ScoreGradeTests(unittest.TestCase):
    def test_grades_cover_upcoming_games_and_stay_in_range(self):
        tg = synthetic()
        # the min training size is for real seasons; shrink it for this tiny table
        with mock.patch.object(scoregrade, "MIN_TRAIN_ROWS", 20):
            grades = scoregrade.game_grades(tg[["game_id", "date", "season"]].drop_duplicates("game_id"), tg, 2026)
        upcoming = tg[(tg.season == 2026) & tg.ypp.isna()].game_id.unique()
        self.assertTrue(set(upcoming) <= set(grades))
        self.assertTrue(all(0 <= v <= 100 for v in grades.values()))

    def test_grade_tag_added_at_threshold_only(self):
        games = pd.DataFrame({"game_id": ["g1", "g2"], "date": ["2026-10-10"] * 2, "season": [2026, 2026],
                              "home": ["A", "C"], "away": ["B", "D"], "total": [55.0, 55.0]})
        tg = pd.DataFrame({"game_id": ["g1", "g1", "g2", "g2"], "team": ["A", "B", "C", "D"], "opp": ["B", "A", "D", "C"],
                           "points": [np.nan] * 4, "yards": [np.nan] * 4})
        mk = lambda gid, h, a: Game(gid, "cfb", datetime(2026, 10, 10, tzinfo=timezone.utc), h, a,
                                    odds=Odds(total=55.0, over_price=-110, under_price=-110), info={"home_fbs": True, "away_fbs": True})
        slate = [mk("s1", "A", "B"), mk("s2", "C", "D")]
        with mock.patch("marv.scoregrade.game_grades", return_value={"g1": 61.0, "g2": 58.0}):
            tags = ou_tags.tag_slate("cfb", games, tg, slate, {"s1": "g1", "s2": "g2"})
        self.assertIn(("SCORE-GRADE", "Under"), [t for t in tags.get("s1", [])])
        self.assertNotIn("s2", tags)


if __name__ == "__main__":
    unittest.main()
