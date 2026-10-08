import unittest

import numpy as np
import pandas as pd

from marv.ingame import model as M
from marv.ingame import plays as PL


def football_summary():
    comp = {"competitors": [{"homeAway": "home", "team": {"id": "1"}}, {"homeAway": "away", "team": {"id": "2"}}]}
    def play(period, kind, yards, down=1, dist=10, home=0, away=0, text=""):
        return {"type": {"text": kind}, "period": {"number": period}, "statYardage": yards,
                "start": {"down": down, "distance": dist}, "homeScore": home, "awayScore": away, "text": text}
    return {"header": {"competitions": [comp]}, "drives": {"previous": [
        {"team": {"id": "1"}, "plays": [play(1, "Rush", 5), play(1, "Pass Reception", 25, 2, 5, 7, 0), play(1, "Kickoff", 60)]},
        {"team": {"id": "2"}, "plays": [play(1, "Pass Interception Return", 0, 3, 8, 7, 0, "intercepted"),
                                        play(2, "Sack", -7, 1, 10, 7, 0)]}]}}


class InGameTests(unittest.TestCase):
    def test_espn_football_plays_and_quarter_stats(self):
        plays = PL.espn_football(football_summary(), "g1")
        self.assertEqual(len(plays), 4)  # the kickoff is not a scrimmage play
        st = PL.football_states(plays, (1, 2)).set_index("checkpoint")
        q1 = st.loc[1]
        self.assertEqual((q1.h_plays, q1.h_yards, q1.h_explosive, q1.a_turnover), (2, 30, 1, 1))
        self.assertAlmostEqual(q1.h_success_rate, 1.0)  # 5 of 10 on 1st down (>=40%), 25 of 5 on 2nd down
        self.assertEqual((q1.h_score, q1.a_score), (7, 0))
        self.assertEqual(st.loc[2].a_sacks, 1)

    def test_espn_basketball_plays(self):
        summary = {"header": {"competitions": [{"competitors": [{"homeAway": "home", "team": {"id": "9"}}]}]},
                   "plays": [{"team": {"id": "9"}, "period": {"number": 1}, "type": {"text": "Jump Shot"},
                              "text": "makes 24-foot three point jumper", "shootingPlay": True, "scoringPlay": True,
                              "homeScore": 3, "awayScore": 0},
                             {"team": {"id": "4"}, "period": {"number": 1}, "type": {"text": "Defensive Rebound"},
                              "text": "", "shootingPlay": False, "scoringPlay": False, "homeScore": 3, "awayScore": 0},
                             {"team": {"id": "4"}, "period": {"number": 1}, "type": {"text": "Free Throw - 1 of 2"},
                              "text": "", "shootingPlay": True, "scoringPlay": True, "homeScore": 3, "awayScore": 1}]}
        st = PL.basketball_states(PL.espn_basketball(summary, "b1"), (1,)).iloc[0]
        self.assertEqual((st.h_fg3a, st.h_fg3m, st.a_dreb, st.a_fta, st.a_ftm, st.h_score, st.a_score), (1, 1, 1, 1, 1, 3, 1))

    def test_model_learns_from_game_stats(self):
        rng = np.random.default_rng(0)
        n = 1500
        d = pd.DataFrame({"checkpoint": 2, "pre_spread": rng.normal(0, 5, n), "pre_total": 45.0,
                          "h_score": rng.integers(0, 28, n).astype(float), "a_score": rng.integers(0, 28, n).astype(float),
                          "h_ypp": rng.normal(5.5, 1, n), "a_ypp": rng.normal(5.5, 1, n)})
        d["d_ypp"] = d.h_ypp - d.a_ypp
        d = M.add_score_cols(d)
        d["final_h"] = d.h_score + 10 + 4 * (d.h_ypp - 5.5) + rng.normal(0, 4, n)
        d["final_a"] = d.a_score + 10 + 4 * (d.a_ypp - 5.5) + rng.normal(0, 4, n)
        m = M.InGameModel("nfl").fit(d.iloc[:1200])
        out = m.predict(d.iloc[1200:])
        win = (out.final_h > out.final_a).astype(float)
        self.assertGreater(((out.p_home >= .5).astype(float) == win).mean(), 0.8)
        p = m.p_over(out, out.exp_total.to_numpy() - 20)
        self.assertGreater(np.nanmean(p), 0.9)


if __name__ == "__main__":
    unittest.main()
