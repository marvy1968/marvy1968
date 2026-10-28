import unittest
from datetime import date

import numpy as np
import pandas as pd

from alpacabot import heatmap as hm

SECTORS = {"A1": "EA", "A2": "EA", "B1": "EB", "B2": "EB", "C1": "EC", "D1": "ED", "E1": "EE", "F1": "EF", "F2": "EF"}


def _daily(drift: dict, n=200):
    idx = pd.bdate_range("2024-01-01", periods=n)
    out = {}
    for k, g in drift.items():
        c = 100 * np.exp(np.cumsum(np.full(n, g)))
        out[k] = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=idx)
    return out


DRIFT = {"EA": 0.003, "EB": 0.002, "EC": 0.001, "ED": -0.001, "EE": -0.002, "EF": -0.003,
         "A1": 0.004, "A2": 0.002, "B1": 0.003, "B2": 0.001, "C1": 0.001, "D1": -0.001, "E1": -0.002, "F1": -0.002, "F2": -0.004}


class HeatmapTests(unittest.TestCase):
    def test_picks_strongest_in_hot_and_weakest_in_cold(self):
        c, _ = hm.frames(_daily(DRIFT))
        p = hm.picks(c, 100, SECTORS)
        self.assertEqual(p, {"A1": 1, "B1": 1, "C1": 1, "D1": -1, "E1": -1, "F2": -1})

    def test_news_filter_drops_slots(self):
        c, _ = hm.frames(_daily(DRIFT))
        day = c.index[99]
        neg = {e: pd.Series(e in ("EA", "EF"), index=[day]) for e in set(SECTORS.values())}
        p = hm.picks(c, 100, SECTORS, news_neg=neg)
        self.assertEqual(p, {"B1": 1, "C1": 1, "F2": -1})

    def test_trending_sleeve_makes_money_and_costs_bite(self):
        c, o = hm.frames(_daily(DRIFT))
        w0 = hm.run(c, o, SECTORS, date(2024, 1, 1), date(2025, 1, 1), cost_bps=0.0)
        w1 = hm.run(c, o, SECTORS, date(2024, 1, 1), date(2025, 1, 1), cost_bps=50.0)
        s0, s1 = hm.stats(w0), hm.stats(w1)
        self.assertGreater(s0["total"], 0.1)
        self.assertGreater(s0["long"], 0)
        self.assertGreater(s0["short"], 0)
        self.assertLess(s1["total"], s0["total"])

    def test_report_runs(self):
        lines = hm.report(_daily(DRIFT, 400), SECTORS, None, date(2025, 7, 1), start=date(2024, 3, 1), seeds=3)
        self.assertTrue(any("plain heat-map sleeve" in x for x in lines))
        self.assertTrue(any("heat map today" in x for x in lines))


if __name__ == "__main__":
    unittest.main()
