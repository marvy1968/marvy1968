import unittest
from datetime import date

import numpy as np
import pandas as pd

from alpacabot import daytp


def bars(seed, n=800, drift=0.0015):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-06-01", periods=n)
    c = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.012, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.003, n))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.006, n)))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.006, n)))
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": rng.uniform(1e6, 3e6, n)}, index=idx)


class DayTpTests(unittest.TestCase):
    def test_trade_tp_and_stop(self):
        r = pd.Series({"o_n": 100.0, "h_n": 103.0, "l_n": 99.5, "c_n": 101.0, "atr": 2.0})
        r = type("R", (), r.to_dict())
        self.assertAlmostEqual(daytp._trade(r, 1.0), (102 * (1 - daytp.COST) - 100 * (1 + daytp.COST)) / 2)
        r.l_n = 97.9
        self.assertAlmostEqual(daytp._trade(r, 1.0), (98 * (1 - daytp.COST) - 100 * (1 + daytp.COST)) / 2)

    def test_report_runs(self):
        raw = {f"S{i}": bars(i) for i in range(4)}
        out = daytp.report(raw, date(2026, 8, 1))
        self.assertTrue(any(x.startswith("- verdict") for x in out))


if __name__ == "__main__":
    unittest.main()
