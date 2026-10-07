import unittest
from datetime import datetime, timezone

import numpy as np

from cfb_bot.cfbd import current_week
from cfb_bot.config import Settings
from cfb_bot.predict import grade, predict_slate
from cfb_bot.ratings import fit_ratings, pace_factors
from cfb_bot.simulate import simulate_game
from cfb_bot.telegram import format_slate, split_message
from cfb_bot.veto import american_to_prob, ml_pick, spread_pick, total_pick


def synthetic_season(n_teams=40, weeks=8, seed=1):
    rng = np.random.default_rng(seed)
    teams = [f"Team {i}" for i in range(n_teams)]
    true_off = dict(zip(teams, rng.normal(0, 7, n_teams)))
    true_def = dict(zip(teams, rng.normal(0, 7, n_teams)))
    games, gid = [], 0
    for week in range(1, weeks + 1):
        order = rng.permutation(teams)
        for home, away in zip(order[::2], order[1::2]):
            gid += 1
            hp = max(0, round(28 + true_off[home] + true_def[away] + 1.25 + rng.normal(0, 10)))
            ap = max(0, round(28 + true_off[away] + true_def[home] - 1.25 + rng.normal(0, 10)))
            games.append({"id": gid, "week": week, "homeTeam": str(home), "awayTeam": str(away),
                          "homePoints": hp, "awayPoints": ap, "completed": True, "neutralSite": False,
                          "homeClassification": "fbs", "awayClassification": "fbs"})
    return games, true_off, true_def


class RatingsTest(unittest.TestCase):
    def test_recovers_true_strength(self):
        games, true_off, true_def = synthetic_season()
        r = fit_ratings(games)
        teams = list(true_off)
        est = [r.offense[t] - r.defense[t] for t in teams]
        truth = [true_off[t] - true_def[t] for t in teams]
        self.assertGreater(np.corrcoef(est, truth)[0, 1], 0.8)

    def test_fcs_opponents_are_pooled(self):
        games = [{"homeTeam": "A", "awayTeam": "Tiny State", "homePoints": 63, "awayPoints": 0,
                  "awayClassification": "fcs", "homeClassification": "fbs", "completed": True}]
        r = fit_ratings(games)
        self.assertIn("__FCS__", r.offense)
        self.assertNotIn("Tiny State", r.offense)

    def test_pace(self):
        stats = [{"team": "Fast", "statName": "games", "statValue": 2},
                 {"team": "Fast", "statName": "rushingAttempts", "statValue": 90},
                 {"team": "Fast", "statName": "passAttempts", "statValue": 90},
                 {"team": "Slow", "statName": "games", "statValue": 2},
                 {"team": "Slow", "statName": "rushingAttempts", "statValue": 60},
                 {"team": "Slow", "statName": "passAttempts", "statValue": 60}]
        pace = pace_factors(stats)
        self.assertAlmostEqual(pace["Fast"], 1.2)
        self.assertAlmostEqual(pace["Slow"], 0.8)


class SimulateTest(unittest.TestCase):
    def test_even_game_is_coin_flip(self):
        sim = simulate_game(28, 28, n=40000, rng=np.random.default_rng(0))
        self.assertAlmostEqual(sim.home_cover_prob(0), 0.5, delta=0.02)
        self.assertAlmostEqual(sim.total.mean(), 56, delta=2.5)
        self.assertFalse((sim.home_points == sim.away_points).any(), "overtime must break ties")

    def test_garbage_time_shrinks_blowout_margin(self):
        rng = np.random.default_rng(0)
        damped = simulate_game(45, 10, n=40000, garbage_margin=21, rng=rng)
        undamped = simulate_game(45, 10, n=40000, garbage_margin=999, rng=np.random.default_rng(0))
        self.assertLess(damped.margin.mean(), undamped.margin.mean() - 1)


class VetoTest(unittest.TestCase):
    s = Settings()

    def test_clear_edge_is_active(self):
        pick = spread_pick("A", "B", -7, -7, 0.62, 10, 5, self.s)
        self.assertTrue(pick.active)
        self.assertEqual(pick.side, "A")

    def test_blowout_tier_needs_bigger_edge(self):
        pick = spread_pick("A", "B", -30, -30, 0.60, 33, 5, self.s)  # 7.6% edge < 10% needed at -30
        self.assertFalse(pick.active)
        self.assertIn("blowout", pick.vetoes[0])

    def test_trap_line(self):
        # Opened A -7, now A -3.5: market moved 3.5 points away from A.
        pick = spread_pick("A", "B", -3.5, -7, 0.65, 8, 5, self.s)
        self.assertTrue(any("trap" in v for v in pick.vetoes))

    def test_volatile_mismatch(self):
        pick = spread_pick("A", "B", -3, -3, 0.95, 30, 5, self.s)
        self.assertTrue(any("volatile" in v for v in pick.vetoes))

    def test_away_side_and_totals(self):
        pick = spread_pick("A", "B", -10, -10, 0.30, 4, 5, self.s)
        self.assertEqual((pick.side, pick.line), ("B", 10))
        under = total_pick(60, 60, 0.35, 53, 5, self.s)
        self.assertEqual(under.side, "Under")
        self.assertTrue(under.active)

    def test_moneyline(self):
        self.assertAlmostEqual(american_to_prob(-110), 110 / 210)
        pick = ml_pick("A", "B", 150, -170, 0.50, 5, self.s)
        self.assertEqual(pick.side, "A")
        self.assertTrue(pick.active)
        heavy = ml_pick("A", "B", -1000, 650, 0.99, 5, self.s)
        self.assertFalse(heavy.active)


class SlateTest(unittest.TestCase):
    def test_end_to_end(self):
        games, _, _ = synthetic_season(weeks=9)
        history = [gm for gm in games if gm["week"] < 9]
        slate = [dict(gm, completed=False) for gm in games if gm["week"] == 9]
        lines = [{"id": gm["id"], "homeTeam": gm["homeTeam"], "awayTeam": gm["awayTeam"],
                  "lines": [{"provider": "consensus", "spread": -3.5, "spreadOpen": -3.0,
                             "overUnder": 55.5, "overUnderOpen": 55.5,
                             "homeMoneyline": -160, "awayMoneyline": 135}]} for gm in slate]
        s = Settings(simulations=4000, max_games=10)
        preds = predict_slate(slate, lines, fit_ratings(history), s, rng=np.random.default_rng(0))
        self.assertEqual(len(preds), 10)
        self.assertTrue(all(len(p.picks) == 3 for p in preds))
        text = format_slate(2026, 9, preds)
        self.assertIn("Week 9", text)
        self.assertIn("veto pass rate", text)
        for p in preds:
            for pk in p.picks:
                self.assertIn(grade(pk, p, 30, 20), ("win", "loss", "push"))

    def test_grade(self):
        from cfb_bot.predict import GamePrediction
        from cfb_bot.veto import Pick
        pred = GamePrediction(1, "", "A", "B", False, 30, 20, 10, 50, 0.7, -7, 50)
        self.assertEqual(grade(Pick("spread", "A", -7, 0.6, 0.1), pred, 30, 20), "win")
        self.assertEqual(grade(Pick("spread", "B", 7, 0.6, 0.1), pred, 30, 20), "loss")
        self.assertEqual(grade(Pick("spread", "A", -10, 0.6, 0.1), pred, 30, 20), "push")
        self.assertEqual(grade(Pick("total", "Under", 52.5, 0.6, 0.1), pred, 30, 20), "win")


class MiscTest(unittest.TestCase):
    def test_current_week(self):
        cal = [{"week": 1, "seasonType": "regular", "startDate": "2026-08-29T00:00:00Z", "endDate": "2026-09-04T00:00:00Z"},
               {"week": 2, "seasonType": "regular", "startDate": "2026-09-04T00:00:00Z", "endDate": "2026-09-11T00:00:00Z"}]
        self.assertEqual(current_week(cal, datetime(2026, 9, 6, tzinfo=timezone.utc)), (2, "regular"))
        self.assertIsNone(current_week(cal, datetime(2027, 1, 1, tzinfo=timezone.utc)))

    def test_split_message(self):
        chunks = split_message("\n\n".join(["x" * 1500] * 5))
        self.assertTrue(all(len(c) <= 4096 for c in chunks))
        self.assertGreater(len(chunks), 1)


if __name__ == "__main__":
    unittest.main()
