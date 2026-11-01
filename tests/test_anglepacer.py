import unittest
from datetime import date

import numpy as np
import pandas as pd

from alpacabot import anglepacer as ap


def bars(seed, n=1600, drift=0.001):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-02", periods=n)
    c = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.015, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.003, n))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.008, "low": np.minimum(o, c) * 0.992, "close": c,
                         "volume": rng.uniform(1e6, 3e6, n)}, index=idx)


class AnglePacerTests(unittest.TestCase):
    def test_slope_of_a_line(self):
        c = pd.Series(np.arange(40, dtype=float) * 2 + 5)
        s, fit = ap._slope(c)
        self.assertAlmostEqual(s.iloc[-1], 2.0)
        self.assertAlmostEqual(fit.iloc[-1], c.iloc[-1])

    def test_weekly_angle_has_no_lookahead(self):
        d = bars(1, n=400)
        full = ap.prep(d)
        cut = ap.prep(d.iloc[:300])
        t = cut.index[-1]
        self.assertTrue(np.isclose(full.loc[t, "wangle"], cut.loc[t, "wangle"], equal_nan=True))
        self.assertTrue(np.isclose(full.loc[t, "angle"], cut.loc[t, "angle"]))

    def test_walk_stop_first_when_both_touched(self):
        idx = pd.bdate_range("2026-01-01", periods=4)
        f = pd.DataFrame({"o": [100, 100, 100, 100], "h": [100, 110, 101, 101], "l": [100, 90, 99, 99], "c": [100, 100, 100, 100],
                          "atr": [2.0] * 4}, index=idx, dtype=float)
        R, k = ap.walk(f, 0, 1, 2.0, 2.0, 0.0)
        self.assertEqual(k, 1)
        self.assertAlmostEqual(R, -1.0)

    def test_walk_tp_and_short(self):
        idx = pd.bdate_range("2026-01-01", periods=4)
        f = pd.DataFrame({"o": [100, 100, 99, 97], "h": [100, 100.5, 99.5, 97.5], "l": [100, 99, 97, 95], "c": [100, 99.5, 97.5, 96],
                          "atr": [2.0] * 4}, index=idx, dtype=float)
        R, k = ap.walk(f, 0, -1, 2.0, 1.5, 0.0)   # TP = 100 - 3 = 97, stop = 104, risk 4
        self.assertEqual(k, 2)
        self.assertAlmostEqual(R, 0.75)

    def test_report_runs(self):
        raw = {f"S{i}": bars(i, drift=0.0006 * ((i % 5) - 2)) for i in range(8)}
        out = ap.report(raw, date(2025, 3, 1), seeds=2)
        self.assertTrue(any(x.startswith("- verdict") for x in out))


if __name__ == "__main__":
    unittest.main()
