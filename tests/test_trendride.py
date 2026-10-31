import unittest
from datetime import date

import numpy as np
import pandas as pd

from alpacabot import trendride as tr


def bars(seed, n=500, drift=0.002):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-10-01", periods=n)
    c = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.012, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.003, n))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.006, "low": np.minimum(o, c) * 0.994, "close": c, "volume": 1e6}, index=idx)


class TrendRideTests(unittest.TestCase):
    def test_walk_stop_target_and_sell(self):
        idx = pd.bdate_range("2026-10-01", periods=4)
        f = pd.DataFrame({"o": [100, 100, 101, 102], "h": [101, 101.5, 104, 103], "l": [99.5, 99.8, 100.5, 101],
                          "c": [100.5, 101, 103, 102.5], "ema20": [95.0] * 4}, index=idx, dtype=float)
        R, k = tr.walk(f, 0, 100.0, 98.0, 103.0, "ema20")
        self.assertEqual(k, 2)                                         # the TP zone on day 3 (not on the fill day)
        self.assertAlmostEqual(R, (103 * (1 - tr.COST) - 100 * (1 + tr.COST)) / (100 * (1 + tr.COST) - 98))

    def test_report_runs(self):
        out = tr.report({f"S{i}": bars(i) for i in range(5)}, date(2026, 9, 1), seeds=2)
        self.assertTrue(any(x.startswith("### A") for x in out) and any(x.startswith("### B") for x in out))


if __name__ == "__main__":
    unittest.main()


class PullbackFollowUpTests(unittest.TestCase):
    def test_pullback_report_runs(self):
        out = tr.pullback_report({f"S{i}": bars(i) for i in range(5)}, date(2026, 9, 1), seeds=2)
        self.assertTrue(any(x.startswith("- chosen") for x in out))
