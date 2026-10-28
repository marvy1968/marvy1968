import datetime as dt
import unittest

import numpy as np
import pandas as pd

from fxbot import consensus as cz


class ConsensusTests(unittest.TestCase):
    def test_zones_average_each_experts_median_and_only_trend_experts_sell(self):
        per = {"lien_h1": ([-2.0] * 30, [2.0] * 30), "connors_rsi2": ([-1.0] * 30, [1.0] * 30), "macd_200ema": ([1.0] * 30, [-1.0] * 30),
               "f0_h4": ([5.0] * 5, [])}                                          # too few signals: left out
        z = cz.zones(per)
        self.assertEqual(z["n_buy_experts"], 3)
        self.assertAlmostEqual(np.mean(z["buy_zone"]), (-2.0 - 1.0 + 1.0) / 3)
        self.assertEqual(z["n_sell_experts"], 1)                                 # only macd_200ema is a trend expert
        self.assertAlmostEqual(np.mean(z["sell_zone"]), -1.0)
        self.assertAlmostEqual(np.mean(z["sell_zone_all"]), (2.0 + 1.0 - 1.0) / 3)
        self.assertAlmostEqual(z["buy_zone"][1] - z["buy_zone"][0], 2 * cz.HALF_WIDTH)

    def test_trade_buys_in_the_buy_zone_and_sells_in_the_sell_zone(self):
        n = 200
        idx = pd.date_range("2026-10-14", periods=n, freq="h", tz="UTC")
        c = 1.1 + 0.002 * np.sin(np.arange(n) / 6.0)
        d = {}
        for s, off in (("b", -0.00002), ("a", 0.00002)):
            d[s + "o"], d[s + "c"], d[s + "h"], d[s + "l"] = c + off, c + off, c + 0.0003 + off, c - 0.0003 + off
        df = pd.DataFrame(d, index=idx)
        tr = cz.trade_zones(df, (-1.5, -0.5), (0.5, 1.5), dt.date(2026, 10, 1), dt.date(2026, 11, 1), side_filter=1)
        self.assertGreater(len(tr), 0)
        self.assertTrue(all(t["side"] == 1 for t in tr))
        self.assertGreater(sum(t["r"] for t in tr), 0)                             # a clean oscillation pays buy-low / sell-high
        self.assertEqual(cz.trade_zones(df, (-1.0, 1.0), (0.5, 1.5), dt.date(2026, 10, 1), dt.date(2026, 11, 1)), [])   # overlapping zones: no trades


if __name__ == "__main__":
    unittest.main()


class DailyGroupTests(unittest.TestCase):
    def test_groups_split_trend_and_reversion(self):
        per = {"connors_rsi2": ([-1.0] * 30, [1.0] * 30), "box_h1": ([-2.0] * 30, [2.0] * 30), "macd_200ema": ([1.0] * 30, [-1.0] * 30)}
        g = cz.group_zones(per)
        self.assertAlmostEqual(np.mean(g["reversion"]["buy"]), -1.5)
        self.assertAlmostEqual(np.mean(g["reversion"]["sell"]), 1.5)
        self.assertAlmostEqual(np.mean(g["trend"]["buy"]), 1.0)
        self.assertAlmostEqual(np.mean(g["trend"]["sell"]), -1.0)

    def test_zones_in_the_wrong_order_give_no_trades(self):
        idx = pd.date_range(end="2026-10-27 20:00", periods=800, freq="h", tz="UTC")
        c = 1.1 + 0.001 * np.sin(np.arange(800) / 30.0)
        d = {}
        for s, off in (("b", -0.00002), ("a", 0.00002)):
            d[s + "o"], d[s + "c"], d[s + "h"], d[s + "l"] = c + off, c + off, c + 0.0003 + off, c - 0.0003 + off
        df = pd.DataFrame(d, index=idx)
        a, b = dt.date(2026, 10, 1), dt.date(2026, 10, 27)
        self.assertEqual(cz.trade_group(df, "reversion", (1.0, 1.5), (-1.5, -1.0), a, b), [])
        self.assertEqual(cz.trade_group(df, "trend", (-1.5, -1.0), (1.0, 1.5), a, b), [])
