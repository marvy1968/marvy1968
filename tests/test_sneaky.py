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


class PivotRangeTests(unittest.TestCase):
    def test_flags_mark_a_reclaim_of_yesterdays_low(self):
        idx = pd.date_range("2026-10-26 21:00", periods=48, freq="h", tz="UTC")       # forex days from 17:00 New York
        c = np.r_[np.full(24, 1.10), [1.0998, 1.1003], np.full(22, 1.101)]
        m = pd.DataFrame({"open": c, "high": c + 0.0004, "low": c - 0.0004, "close": c}, index=idx)
        m.iloc[24, m.columns.get_loc("low")] = 1.0990                                   # wick below yesterday's low (1.0996)
        lo, so, pdh, pdl = sp.pivot_flags(m)
        self.assertTrue(np.isnan(pdl[0]))
        self.assertTrue(lo[24] and lo[25])                                             # the reclaim bar and the next 2
        self.assertFalse(so[:24].any())                                               # no levels on the first day

    def test_wrapper_gates_entries(self):
        class Always:
            max_bars = 10

            def frame(self, m):
                return pd.DataFrame({"long": True, "short": True, "exit_long": False, "exit_short": False, "stop": 0.001,
                                     "rank": 0.0}, index=m.index)
        idx = pd.date_range("2025-06-01", periods=24 * 300, freq="h", tz="UTC")
        c = 1.1 + 0.002 * np.sin(np.arange(len(idx)) / 7)
        m = pd.DataFrame({"open": c, "high": c + 0.0003, "low": c - 0.0003, "close": c}, index=idx)
        f = sp.PivotRange(Always(), "pivot").frame(m)
        lo, so, _, _ = sp.pivot_flags(m)
        self.assertFalse((f["long"].to_numpy() & ~lo).any())
        self.assertTrue((np.isnan(f["target"]) | (f["target"] >= f["stop"]) | ~(f["long"] | f["short"])).all())


if __name__ == "__main__":
    unittest.main()
