import unittest
from datetime import date

import numpy as np
import pandas as pd

from alpacabot import pacer


def bars(seed, n=900, drift=0.001):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    c = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.015, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.003, n))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.008, "low": np.minimum(o, c) * 0.992, "close": c,
                         "volume": rng.uniform(1e6, 3e6, n)}, index=idx)


class PacerTests(unittest.TestCase):
    def test_walk_half_at_t1_then_t2(self):
        idx = pd.bdate_range("2026-01-01", periods=5)
        f = pd.DataFrame({"o": [100, 100, 101, 103, 104], "h": [100, 101, 103, 106, 105], "l": [99, 99.5, 100.5, 102.5, 103],
                          "c": [100, 100.5, 102.5, 105, 104], "atr": [1.0] * 5, "rng20": [5.0] * 5}, index=idx, dtype=float)
        R, k = pacer.walk(f, 0, measured=False)
        e = 100 * (1 + pacer.COST)
        self.assertEqual(k, 3)
        self.assertAlmostEqual(R, 0.5 * ((e + 2) * (1 - pacer.COST) - e) / 1.5 + 0.5 * ((e + 3) * (1 - pacer.COST) - e) / 1.5, places=6)

    def test_report_runs(self):
        raw = {f"S{i}": bars(i, drift=0.0005 * (i % 4)) for i in range(10)}
        raw["SPY"] = bars(99)
        out = pacer.report(raw, date(2026, 6, 1), seeds=2)
        self.assertTrue(any(x.startswith("- verdict") for x in out))


if __name__ == "__main__":
    unittest.main()
