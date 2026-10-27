import datetime as dt
import unittest

import numpy as np
import pandas as pd

from fxbot import exitlab as fxl


def _bars(closes, spread=0.0002):
    c = np.asarray(closes, float)
    idx = pd.date_range("2025-01-06", periods=len(c), freq="h", tz="UTC")
    d = {}
    for side, off in (("b", -spread / 2), ("a", spread / 2)):
        d[side + "o"], d[side + "c"] = c + off, c + off
        d[side + "h"], d[side + "l"] = c + 0.0005 + off, c - 0.0005 + off
    d["volume"] = 100.0
    return pd.DataFrame(d, index=idx)


class _Strat:
    max_bars = 10 ** 6

    def __init__(self, at, stop):
        self.at, self.stop = at, stop

    def frame(self, m):
        f = pd.DataFrame(index=m.index)
        f["long"] = False
        f.iloc[self.at, 0] = True
        f["short"], f["exit_long"], f["exit_short"], f["stop"], f["rank"] = False, False, False, self.stop, 0.0
        return f


class FxExitLabTests(unittest.TestCase):
    def setUp(self):
        self.d = _bars([1.1000] * 30 + [1.1000 + 0.001 * i for i in range(1, 31)] + [1.13 - 0.003 * i for i in range(1, 15)])
        self.a, self.b = dt.date(2020, 1, 1), dt.date(2030, 1, 1)

    def test_fixed_target_pays_the_spread(self):
        t = fxl.simulate(self.d, _Strat(29, 0.005), "fixed_1r", self.a, self.b)
        self.assertAlmostEqual(t[0]["r"], 1.0, places=6)                 # filled at the ask, target hit on the bid high

    def test_live_cuts_at_half_r(self):
        d = _bars([1.1] * 30 + [1.099, 1.097, 1.094, 1.090])
        t = fxl.simulate(d, _Strat(29, 0.004), "live", self.a, self.b)
        self.assertLess(t[0]["r"], -0.5 + 1e-9)
        self.assertGreater(t[0]["r"], -1.0)

    def test_atr_trail_locks_gain(self):
        t = fxl.simulate(self.d, _Strat(29, 0.005), "atr_trail_3", self.a, self.b)
        self.assertGreater(t[0]["r"], 3.0)

    def test_own_uses_the_engine(self):
        t = fxl.simulate(self.d, _Strat(29, 0.005), "own", self.a, self.b)
        self.assertEqual(len(t), 1)

    def test_short_test_shape(self):
        sh = fxl.short_test({"x": (lambda: _Strat(29, 0.005), "H1")}, {"H1": {"EUR_USD": self.d}}, dt.date(2025, 1, 12), days=10)
        self.assertEqual(set(sh["x"]), set(fxl.EXITS))
        self.assertFalse(sh["x"]["live"]["pass"])


if __name__ == "__main__":
    unittest.main()
