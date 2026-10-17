import unittest

import numpy as np
import pandas as pd


class ReversalLevelsTests(unittest.TestCase):
    def _bars(self, days=15, seed=0):
        rng = np.random.default_rng(seed)
        idx = []
        for d in pd.bdate_range("2026-07-01", periods=days):
            idx += list(pd.date_range(d + pd.Timedelta(hours=9, minutes=30), periods=78, freq="5min", tz="America/New_York"))
        idx = pd.DatetimeIndex(idx)
        t = np.arange(len(idx))
        c = 100 + 3 * np.sin(t / 9.0) + rng.normal(0, 0.15, len(idx)).cumsum() * 0.2     # waves: clear tops and bottoms
        o = np.r_[c[0], c[:-1]]
        return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.1, "low": np.minimum(o, c) - 0.1, "close": c,
                             "volume": 1000.0}, index=idx)

    def test_study_finds_turns_and_tops_read_higher_than_bottoms(self):
        from alpacabot.levels import report_lines, study
        res = study({"AAA": self._bars(), "BBB": self._bars(seed=1)})
        self.assertGreater(res["turn_tops"], 10)
        self.assertGreater(res["turn_bottoms"], 10)
        top = res["at_turns"]["rsi14"]["tops, all"]["p25_med_p75"][1]
        bot = res["at_turns"]["rsi14"]["bottoms, all"]["p25_med_p75"][1]
        self.assertGreater(top, 55)
        self.assertLess(bot, 45)
        self.assertIn("AAA", res["per_stock"])
        self.assertTrue(any("odds" in ln for ln in report_lines(res)))

    def test_outcome_labels_first_touch_within_the_session(self):
        from alpacabot.levels import _outcomes
        h = np.array([10.0, 10.2, 12.5, 10.0])
        l = np.array([9.9, 9.8, 10.1, 9.0])
        c = np.array([10.0, 10.0, 12.0, 9.5])
        a = np.full(4, 1.0)
        day = np.array([1, 1, 1, 2])
        out = _outcomes(h, l, c, a, day, look=5, move=1.0)
        self.assertEqual(out[0], 1)          # bar 2's high 12.5 >= 11 before any low <= 9
        self.assertEqual(out[2], 0)          # next bar is another session


if __name__ == "__main__":
    unittest.main()
