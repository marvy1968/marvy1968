import unittest

import numpy as np
import pandas as pd

from marv.stats import h2h
from marv.stats.basketball import WNBA


def synthetic(seasons=(2020, 2021, 2022), teams=8, seed=0):
    """Round-robin seasons where a team's true strength drives both its stats and its results."""
    rng = np.random.default_rng(seed)
    strength = {f"T{i}": 3 * (i - teams / 2) for i in range(teams)}
    games, rows, gid = [], [], 0
    for season in seasons:
        day = pd.Timestamp(f"{season}-05-01")
        for rnd in range(3):
            for i in range(teams):
                for j in range(i + 1, teams):
                    home, away = f"T{i}", f"T{j}"
                    hp = 80 + strength[home] + 2 + rng.normal(0, 6)
                    ap = 80 + strength[away] + rng.normal(0, 6)
                    gid += 1
                    games.append({"game_id": str(gid), "date": day, "season": season, "home": home, "away": away,
                                  "neutral": False, "home_points": hp, "away_points": ap,
                                  "spread": -(strength[home] - strength[away] + 2), "total": 160.0})
                    for team, opp, pts, h in ((home, away, hp, 1.0), (away, home, ap, 0.0)):
                        rows.append({"game_id": str(gid), "date": day, "season": season, "team": team, "opp": opp,
                                     "home": h, "points": pts, "ypp": 5 + strength[team] / 4 + rng.normal(0, .3),
                                     "turnovers": 12 - strength[team] / 2 + rng.normal(0, 1)})
                    day += pd.Timedelta(days=1)
    return pd.DataFrame(games), pd.DataFrame(rows)


class H2HTests(unittest.TestCase):
    def test_features_use_only_earlier_games(self):
        games, tg = synthetic(seasons=(2020,))
        f = h2h.team_features(tg, ["ypp"]).set_index(["game_id", "team"])
        team = tg[tg["team"] == "T0"].sort_values("date")
        third = team.iloc[2]
        expect = team.iloc[:2]["ypp"].mean()
        self.assertAlmostEqual(f.loc[(third.game_id, "T0"), "s_ypp"], expect)
        self.assertTrue(np.isnan(f.loc[(team.iloc[0].game_id, "T0"), "s_ypp"]))

    def test_learns_directions_and_beats_coin_flip(self):
        games, tg = synthetic()
        df = h2h.walk_forward(WNBA, games, tg, [2021, 2022], n=300)
        model = h2h.fit(h2h.matchups(games, h2h.team_features(tg, ["ypp", "turnovers"])))
        self.assertEqual(model.direction["ypp"], 1)
        self.assertEqual(model.direction["turnovers"], -1)
        self.assertEqual(model.direction["alw_ypp"], -1)
        o = h2h.outcomes(df)
        self.assertGreater((np.where(df["p_home"] >= .5, 1., 0.) == o["ml"]).mean(), 0.7)
        self.assertIn("Moneyline, every game", h2h.report(df, 2021))


class WeightedH2HTests(unittest.TestCase):
    def test_weighted_modes_learn_which_stat_matters(self):
        games, tg = synthetic()
        tg["noise"] = np.random.default_rng(3).normal(0, 1, len(tg))  # a stat with no signal
        m = h2h.matchups(games, h2h.team_features(tg, ["ypp", "turnovers", "noise"]))
        for mode in ("weighted", "magnitude"):
            model = h2h.fit(m, mode)
            w = h2h.stat_weights(model)
            self.assertGreater(abs(w["ypp"]), abs(w["noise"]))
            out = h2h.predict(m.dropna(subset=["h_s_ypp", "a_s_ypp"]).head(50), model, WNBA, n=200)
            self.assertTrue(out["p_home"].between(0, 1).all())

    def test_breakdown_points_add_up_to_the_margin(self):
        games, tg = synthetic()
        m = h2h.matchups(games, h2h.team_features(tg, ["ypp", "turnovers"])).dropna(subset=["h_s_ypp", "a_s_ypp"])
        model = h2h.fit(m, "magnitude")
        b = h2h.breakdown(m, model, i=5, top=100)
        margin = h2h.predict(m.iloc[[5]], model, WNBA, n=50)["h2h_margin"].iloc[0]
        self.assertAlmostEqual(b["points"].sum() + model.weights.intercept_, margin, places=6)


class OpponentAdjustTests(unittest.TestCase):
    def test_adjusts_for_the_opponents_defense(self):
        tg = pd.DataFrame({"game_id": ["1", "1", "2", "2", "3", "3"],
                           "date": pd.to_datetime(["2021-01-01"] * 2 + ["2021-01-02"] * 2 + ["2021-01-03"] * 2),
                           "season": 2021, "team": ["A", "B", "C", "B", "A", "C"], "opp": ["B", "A", "B", "C", "C", "A"],
                           "home": [1.0, 0.0, 1.0, 0.0, 1.0, 0.0], "points": [20.0, 10, 30, 10, 20, 20],
                           "ypp": [7.0, 5.0, 7.0, 5.0, 6.0, 6.0]})
        adj = h2h.opponent_adjust(tg, ["ypp"]).set_index(["game_id", "team"])
        # Game 3: A plays C. Before it C allowed 5.0 yards/play vs a league average of 6.0, so A's 6.0 is worth 7.0.
        self.assertAlmostEqual(adj.loc[("3", "A"), "ypp"], 7.0)
        # Game 1 has no earlier games and no prior season, so nothing changes.
        self.assertAlmostEqual(adj.loc[("1", "A"), "ypp"], 7.0)
        self.assertAlmostEqual(adj.loc[("1", "A"), "alw_ypp"], 5.0)


if __name__ == "__main__":
    unittest.main()
