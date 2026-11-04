import unittest

import numpy as np

from alpacabot import minexits as mx
from alpacabot import velez as ve
from alpacabot import vwapday as vd


def sd_from(c):
    c = np.asarray(c, float)
    o = np.r_[c[0], c[:-1]]
    return {"o": o, "h": np.maximum(o, c) + 0.01, "l": np.minimum(o, c) - 0.01, "c": c, "v": np.full(len(c), 1000.0), "n": len(c)}


class MinExitTests(unittest.TestCase):
    def setUp(self):
        mx.reset()
        mx.OVERRIDE["exit"] = None

    def tearDown(self):
        mx.OVERRIDE["exit"] = None
        mx.reset()

    def test_fixed_target_and_stop(self):
        c = np.r_[np.full(40, 100.0), np.linspace(100, 103, 60), np.full(290, 103.0)]
        ev, *_ = mx.min_walk("fixed_2r", sd_from(c), 1, 40, 100.0, 99.0, 380)
        self.assertEqual(ev[0][3], "target")
        self.assertAlmostEqual(ev[0][2], 102.0, places=2)
        c2 = np.r_[np.full(40, 100.0), np.linspace(100, 98, 30), np.full(320, 98.0)]
        ev, *_ = mx.min_walk("fixed_2r", sd_from(c2), 1, 40, 100.0, 99.0, 380)
        self.assertEqual(ev[0][3], "stop")

    def test_time_and_eod(self):
        c = np.full(390, 100.0)
        ev, *_ = mx.min_walk("time_30", sd_from(c), 1, 40, 100.0, 99.0, 380)
        self.assertEqual((ev[0][0], ev[0][3]), (70, "time"))
        ev, *_ = mx.min_walk("crabel", sd_from(c), 1, 40, 100.0, 99.0, 380)
        self.assertEqual((ev[0][0], ev[0][3]), (380, "eod"))

    def test_short_mirror_williams(self):
        c = np.r_[np.full(40, 100.0), np.linspace(100, 99.5, 20), np.full(330, 99.5)]
        ev, *_ = mx.min_walk("williams", sd_from(c), -1, 40, 100.0, 101.0, 380)
        self.assertEqual(ev[0][3], "signal")
        self.assertLess(ev[0][2], 100.0)

    def test_ponsi_locks(self):
        c = np.r_[np.full(40, 100.0), np.linspace(100, 104, 80), np.linspace(104, 98, 100), np.full(170, 98.0)]
        ev, *_ = mx.min_walk("ponsi", sd_from(c), 1, 40, 100.0, 99.0, 380)
        self.assertGreater(ev[0][2], 100.0)

    def test_every_exit_runs(self):
        rng = np.random.default_rng(3)
        c = 100 + np.cumsum(rng.normal(0, 0.05, 390))
        for ex in mx.EXITS_MIN[1:]:
            ev, *_ = mx.min_walk(ex, sd_from(c), 1, 60, float(c[59]), float(c[59]) - 0.5, 380)
            self.assertEqual(len(ev), 1, ex)
            self.assertTrue(60 <= ev[0][0] <= 380, ex)

    def test_override_hooks(self):
        c = np.full(390, 100.0)
        sd = sd_from(c)
        mx.OVERRIDE["exit"] = "time_30"
        ev, *_ = vd.walk("orb", sd, 1, 40, 100.0, 99.0)
        self.assertEqual(ev[0][3], "time")
        import pandas as pd
        ev = ve.walk(sd, 40, 100.0, 99.0, pd.DataFrame({"close_min": [], "c": [], "sma20": []}), 380)
        self.assertEqual(ev[0][3], "time")
        mx.OVERRIDE["exit"] = None
        ev, *_ = vd.walk("orb", sd, 1, 40, 100.0, 99.0)
        self.assertNotEqual(ev[0][3], "time")

    def test_weekly_stair_tightens(self):
        mx.CUR["week"] = (2026, 45)
        mx.WEEK_R[(2026, 45)] = 6.0                       # > 5R this week -> 0.20R trail
        c = np.r_[np.full(40, 100.0), np.linspace(100, 102, 20), np.linspace(102, 101.5, 20), np.full(310, 101.5)]
        ev, *_ = mx.min_walk("wstair", sd_from(c), 1, 40, 100.0, 99.0, 380)
        self.assertGreater(ev[0][2], 101.7)


if __name__ == "__main__":
    unittest.main()
