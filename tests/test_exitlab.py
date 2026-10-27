import datetime as dt
import unittest

import numpy as np
import pandas as pd

from alpacabot import exitlab as xl


def _bars(closes):
    c = np.asarray(closes, float)
    idx = pd.bdate_range("2024-01-02", periods=len(c), tz="America/New_York")
    return pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1e6}, index=idx)


def _sig(d, at, stop, tp=np.inf):
    s = pd.DataFrame(index=d.index)
    s["entry"] = False
    s.iloc[at, s.columns.get_loc("entry")] = True
    s["stop"], s["tp"], s["exit"] = stop, tp, False
    return s


class ExitLabTests(unittest.TestCase):
    def setUp(self):
        self.d = _bars([100.0] * 30 + [100 + i for i in range(1, 31)] + [130 - 3 * i for i in range(1, 15)])
        self.a, self.b = dt.date(2020, 1, 1), dt.date(2030, 1, 1)

    def test_fixed_target_hit(self):
        t = xl.simulate(self.d, _sig(self.d, 29, 95.0), "fixed_1r", self.a, self.b, slip_bps=0)
        self.assertEqual(t[0]["reason"], "target")
        self.assertAlmostEqual(t[0]["r"], 1.0, places=6)

    def test_time_exit(self):
        t = xl.simulate(self.d, _sig(self.d, 29, 95.0), "time_10", self.a, self.b, slip_bps=0)
        self.assertEqual(t[0]["reason"], "signal")
        self.assertEqual(t[0]["days"], 11)

    def test_atr_trail_locks_gain(self):
        t = xl.simulate(self.d, _sig(self.d, 29, 95.0), "atr_trail_3", self.a, self.b, slip_bps=0)
        self.assertEqual(t[0]["reason"], "stop")
        self.assertGreater(t[0]["r"], 3.0)                 # rode the run up, gave back only ~3 ATR

    def test_initial_stop_and_no_lookahead(self):
        d = _bars([100.0] * 30 + [99, 97, 94, 90])
        t = xl.simulate(d, _sig(d, 29, 95.0), "fixed_3r", self.a, self.b, slip_bps=0)
        self.assertEqual(t[0]["reason"], "stop")
        self.assertAlmostEqual(t[0]["r"], -1.0, places=6)

    def test_short_test_shape(self):
        bars = {"SPY": self.d, "QQQ": self.d, "AAA": self.d}
        sh = xl.short_test(bars, self.d["close"], dt.date(2024, 4, 20))
        self.assertEqual(set(sh), set(xl.STRATEGIES))
        self.assertEqual(set(sh["topdown"]), set(xl.EXITS))
        self.assertFalse(sh["topdown"]["own"]["pass"])


if __name__ == "__main__":
    unittest.main()
