import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import alpacabot.swing_paper as sp


def _daily(drift, n=420, seed=0):
    idx = pd.bdate_range("2025-01-01", periods=n)
    c = 100 * np.exp(np.cumsum(np.full(n, drift) + np.random.default_rng(seed).normal(0, 0.004, n)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.003, "low": np.minimum(o, c) * 0.997, "close": c, "volume": 1e6}, index=idx)


class SwingTrendFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = sp.SwingRunner(None, state_dir=self.tmp.name, base_equity=50000.0)
        self.bars = {"UP": _daily(0.003), "DN": _daily(-0.003), "UP2": _daily(0.003, seed=2)}
        self._db, self._tf = sp.daily_bars, sp.TREND_FILTERS
        sp.daily_bars = lambda sym, now, fetch=None, days=260: self.bars[sym]
        self.now = datetime(2026, 10, 28, 16, 30, tzinfo=ZoneInfo("America/New_York"))

    def tearDown(self):
        sp.daily_bars, sp.TREND_FILTERS = self._db, self._tf
        self.tmp.cleanup()

    def test_entries_need_a_long_up_trend_and_reversals_exit(self):
        sp.TREND_FILTERS = {"long": True, "reversal": True}
        held = {"DN": {"qty": 5, "entry": 90.0, "side": 1}, "UP2": {"qty": 5, "entry": 90.0, "side": 1}}
        exits, entries = self.r.trend_filters(held, [], [{"symbol": "UP"}, {"symbol": "DN"}], self.now)
        self.assertEqual([e["symbol"] for e in entries], ["UP"])
        self.assertEqual(exits, ["DN"])

    def test_off_changes_nothing(self):
        sp.TREND_FILTERS = None
        held = {"DN": {"qty": 5, "entry": 90.0, "side": 1}}
        self.assertEqual(self.r.trend_filters(held, [], [{"symbol": "DN"}], self.now), ([], [{"symbol": "DN"}]))


if __name__ == "__main__":
    unittest.main()
