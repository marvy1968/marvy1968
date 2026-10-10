"""NFL institutional simulation: O-line health, velocity-decayed steam, skew-normal MC with std 13.5, wiring."""
import math
import unittest

import numpy as np
import pandas as pd

from marv import nfl_institutional as NI
from marv import pick_engine as PE

P = {"O": 1.0, "OT": -1.0, "S": 1.0, "ST": 1.5, "skew_a": 0.8, "sd_m": 13.5, "skew_t": [1.5, -11.0, 18.0]}


def _snaps():
    rows = []
    for wk in (1, 2, 3):
        for n, pos in (("Lt One", "T"), ("Lg Two", "G"), ("C Three", "C"), ("Rg Four", "G"), ("Rt Five", "T"),
                       ("Wr Six", "WR")):
            if wk == 3 and n == "Rg Four":
                continue  # missed week 3 (injured, not on the week-4 report -> still out)
            rows.append({"week": wk, "player": n, "position": pos, "team": "KC", "offense_pct": 1.0})
    s = pd.DataFrame(rows)
    s["key"] = s.player.map(NI._key)
    i = pd.DataFrame([{"week": 4, "full_name": "Lt One", "team": "KC", "report_status": "Out"},
                      {"week": 4, "full_name": "C Three", "team": "KC", "report_status": "Questionable"}])
    i["key"] = i.full_name.map(NI._key)
    return s, i


class NflInstitutionalTest(unittest.TestCase):
    def test_ol_lost(self):
        s, i = _snaps()
        lost = NI.ol_lost(s, i, "KC", 4)
        # Lt Out 1.0 + C questionable 0.25 + Rg absent last game (usual share 2/3) 0.667
        self.assertAlmostEqual(lost, 1.0 + 0.25 + 2 / 3, places=2)
        self.assertIsNone(NI.ol_lost(s, i, "KC", 1))           # no earlier game this season
        self.assertEqual(NI.ol_lost(s, i.iloc[:0], "KC", 3), 0.0)

    def test_skew_std_135(self):
        a, loc, sc = NI.skew_params(0.8)
        from scipy.stats import skewnorm
        self.assertAlmostEqual(float(skewnorm.std(a, loc, sc)), 13.5, places=6)
        self.assertAlmostEqual(float(skewnorm.mean(a, loc, sc)), 0.0, places=6)
        self.assertEqual(NI.skew_params(0.0), (0.0, 0.0, 13.5))

    def test_directions(self):
        base = NI.nfl_institutional_simulation(3.0, 45.0, 0.0, 0.0, -3.0, 45.0, -3.0, 45.0, P, n=4000)
        hurt = NI.nfl_institutional_simulation(3.0, 45.0, 2.0, 0.0, -3.0, 45.0, -3.0, 45.0, P, n=4000)
        steam = NI.nfl_institutional_simulation(3.0, 45.0, 0.0, 0.0, -6.0, 47.0, -3.0, 45.0, P, n=4000)
        self.assertEqual(base["margin"], 3.0)
        self.assertAlmostEqual(hurt["margin"], 1.0, places=1)    # home missing 2 linemen
        self.assertLess(hurt["total"], base["total"])
        self.assertGreater(steam["margin"], base["margin"])       # money on the home side
        self.assertGreater(steam["total"], base["total"])
        self.assertEqual(hurt["ol_health"], (0.6, 1.0))
        for k in ("p_home", "p_home_cover", "p_over"):
            self.assertTrue(0 <= base["sim"][k] <= 1)

    def test_skew_oriented_and_spread(self):
        a = NI.simulate(7.0, 45.0, P, n=20000, seed=1)["p_home"]
        b = NI.simulate(-7.0, 45.0, P, n=20000, seed=1)["p_home"]
        self.assertAlmostEqual(a, 1 - b, delta=0.02)
        # std 13.5: a 7-point favourite wins roughly 65-75% of sims
        self.assertTrue(0.6 < a < 0.8)

    def test_missing_inputs(self):
        r = NI.nfl_institutional_simulation(-2.0, 44.0, None, float("nan"), None, None, None, None, P, n=1000)
        self.assertEqual(r["margin"], -2.0)
        self.assertNotIn("p_home_cover", r["sim"])

    def test_pick_engine_text_no_percent(self):
        inst = NI.nfl_institutional_simulation(3.0, 45.0, 1.0, 0.0, -3.0, 45.0, -2.0, 44.0, P, n=1000)
        inst["tiers"] = (None, None)
        r = {"home": "Kansas City Chiefs", "away": "Denver Broncos", "power_margin": 3.0, "trend_margin": 2.0,
             "margin": inst["margin"], "total": inst["total"], "spread": -3.0, "line": 45.0,
             "picks": {"ml": "home", "spread": "home", "total": "over"},
             "strength": {"ml": 0.1, "spread": 0.03, "total": 0.02}, "best": "spread",
             "ml_agree": {"power": True, "trend": True, "mc": True}, "institutional": inst}
        t = PE.text(r, "nfl")
        self.assertIn("institutional skew-MC", t)
        self.assertIn("OL starters out Chiefs 1.0", t)
        self.assertNotIn("%", t)


if __name__ == "__main__":
    unittest.main()
