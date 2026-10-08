import unittest

import numpy as np
import pandas as pd

from marv.stats import pfm


class PFMTests(unittest.TestCase):
    def test_bad_defense_raises_the_other_teams_score(self):
        p = pfm.PFMParams()
        good_d = pfm.lambdas({"off_epa": 0.0, "def_epa": -0.1, "pace": 1}, {"off_epa": 0.0, "def_epa": -0.1, "pace": 1}, p)
        bad_d = pfm.lambdas({"off_epa": 0.0, "def_epa": 0.2, "pace": 1}, {"off_epa": 0.0, "def_epa": -0.1, "pace": 1}, p)
        self.assertGreater(bad_d[1], good_d[1])  # away team scores more against the bad home defense
        h, a = pfm.simulate(*good_d, n=20000, rng=np.random.default_rng(0))
        self.assertTrue(40 < h.mean() + a.mean() < 50)  # realistic NFL total, not ~16

    def test_fit_recovers_scale_and_veto(self):
        rng = np.random.default_rng(1)
        n = 2000
        rows = pd.DataFrame({c: rng.normal(0, 0.1, n) for c in ("h_off_epa", "a_off_epa", "h_def_epa", "a_def_epa")})
        rows["h_pace"] = rows["a_pace"] = 1.0
        rows["home_score"] = 3.5 * rng.poisson(6.5 + 8 * (rows.h_off_epa + rows.a_def_epa))
        rows["away_score"] = 3.5 * rng.poisson(6.2 + 8 * (rows.a_off_epa + rows.h_def_epa))
        p = pfm.fit(rows)
        self.assertAlmostEqual(p.scale, 8, delta=1.5)
        self.assertAlmostEqual(p.home, 0.3, delta=0.2)
        self.assertEqual(pfm.veto({"injuries": True}), "injuries")
        self.assertEqual(pfm.veto({"spread": -1.0}), "tight spread")
        self.assertIsNone(pfm.veto({"spread": -8.5}))


if __name__ == "__main__":
    unittest.main()
