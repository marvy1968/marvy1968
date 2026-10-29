import unittest
from datetime import date

import numpy as np
import pandas as pd

from fxbot import sneaky as sp


def bars(rows, start="2026-10-26 13:30", freq="15min"):
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    d = pd.DataFrame(index=idx)
    for k, col in zip("ohlc", range(4)):
        v = np.array([r[col] for r in rows], float)
        d["b" + k], d["a" + k] = v - 0.005, v + 0.005
    return d


class SneakyTests(unittest.TestCase):
    def _stock_days(self):
        # day 1: range 100-102 ; day 2: dips to 99.9 at 10:00, closes back above 100 as an up bar, then runs to 104
        d1 = [(101, 102, 100, 101)] * 26
        d2 = [(100.5, 100.6, 100.2, 100.4)] * 2 + [(100.2, 100.4, 99.9, 100.3)] + [(100.3, 100.8, 100.25, 100.7)] + \
             [(100.7 + 0.15 * k, 100.9 + 0.15 * k, 100.6 + 0.15 * k, 100.8 + 0.15 * k) for k in range(22)]
        a = bars(d1, "2026-10-26 13:30")
        b = bars(d2, "2026-10-27 13:30")
        return pd.concat([a, b])

    def test_long_setup_at_yesterdays_low_hits_target(self):
        df = self._stock_days()
        tr = sp.trades(df, False, date(2026, 10, 27), date(2026, 10, 27))
        self.assertEqual(len(tr), 1)
        self.assertEqual(tr[0]["side"], 1)
        self.assertEqual(tr[0]["why"], "target")
        self.assertAlmostEqual(tr[0]["r"], sp.TARGET_R, places=6)

    def test_prior_levels_use_yesterday(self):
        df = self._stock_days()
        from fxbot.data import mid
        m = mid(df)
        h, l = sp.prior_levels(m, sp.day_key(df.index, False), False)
        self.assertTrue(np.isnan(h[0]))
        self.assertAlmostEqual(h[-1], 102.0)
        self.assertAlmostEqual(l[-1], 100.0)

    def test_control_and_resample(self):
        df = self._stock_days()
        tr = sp.trades(df, False, date(2026, 10, 27), date(2026, 10, 27))
        self.assertEqual(len(sp.control(df, False, tr, 1)), 1)
        five = bars([(100, 101, 99, 100.5)] * 6, "2026-10-27 13:30", "5min")
        self.assertEqual(len(sp.resample15(five)), 2)


if __name__ == "__main__":
    unittest.main()
