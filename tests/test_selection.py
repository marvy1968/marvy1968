import unittest

import numpy as np
import pandas as pd


def _daily(drift, seed, n=320):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-06-02", periods=n)
    t = np.arange(n)
    c = 100 * np.exp(drift * t / 100) + rng.normal(0, 0.3, n)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.4, "low": np.minimum(o, c) - 0.4, "close": c, "volume": 1e6}, index=idx)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.spy = _daily(0.05, 0)
        self.daily = {"UP1": _daily(0.4, 1), "UP2": _daily(0.3, 2), "DN1": _daily(-0.4, 3), "DN2": _daily(-0.3, 4), "MID": _daily(0.0, 5)}
        self.sof = {s: "XLK" for s in self.daily}

    def test_strongest_go_long_weakest_go_short(self):
        from alpacabot.selection import for_session, lists
        lok, sok = lists(self.daily, self.spy, self.sof, None, top=2)
        day = self.spy.index[-1].date()
        self.assertTrue(for_session(lok, "UP1", day) and for_session(lok, "UP2", day))
        self.assertTrue(for_session(sok, "DN1", day) and for_session(sok, "DN2", day))
        self.assertFalse(for_session(lok, "DN1", day) or for_session(sok, "UP1", day))

    def test_news_gate(self):
        from alpacabot.selection import for_session, lists
        idx = pd.DatetimeIndex(pd.to_datetime([x for x in self.spy.index.date]))
        bad = {"XLK": pd.Series(True, index=idx)}
        good = {"XLK": pd.Series(False, index=idx)}
        day = self.spy.index[-1].date()
        lok, sok = lists(self.daily, self.spy, self.sof, bad, top=2)
        self.assertFalse(for_session(lok, "UP1", day))               # negative sector news: no longs
        self.assertTrue(for_session(sok, "DN1", day))
        lok, sok = lists(self.daily, self.spy, self.sof, good, top=2)
        self.assertTrue(for_session(lok, "UP1", day))
        self.assertFalse(for_session(sok, "DN1", day))               # no negative news: no shorts

    def test_session_uses_the_previous_close(self):
        from alpacabot.selection import for_session
        ok = pd.DataFrame({"A": [True, False]}, index=pd.to_datetime(["2026-10-15", "2026-10-16"]))
        self.assertTrue(for_session(ok, "A", pd.Timestamp("2026-10-16").date()))
        self.assertFalse(for_session(ok, "A", pd.Timestamp("2026-10-19").date()))
        self.assertIsNone(for_session(ok, "B", pd.Timestamp("2026-10-19").date()))


if __name__ == "__main__":
    unittest.main()
