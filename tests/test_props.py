import unittest

import numpy as np
import pandas as pd

from marv.props import nfl as P
from marv.props import odds as O
from marv.props import run as R


def event(books):
    return {"id": "e1", "home_team": "Buffalo Bills", "away_team": "Miami Dolphins",
            "commence_time": "2026-10-11T17:00:00Z", "bookmakers": books}


def book(key, line, over=-110, under=-110):
    return {"key": key, "title": key.title(), "markets": [{"key": "player_pass_yds", "outcomes": [
        {"name": "Over", "description": "Josh Allen", "point": line, "price": over},
        {"name": "Under", "description": "Josh Allen", "point": line, "price": under}]}]}


class PropsTests(unittest.TestCase):
    def test_prop_rows_reads_player_from_description_and_prefers_book(self):
        df = O.prop_rows(event([book("draftkings", 250.5), book("bovada", 247.5, -120, 100)]))
        self.assertEqual(len(df), 1)
        r = df.iloc[0]
        self.assertEqual((r.player, r.book, r.line, r.over_price, r.under_price), ("Josh Allen", "bovada", 247.5, -120, 100))
        self.assertEqual(O.prop_rows(event([book("draftkings", 250.5)])).iloc[0].book, "draftkings")

    def test_names_and_prices(self):
        self.assertEqual(O.norm_name("Marvin Harrison Jr."), O.norm_name("marvin harrison"))
        self.assertAlmostEqual(R.implied(-110), 110 / 210)
        self.assertAlmostEqual(R.payout(150), 1.5)
        self.assertEqual(list(P.half_line(np.array([47.0, 47.9]))), [47.5, 47.5])

    def test_model_projects_and_prices_overs(self):
        rng = np.random.default_rng(0)
        n = 1500
        rows = pd.DataFrame({"ewm_passing_yards": rng.uniform(150, 300, n)})
        for c in ("l3_passing_yards", "szn_passing_yards"):
            rows[c] = rows["ewm_passing_yards"] + rng.normal(0, 10, n)
        for c in ("implied_pts", "total_line", "is_home", "indoors", "wind", "games_before", "def_passing_yards"):
            rows[c] = rng.normal(0, 1, n)
        rows["passing_yards"] = rows["ewm_passing_yards"] + rng.normal(0, 40, n)
        m = P.PropModel(P.MARKETS["pass_yds"]).fit(rows)
        proj = m.project(rows.head(200))
        self.assertLess(np.mean(abs(proj - rows["ewm_passing_yards"].head(200))), 25)
        p = m.p_over(np.array([250.0, 250.0]), np.array([200.5, 300.5]), n=4000)
        self.assertGreater(p[0], 0.8)
        self.assertLess(p[1], 0.2)
        df = pd.DataFrame({"passing_yards": [260, 240, 230], "line": [250.5] * 3, "p": [0.7, 0.3, 0.52]})
        g = P.grade(df, "passing_yards", "line", "p", 0.05)
        self.assertEqual((g["n"], g["win_rate"]), (2, 1.0))


if __name__ == "__main__":
    unittest.main()
