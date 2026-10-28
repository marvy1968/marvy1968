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


class WalkForwardTests(unittest.TestCase):
    def test_quarters_cover_the_range(self):
        q = cz.quarters(dt.date(2025, 1, 1), dt.date(2025, 8, 10))
        self.assertEqual(q, [(dt.date(2025, 1, 1), dt.date(2025, 3, 31)), (dt.date(2025, 4, 1), dt.date(2025, 6, 30)), (dt.date(2025, 7, 1), dt.date(2025, 8, 10))])

    def test_z_at_takes_the_bar_at_or_before_the_signal(self):
        idx = pd.date_range("2026-01-05", periods=5, freq="h", tz="UTC")
        z = pd.Series([0.0, 1.0, 2.0, 3.0, 4.0], index=idx)
        t = pd.DatetimeIndex([idx[2] + pd.Timedelta(minutes=30), idx[0] - pd.Timedelta(hours=1)])
        out = cz.z_at(t, z)
        self.assertEqual(list(out.values), [2.0])


class FinancingTests(unittest.TestCase):
    def test_multi_day_holds_pay_financing(self):
        from fxbot.engine import FIN_PER_NIGHT
        n = 900
        idx = pd.date_range(end="2026-10-27 20:00", periods=n, freq="h", tz="UTC")
        c = 1.1 + 0.00002 * np.arange(n)                                   # slow steady rise: a trend long that is held for days
        d = {}
        for s, off in (("b", -0.00002), ("a", 0.00002)):
            d[s + "o"], d[s + "c"], d[s + "h"], d[s + "l"] = c + off, c + off, c + 0.0002 + off, c - 0.0002 + off
        df = pd.DataFrame(d, index=idx)
        tr = cz.trade_group(df, "trend", (0.001, 99.0), (-99.0, -0.001), dt.date(2026, 10, 1), dt.date(2026, 10, 27), "EUR_USD")
        self.assertTrue(tr)
        self.assertGreater(tr[0]["nights"], 0)
        self.assertGreater(FIN_PER_NIGHT, 0)
