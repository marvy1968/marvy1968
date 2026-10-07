import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from marv.models import Game, Pick, Prediction
from marv.ratings import RatingParams
from marv.stats.backtest import PickRules, accuracy_by_confidence, score, to_games
from marv.stats.experts import ExpertConfig, ExpertPanel
from marv.stats.features import build_features, feature_columns
from marv.stats.live import StatsProjection, StatsRules, add_upcoming
from marv.stats.mlb import parse_gamelog
from marv.stats.ncaaf import parse_stat, team_box_rows


def toy_league(n_teams=8, days=60, seed=0):
    rng = np.random.default_rng(seed)
    teams = [f"T{i}" for i in range(n_teams)]
    skill = dict(zip(teams, rng.normal(0, 1, n_teams)))
    rows, games = [], []
    for d in range(days):
        order = rng.permutation(teams)
        for i, (h, a) in enumerate(zip(order[::2], order[1::2])):
            gid = f"{d}-{i}"
            date = pd.Timestamp("2025-01-01") + pd.Timedelta(days=d)
            hy, ay = 300 + 150 * skill[h] + rng.normal(0, 20), 300 + 150 * skill[a] + rng.normal(0, 20)
            hp, ap = round(20 + hy / 40 + rng.normal(0, 3)), round(20 + ay / 40 + rng.normal(0, 3))
            games.append({"game_id": gid, "date": date, "season": 2025, "home": h, "away": a, "neutral": False,
                          "home_points": hp, "away_points": ap, "spread": -2.5, "total": 60.5,
                          "home_ml": -140, "away_ml": 120})
            rows.append({"game_id": gid, "date": date, "season": 2025, "team": h, "opp": a, "home": 1.0,
                         "points": hp, "yards": hy})
            rows.append({"game_id": gid, "date": date, "season": 2025, "team": a, "opp": h, "home": 0.0,
                         "points": ap, "yards": ay})
    return pd.DataFrame(games), pd.DataFrame(rows)


class FeatureTest(unittest.TestCase):
    def test_no_leakage(self):
        games, tg = toy_league()
        m = build_features(tg, ["yards"], halflife=5)
        first = m.sort_values("date").groupby("team").head(1)
        self.assertTrue(first["off_points"].isna().all(), "first game must have no prior stats")
        # Changing a game's own result must not change its features.
        tg2 = tg.copy()
        tg2.loc[tg2["game_id"] == "30-0", ["points", "yards"]] = 999
        m2 = build_features(tg2, ["yards"], halflife=5)
        a = m[m.game_id == "30-0"].sort_values("team")["off_yards"].to_numpy()
        b = m2[m2.game_id == "30-0"].sort_values("team")["off_yards"].to_numpy()
        np.testing.assert_allclose(a, b)

    def test_opponent_profile_attached(self):
        games, tg = toy_league()
        m = build_features(tg, ["yards"], halflife=5)
        row = m[(m.game_id == "40-0")].iloc[0]
        opp = m[(m.game_id == "40-0") & (m.team == row.opp)].iloc[0]
        self.assertAlmostEqual(row["opp_alw_yards"], opp["alw_yards"])


class ExpertTest(unittest.TestCase):
    def test_panel_and_scoring(self):
        games, tg = toy_league(days=80)
        m = build_features(tg, ["yards"], halflife=5)
        cols = [c for c in feature_columns(m) if not c.startswith(("mx_", "mxd_"))]
        cut = pd.Timestamp("2025-03-01")
        hist = [Game(r.game_id, "x", r.date.to_pydatetime(), r.home, r.away, completed=True,
                     home_score=r.home_points, away_score=r.away_points)
                for r in games[games.date < cut].itertuples()]
        panel = ExpertPanel(ExpertConfig(rf_trees=30, min_train_rows=50), cols,
                            RatingParams(multiplicative=False, home_adv=0.0)).fit(m[m.date < cut], hist, cut)
        test = m[m.date >= cut]
        proj = panel.project(test)
        self.assertEqual(len(proj), len(test))
        rows = pd.concat([test[["game_id", "date", "season", "team", "home", "points", "gp"]], proj], axis=1)
        g = to_games(rows, games.set_index("game_id"))
        g["p_home"] = 1 / (1 + np.exp(-(g["h_consensus"] - g["a_consensus"]) / 4))
        g["p_over"] = 0.6
        res = score(g, PickRules())
        self.assertGreater(res["ml_acc"], 0.6)  # skill is real in the toy league
        self.assertIn("ou_acc", res)
        self.assertFalse(accuracy_by_confidence(g).empty)


class LiveTest(unittest.TestCase):
    def test_add_upcoming_and_vetoes(self):
        games, tg = toy_league(days=5)
        slate = [Game("x1", "nba", datetime(2025, 1, 20, tzinfo=timezone.utc), "T1", "T2")]
        g2, tg2, ids = add_upcoming(games, tg, slate)
        self.assertEqual(ids["x1"], "live-x1")
        self.assertEqual(len(tg2) - len(tg), 2)
        p = StatsProjection(24, 20, {"formula": (25, 20), "forest": (19, 21), "ratings": (24, 20)},
                            StatsRules(ml_min_prob=0.6), min_gp=10)
        pred = Prediction(slate[0], 24, 20, 4, 44, 0.62)
        v = p.vetoes_for(Pick("ml", "T1", -150, -150, 0.62, 0.05, 0.03), pred)
        self.assertIn("experts split on the winner", v)
        v = p.vetoes_for(Pick("total", "Under", 47.5, -110, 0.58, 0.05, 0.03), pred)
        self.assertEqual(v, [])


class ParserTest(unittest.TestCase):
    def test_cfbd_stats(self):
        self.assertEqual(parse_stat("thirdDownEff", "5-12")["thirdDownEff_pct"], 5 / 12)
        self.assertAlmostEqual(parse_stat("possessionTime", "31:30")["possessionTime"], 31.5)
        self.assertEqual(parse_stat("totalYards", "412"), {"totalYards": 412.0})
        rows = team_box_rows([{"id": 1, "teams": [{"team": "Georgia", "points": 30,
                                                     "stats": [{"category": "turnovers", "stat": "2"}]}]}])
        self.assertEqual(rows[0]["turnovers"], 2.0)

    def test_retrosheet_line(self):
        import tempfile
        from pathlib import Path
        line = ('"20230330","0","Thu","MIL","NL",1,"CHN","NL",1,0,4,51,"D","","","","CHI11",36054,141,'
                '"000000000","00400000x",29,4,0,0,0,0,0,0,0,5,0,12,0,0,2,0,7,4,4,4,0,0,24,12,1,0,1,0,30,6,0,0,0,3,0,0,1,4,0,5,0,0,1,0,7,4,0,0,1,0,27,13,1,2,2,0,'
                + ",".join(['""'] * 12) + ',"m1","A","m2","B","w","W","l","L","","(none)","","(none)","burnc002","Corbin Burnes","strom001","Marcus Stroman"')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "gl.txt"
            p.write_text(line + "\n")
            df = parse_gamelog(p)
        r = df.iloc[0]
        self.assertEqual((r.vis, r.home, r.vis_runs, r.home_runs), ("MIL", "CHN", 0, 4))
        self.assertEqual((r.v_ab, r.v_h, r.v_bb, r.v_so), (29, 4, 5, 12))
        self.assertEqual((r.vis_sp, r.home_sp), ("burnc002", "strom001"))


if __name__ == "__main__":
    unittest.main()
