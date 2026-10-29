import unittest
from datetime import date

import numpy as np
import pandas as pd

from alpacabot import jdub as jd


def session(prices, day="2026-10-27"):
    idx = pd.date_range(f"{day} 09:30", periods=len(prices), freq="1min", tz="America/New_York").tz_convert("UTC")
    rows = []
    for o, h, l, c in prices:
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000})
    return pd.DataFrame(rows, index=idx)


def long_day():
    p = [(100, 100.5, 99.8, 100.2)] * 5                        # opening range 99.8 - 100.5
    p += [(100.2, 100.4, 100.1, 100.3)] * 3
    p += [(100.3, 101.0, 100.3, 100.9), (100.9, 101.2, 100.8, 101.1)]   # break and run (FVG: 100.8 > 100.4)
    p += [(101.1, 101.1, 100.4, 100.6)]                         # retest touches 100.5 ... closes above (100.6 > 100.5)
    p += [(100.6, 100.7, 100.5, 100.65)]
    p += [(100.65 + 0.05 * k, 100.75 + 0.05 * k, 100.6 + 0.05 * k, 100.7 + 0.05 * k) for k in range(40)]
    return session(p)


class JdubTests(unittest.TestCase):
    def test_setup_is_the_retest_not_the_breakout(self):
        g = jd.sessions(long_day())[date(2026, 10, 27)]
        s = jd.day_setups(g)
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["side"], 1)
        self.assertEqual(s[0]["retest"], 10)
        self.assertAlmostEqual(s[0]["stop"], 100.4)
        self.assertTrue(s[0]["fvg"])

    def test_walk_hits_target(self):
        g = jd.sessions(long_day())[date(2026, 10, 27)]
        s = jd.day_setups(g)[0]
        r = jd.walk(g, s["i"], 1, s["stop"], 2.0, 0.0)
        self.assertAlmostEqual(r, 2.0)
        self.assertLess(jd.walk(g, s["i"], 1, s["stop"], 2.0, 10.0), 2.0)

    def test_hybrid_blocks_long_against_down_trend(self):
        days = {"AAA": jd.sessions(long_day())}
        d = date(2026, 10, 27)
        plain = jd.run(days, d, d, "JDUB", 2.0, 0.0, {}, {}, lambda ts: False)
        blocked = jd.run(days, d, d, "H1", 2.0, 0.0, {"AAA": {d: -1}}, {}, lambda ts: False)
        self.assertEqual((len(plain), len(blocked)), (1, 0))

    def test_ride_hybrid_rules(self):
        from alpacabot.trendzones import to_bidask
        idx = pd.date_range("2026-10-27 09:30", periods=78, freq="5min", tz="America/New_York").tz_convert("UTC")
        p = [(100.5, 101, 100, 100.5)] * 6                                       # opening range 100 - 101 (9:30-10:00)
        p += [(100.5, 102, 101.2, 102)]                                          # 10:00 bar: hard move, breaks OR, FVG (101.2 > 101)
        p += [(102, 102.2, 101.3, 101.8)]                                        # pulls back to 101.4 -> RT fills there
        p += [(101.8 + 0.1 * k, 102 + 0.1 * k, 101.7 + 0.1 * k, 101.9 + 0.1 * k) for k in range(70)]
        m5 = pd.DataFrame([{"open": o, "high": h, "low": l, "close": c, "volume": 1} for o, h, l, c in p], index=idx)
        ba = to_bidask(m5, 0.0)
        td = np.zeros(78, int)
        td[6] = 1
        at, rv = np.full(78, 4.0), np.zeros(78, int)
        base = jd.ride_hybrid(m5, ba, td, at, rv, set())
        rt = jd.ride_hybrid(m5, ba, td, at, rv, {"or", "fvg", "rt"})
        self.assertEqual((len(base), len(rt)), (1, 1))
        self.assertAlmostEqual(rt[0]["r"] - base[0]["r"], 0.3)                  # filled 0.6 cheaper on a 2.0 stop
        self.assertAlmostEqual(jd.ride_hybrid(m5, ba, td, at, rv, {"2r"})[0]["r"], 2.0)
        m5b = m5.copy()
        m5b.iloc[6, m5b.columns.get_loc("close")] = 100.9                        # no OR break -> no trade
        self.assertEqual(jd.ride_hybrid(m5b, to_bidask(m5b, 0.0), td, at, rv, {"or"}), [])

    def test_report_runs(self):
        m1 = {"AAA": long_day(), "SPY": long_day()}
        lines = jd.report(m1, {}, date(2026, 10, 28), seeds=2)
        self.assertTrue(any(x.startswith("- H3 2R") for x in lines))


if __name__ == "__main__":
    unittest.main()
