import unittest
from datetime import date

import numpy as np
import pandas as pd

from fxbot import exitlab as xl


def bidask(c, start="2026-06-01", spread=0.0002):
    c = np.asarray(c, float)
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + 0.0005, np.minimum(o, c) - 0.0005
    idx = pd.date_range(start, periods=len(c), freq="h", tz="UTC")
    d = {}
    for k, v in (("o", o), ("h", h), ("l", l), ("c", c)):
        d["b" + k], d["a" + k] = v - spread / 2, v + spread / 2
    return pd.DataFrame(d, index=idx)


class OneLong:
    """Long on bar 30 with a 0.0050 stop; nothing else."""
    max_bars = 500

    def frame(self, m):
        n = len(m)
        L = np.zeros(n, bool)
        L[30] = True
        z = np.zeros(n, bool)
        return pd.DataFrame({"long": L, "short": z, "exit_long": z, "exit_short": z, "stop": np.full(n, 0.0050)}, index=m.index)


def run(c, ex):
    df = bidask(c)
    return xl.simulate(df, OneLong(), ex, date(2026, 1, 1), date(2027, 1, 1))


class MuseExitTests(unittest.TestCase):
    def test_williams_first_profitable_close(self):
        c = np.r_[np.full(31, 1.10), 1.0995, 1.1003, 1.1010, np.full(40, 1.1010)]
        r = run(c, "williams")
        self.assertEqual(len(r), 1)
        self.assertGreater(r[0]["r"], 0)
        self.assertLess(r[0]["r"], 0.4)

    def test_ponsi_breakeven_then_trail(self):
        up = np.linspace(1.10, 1.12, 60)                  # +4R run, then a slide back to entry
        down = np.linspace(1.12, 1.095, 60)
        c = np.r_[np.full(31, 1.10), up, down]
        r = run(c, "ponsi")[0]["r"]
        self.assertGreater(r, 1.0)                        # trail locked well above break-even

    def test_raschke_flat_day6(self):
        c = np.r_[np.full(31, 1.10), np.linspace(1.10, 1.104, 200)]
        r = run(c, "raschke")
        self.assertEqual(len(r), 1)
        self.assertGreater(r[0]["r"], 0)

    def test_turtle_needs_240_bars_of_history(self):
        c = np.r_[np.full(31, 1.10), np.full(60, 1.10)]
        r = run(c, "turtle")                              # no 240-bar low yet: rides to the end of data
        self.assertEqual(len(r), 1)

    def test_wstair_tight_trail(self):
        c = np.r_[np.full(31, 1.10), 1.1030, 1.1060, 1.1035, np.full(30, 1.1035)]
        r = run(c, "wstair")[0]["r"]
        self.assertGreater(r, 0.5)                        # best ~1.2R, trail 0.5R under it

    def test_crabel_day_close(self):
        c = np.r_[np.full(31, 1.10), np.full(60, 1.1010)]
        r = run(c, "crabel")
        self.assertEqual(len(r), 1)

    def test_all_new_exits_registered(self):
        self.assertEqual(set(xl.MUSE_EXITS), {"ponsi", "raschke", "turtle", "wstair", "wstair_ag", "crabel", "williams"})


if __name__ == "__main__":
    unittest.main()
