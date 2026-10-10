"""WNBA institutional simulation: rotation depth, skew-normal MC with std 7.5, feature directions, wiring."""
import unittest

import numpy as np
import pandas as pd

from marv import pick_engine as PE
from marv import wnba_institutional as WI

P = {"N": 0.3, "T": 0.2, "D": 0.5, "M": 2.0, "S": 1.0, "TT": 0.5, "MT": -1.0, "ST": 1.0,
     "skew_a": 0.5, "sd_m": 7.5, "skew_t": [0.5, -5.0, 15.0]}


class WnbaInstitutionalTest(unittest.TestCase):
    def test_skew_std_75(self):
        from scipy.stats import skewnorm
        a, loc, sc = WI.skew_params(0.9)
        self.assertAlmostEqual(float(skewnorm.std(a, loc, sc)), 7.5, places=6)
        self.assertAlmostEqual(float(skewnorm.mean(a, loc, sc)), 0.0, places=6)
        self.assertEqual(WI.SD_MARGIN, 7.5)

    def test_rotation_depth_and_missing(self):
        rows = []
        for g in range(1, 7):
            for pid, mins in ((1, 34), (2, 32), (3, 30), (4, 28), (5, 25), (6, 20), (7, 13), (8, 8)):
                if g == 6 and pid == 2:
                    continue  # star sits out game 6
                rows.append({"game_id": str(g), "team": "Aces", "season": 2026, "athlete_id": pid, "minutes": mins,
                             "date": pd.Timestamp("2026-06-01") + pd.Timedelta(days=g)})
        r = WI.rotation(pd.DataFrame(rows))
        post = r[r.game_id.isna()].iloc[0]
        self.assertAlmostEqual(post.miss_post, 32 * 4 / 5 / 40, places=3)   # usual mins of the absent star / 40
        self.assertEqual(post.depth_post, 7.0)
        g4 = r[r.game_id == "4"].iloc[0]
        self.assertEqual(g4.miss, 0.0)
        self.assertTrue(np.isnan(r[r.game_id == "2"].iloc[0].depth))         # too few earlier games

    def test_directions(self):
        f0 = {"net_d": 0.0, "ts_d": 0.0, "ts_s": 0.0, "depth_d": 0.0, "miss_d": 0.0, "miss_s": 0.0}
        base = WI.wnba_institutional_simulation(3.0, 165.0, f0, -3.0, 165.0, -3.0, 165.0, P, n=4000)
        better = WI.wnba_institutional_simulation(3.0, 165.0, {**f0, "net_d": 10.0, "ts_d": 5.0}, -3.0, 165.0,
                                                  -3.0, 165.0, P, n=4000)
        hurt = WI.wnba_institutional_simulation(3.0, 165.0, {**f0, "miss_d": -1.0, "miss_s": 1.0}, -3.0, 165.0,
                                                -3.0, 165.0, P, n=4000)
        steam = WI.wnba_institutional_simulation(3.0, 165.0, f0, -5.0, 167.0, -3.0, 165.0, P, n=4000)
        self.assertEqual(base["margin"], 3.0)
        self.assertAlmostEqual(better["margin"], 3.0 + 3.0 + 1.0, places=1)
        self.assertLess(hurt["margin"], base["margin"])
        self.assertLess(hurt["total"], base["total"])
        self.assertGreater(steam["margin"], base["margin"])
        self.assertGreater(steam["total"], base["total"])
        for k in ("p_home", "p_home_cover", "p_over"):
            self.assertTrue(0 <= base["sim"][k] <= 1)

    def test_missing_features_safe(self):
        r = WI.wnba_institutional_simulation(2.0, 160.0, {"net_d": float("nan")}, None, None, None, None, P, n=1000)
        self.assertEqual(r["margin"], 2.0)
        self.assertNotIn("p_home_cover", r["sim"])

    def test_tighter_sd_more_confident(self):
        wide = WI.simulate(4.0, 160.0, {**P, "sd_m": 12.0}, n=20000, seed=2)["p_home"]
        tight = WI.simulate(4.0, 160.0, P, n=20000, seed=2)["p_home"]
        self.assertGreater(tight, wide)

    def test_pick_engine_wiring(self):
        self.assertTrue(hasattr(PE, "_wnba_institutional"))
        self.assertIn("wnba", PE.SPORTS)
        r = {"home": "Las Vegas Aces", "away": "Seattle Storm", "picks": {"ml": "home"}, "power_margin": 2.0,
             "trend_margin": 1.0, "margin": 3.0, "ml_agree": {"a": True}, "best": "ml",
             "institutional": {"tiers": (None, None), "steam_pts": 0.5, "features": {"net_d": 4.0, "miss_d": -1.0}}}
        t = PE.text(r, "wnba")
        self.assertIn("institutional skew-MC", t)
        self.assertIn("net rtg +4.0/100", t)
        self.assertNotIn("%", t)


if __name__ == "__main__":
    unittest.main()
