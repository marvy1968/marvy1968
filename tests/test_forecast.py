import tempfile
import unittest

import numpy as np
import pandas as pd

from alpacabot import forecast as fc


def bars(seed, n=700):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-06-01", periods=n, freq="B", tz="UTC") + pd.Timedelta(hours=4)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))
    o = c * np.exp(rng.normal(0, 0.004, n))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.008, "low": np.minimum(o, c) * 0.992, "close": c, "volume": 1.0}, index=idx)


class ForecastTests(unittest.TestCase):
    def test_trade_levels(self):
        o, h, l, c = (np.array([100.0]), np.array([103.0]), np.array([99.0]), np.array([102.0]))
        r = fc._trade(o, h, l, c, 1, 100.5, 2.0, (0.3, 1.0, 1.5), 1)       # open 100 <= limit 99.9? no -> limit fills at 99.9 (low 99)
        self.assertAlmostEqual(r, (99.9 + 3.0 - 99.9) / 2.0 - 2 * fc.COST * 99.9 / 2.0)
        self.assertIsNone(fc._trade(np.array([101.5]), np.array([102.0]), np.array([101.2]), c, 1, 103.0, 2.0, (1.0, 1.0, 1.5), 1))   # limit 101 never reached

    def test_calls_rank(self):
        t = pd.Timestamp("2026-10-28")
        df = pd.DataFrame({"symbol": list("ABCDEFG"), "p1": [0.7, 0.6, 0.52, 0.5, 0.48, 0.4, 0.3]}, index=[t] * 7)
        cl = fc.calls(df, "p1", k=2)
        self.assertEqual(cl, {(t, "A"): 1, (t, "B"): 1, (t, "G"): -1, (t, "F"): -1})

    def test_report_runs(self):
        daily = {s: bars(i) for i, s in enumerate(["SPY", "QQQ", "XLK", "AAA", "BBB", "CCC"])}
        with tempfile.TemporaryDirectory() as d:
            out = fc.report(daily, {"AAA": "XLK", "BBB": "XLK", "CCC": "XLK"}, (daily["SPY"].index[-1] + pd.Timedelta(days=1)).date(), d)
        self.assertTrue(any(x.startswith("- DAY") for x in out))
        self.assertTrue(any(x.startswith("- WEEK") for x in out))
        self.assertTrue(any(x.strip().startswith("AAA") for x in out))


if __name__ == "__main__":
    unittest.main()
