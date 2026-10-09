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


if __name__ == "__main__":
    unittest.main()
