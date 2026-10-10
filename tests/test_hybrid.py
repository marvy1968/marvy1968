"""Hybrid engine: trend catcher, full-spectrum H2H matrix, restricted MC totals + overlay / Upset Alert wiring."""
import unittest
from pathlib import Path
from unittest import mock

from marv import hybrid as H
from marv import insight as I


def prof(**kw):
    p = {"n": 6, "off": 2.5, "def": 2.0, "net_epa": 0.05, "ypp": 6.0, "succ": 0.45, "tov": 0.12, "expl": 0.10,
         "yppm": 0.5, "pace": 70.0, "poss": 24.0, "poss_sd": 2.0, "off_sd": 0.6, "def_sd": 0.6}
    p.update(kw)
    for c in H.CATS:
        p.setdefault(c + "3", p[c])
    return p


class HybridTest(unittest.TestCase):
    def test_trend_modifier_floor_and_reasons(self):
        m, why = H.trend_catcher_modifier(prof(), "cfb")
        self.assertEqual(m, 1.0)
        self.assertEqual(why, [])
        m, why = H.trend_catcher_modifier(prof(tov3=0.30, yppm3=-1.0), "cfb")  # 1 - 0.09 - 0.075
        self.assertAlmostEqual(m, 0.835)
        self.assertEqual(len(why), 2)
        m, _ = H.trend_catcher_modifier(prof(tov3=1.0, yppm3=-5.0), "cfb")
        self.assertEqual(m, H.MOD_FLOOR)

    def test_matrix_winner_take_all(self):
        good, bad = prof(off=3.5, net_epa=0.2, ypp=7.0, yppm=2.0, pace=75), prof(def_=0)
        r = H.full_spectrum_h2h_matrix(good, bad, "cfb")
        self.assertEqual(r["ml"], "home")
        self.assertEqual(r["home_pts"], 5)  # off, net EPA, ypp, ypp margin, pace (+1 dir)
        self.assertEqual(r["away_pts"], 0)
        r = H.full_spectrum_h2h_matrix(good, bad, "cfb", mh=0.8)  # trend mod scales the category points
        self.assertAlmostEqual(r["home_pts"], 4.0)

    def test_mc_totals_quantiles_only(self):
        r = H.analyze(prof(), prof(), "cfb", spread=-3.0, total=50.0)
        mc = r["mc"]
        self.assertLess(mc["p25"], mc["median"])
        self.assertLess(mc["median"], mc["p75"])
        self.assertAlmostEqual(mc["median"], 24 / 2 * 4.5, delta=2.5)
        self.assertEqual(r["ou"], "over" if mc["median"] > 50 else "under")
        self.assertEqual(r["ats"], "home")  # even matrix: HFA 3.5 beats -3

    def test_overlay_heavy_fav_weak_categories_is_against(self):
        rec = {"home": "Louisville", "away": "Florida State", "game_id": "1", "model_margin": 14.0, "notes": []}
        fake = H.analyze(prof(off=2.0, net_epa=-0.1, ypp=5.0, yppm=-1.0, tov=0.2, tov3=0.4), prof(off=3.0, net_epa=0.1),
                         "cfb", spread=-14.0, total=55.0)
        with mock.patch.object(H, "game", return_value=fake):
            ins = I.side_insight(Path("/nonexistent"), "cfb", rec, None, "home", -14.0, -700, spread=False)
        labels = [f.label for f in ins.factors if f.side < 0]
        self.assertTrue(any("wins hybrid categories" in x for x in labels), labels)
        self.assertTrue(any("trend catcher" in x for x in labels), labels)
        self.assertNotEqual(ins.verdict, "good")  # heavy-favourite gate caps it

    def test_total_insight_gets_mc_factor(self):
        rec = {"home": "A", "away": "B", "game_id": "1", "model_total": 50.0, "notes": []}
        fake = H.analyze(prof(), prof(), "cfb", total=40.0)
        with mock.patch.object(H, "game", return_value=fake):
            ins = I.total_insight(Path("/nonexistent"), "cfb", rec, None, True, 40.0)
        f = [f for f in ins.factors if f.label.startswith("hybrid MC total")]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].side, 1)


def _box_rows(gid, date, home, away, hs, as_, season=2026):
    base = {"season": season, "season_type": 2, "game_date": date, "field_goals_made": 30, "field_goals_attempted": 70,
            "three_point_field_goals_made": 8, "free_throws_attempted": 20, "offensive_rebounds": 10,
            "defensive_rebounds": 25, "total_rebounds": 35, "total_turnovers": 14}
    return [{**base, "game_id": gid, "team_display_name": home, "opponent_team_display_name": away,
             "team_home_away": "home", "team_score": hs},
            {**base, "game_id": gid, "team_display_name": away, "opponent_team_display_name": home,
             "team_home_away": "away", "team_score": as_, "total_turnovers": 18, "total_rebounds": 30}]


class HybridBasketballTest(unittest.TestCase):
    def test_bb_team_games_categories(self):
        import tempfile
        import pandas as pd
        with tempfile.TemporaryDirectory() as d:
            rows = []
            for i in range(5):
                rows += _box_rows(f"{i}", f"2026-06-0{i + 1}", "Las Vegas Aces", "Seattle Storm", 90, 80)
            with mock.patch.object(H, "bb_box", return_value=pd.DataFrame(rows)):  # no parquet engine in tests
                tg = H.bb_team_games(Path(d), "wnba", [2026])
            self.assertEqual(len(tg), 10)
            aces = tg[tg.team == "Las Vegas Aces"].iloc[0]
            poss = 70 + 0.44 * 20 - 10 + 14
            self.assertAlmostEqual(aces["off"], 90 / poss)
            self.assertAlmostEqual(aces["ypp"], (30 + 4) / 70)          # eFG%
            self.assertAlmostEqual(aces["succ"], 90 / (2 * (70 + 8.8)))  # TS%
            self.assertAlmostEqual(aces["tov"], 14 / poss)
            self.assertAlmostEqual(aces["yppm"], 5)                      # rebound margin
            self.assertAlmostEqual(aces["expl"], 10 / 35)                # OREB%

    def test_bb_live_profile_and_text(self):
        import tempfile
        import pandas as pd
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "cache"
            cache.mkdir()
            rows = []
            for i in range(5):
                rows += _box_rows(f"{i}", f"2026-06-0{i + 1}", "Las Vegas Aces", "Seattle Storm", 90, 80)
            H._CACHE.clear()
            with mock.patch.object(H, "bb_box", return_value=pd.DataFrame(rows)):
                r = H.game(Path(d), "wnba", "Las Vegas Aces", "Seattle Storm", -6.5, 160.5,
                           as_of=datetime(2026, 7, 1, tzinfo=timezone.utc))
            self.assertIsNotNone(r)
            self.assertEqual(r["ml"], "home")
            self.assertGreater(r["home_pts"], r["away_pts"])
            self.assertIn("tov", r["won"]["home"])
            self.assertTrue(r["mc"]["p25"] < r["mc"]["median"] < r["mc"]["p75"])
            self.assertIn("MC total", H.text(r, "Las Vegas Aces", "Seattle Storm", "wnba"))
            self.assertIn(H.label("wnba", "ypp"), ("eFG%",))

    def test_bb_trend_modifier(self):
        p = prof(tov=0.12, tov3=0.16, yppm=3.0, yppm3=-2.0)
        m, why = H.trend_catcher_modifier(p, "nba")  # 1 - 1.5*0.04 - 0.01*5 = 0.89
        self.assertAlmostEqual(m, 0.89)
        self.assertTrue(any("TOV rate" in w for w in why) and any("reb margin" in w for w in why), why)

    def test_all_sports_wired(self):
        from marv import defense, parlay, upset
        for sp in ("nba", "wnba", "ncaab", "ncaaw", "euroleague"):
            self.assertIn(sp, H.SPORTS)
            self.assertIn(sp, upset.SPORTS)
            self.assertIn(sp, parlay.SPORTS)
            self.assertIn(sp, parlay.ODDS_KEYS)
            self.assertIn(sp, defense.HEAVY)
        self.assertIsNone(I.hybrid_read(Path("/nonexistent"), "mlb", {"home": "A", "away": "B"}))


if __name__ == "__main__":
    unittest.main()
