"""fxbot.refine: the session-window wrapper masks entries outside the hours; the pre-registered check runs and applies its rule."""
import unittest
from datetime import date

import numpy as np
import pandas as pd

from fxbot import refine as rf
from tests.test_fx_exits_muse import bidask


class EveryBarLong:
    max_bars = 5

    def frame(self, m):
        n = len(m)
        z = np.zeros(n, bool)
        L = np.zeros(n, bool)
        L[::7] = True
        return pd.DataFrame({"long": L, "short": z, "exit_long": z, "exit_short": z, "stop": np.full(n, 0.0040)}, index=m.index)


class RefineTests(unittest.TestCase):
    def test_window_masks_by_fill_hour(self):
        df = bidask(np.full(48, 1.10), start="2026-06-01")
        from fxbot.data import mid
        f = rf.SessionWindow(EveryBarLong(), 7, 17).frame(mid(df))
        fills = df.index[1:][f["long"].to_numpy()[:-1]]
        self.assertTrue(len(fills) > 0)
        self.assertTrue(all(7 <= t.hour < 17 for t in fills))

    def test_check_runs_and_rule(self):
        rng = np.random.default_rng(3)
        today = date(2026, 9, 1)
        data = {f"P{k}": bidask(1.10 + np.cumsum(rng.normal(0, 0.0008, 24 * 100)), start="2026-05-20") for k in range(2)}
        res = rf.check(EveryBarLong, lambda: rf.SessionWindow(EveryBarLong(), 7, 17), data, today, seeds=2)
        self.assertEqual(set(res["checks"]), {"30+ trades", "half 1 not worse", "half 2 better", "PF >= 1.0 at 2x spread", "beats random timing"})
        self.assertEqual(res["passes"], all(res["checks"].values()))
        self.assertLess(res["variant"]["all"]["trades"], res["live"]["all"]["trades"])
        out = rf.lines("t", res)
        self.assertTrue(out[-1].startswith("- PRE-REGISTERED VERDICT"))


if __name__ == "__main__":
    unittest.main()


class DailyTrendTests(unittest.TestCase):
    def test_trend_gate(self):
        from fxbot.data import mid
        up = bidask(1.0 + np.arange(24 * 30) * 0.0001, start="2026-05-01")
        f = rf.DailyTrend(EveryBarLong(), 5).frame(mid(up))
        self.assertFalse(f["long"].iloc[: 24 * 5].any())          # not enough completed days yet
        self.assertTrue(f["long"].iloc[24 * 10:].any())             # rising market: longs allowed
        dn = bidask(1.3 - np.arange(24 * 30) * 0.0001, start="2026-05-01")
        g = rf.DailyTrend(EveryBarLong(), 5).frame(mid(dn))
        self.assertFalse(g["long"].iloc[24 * 10:].any())            # falling market: no longs


class RetestCheckTests(unittest.TestCase):
    def test_retest_check_runs(self):
        rng = np.random.default_rng(5)
        today = date(2026, 9, 1)
        data = {f"P{k}": bidask(1.10 + np.cumsum(rng.normal(0, 0.0009, 24 * 110)), start="2026-05-10") for k in range(2)}
        out = rf.retest_check(data, today, {"be_at_r": 0.0}, {"be_at_r": 0.0, "min_rr": 2.5}, "t", seeds=2)
        self.assertTrue(out[-1].startswith("- PRE-REGISTERED VERDICT"))
        self.assertIn("playbook's own exits", out[0])

    def test_random_twin_hits_stop_or_target(self):
        df = bidask(np.r_[np.full(20, 1.10), np.linspace(1.10, 1.13, 200)], start="2026-06-01")
        r = rf._random_twin(df, {"side": "long", "risk": 0.002, "rr_target": 2.0}, np.random.default_rng(1), date(2026, 6, 1), date(2026, 6, 30))
        self.assertIn(r, (-1.0, 2.0))


class H4TrendTests(unittest.TestCase):
    def test_slope_gate_uses_completed_bars(self):
        import pandas as pd
        df = bidask(1.0 + np.arange(24 * 40) * 0.0001, start="2026-05-01")
        t_long = {"side": "long", "entry": pd.Timestamp("2026-06-05 10:00", tz="UTC")}
        t_short = {"side": "short", "entry": pd.Timestamp("2026-06-05 10:00", tz="UTC")}
        self.assertTrue(rf.h4_trend_ok("X", df, t_long))
        self.assertFalse(rf.h4_trend_ok("X", df, t_short))
        early = {"side": "long", "entry": pd.Timestamp("2026-05-03 10:00", tz="UTC")}
        self.assertFalse(rf.h4_trend_ok("X", df, early))          # SMA50 not warm yet


class SpreadGateTests(unittest.TestCase):
    def test_wide_spread_blocks(self):
        from fxbot.data import mid
        tight = bidask(np.full(60, 1.10), spread=0.0002)
        wide = bidask(np.full(60, 1.10), spread=0.0010)
        self.assertTrue(rf.SpreadGate(EveryBarLong(), tight, 0.10).frame(mid(tight))["long"].any())    # 0.0002 <= 10% of 0.0040
        self.assertFalse(rf.SpreadGate(EveryBarLong(), wide, 0.10).frame(mid(wide))["long"].any())     # 0.0010 > 0.0004

    def test_spread_ok(self):
        import pandas as pd
        df = bidask(np.full(30, 1.10), spread=0.0010)
        t = {"entry": df.index[5], "risk": 0.0040}
        self.assertFalse(rf.spread_ok("X", df, t))
        self.assertTrue(rf.spread_ok("X", df, {**t, "risk": 0.02}))

    def test_check_with_df_maker(self):
        rng = np.random.default_rng(4)
        data = {"P": bidask(1.10 + np.cumsum(rng.normal(0, 0.0008, 24 * 100)), start="2026-05-20")}
        res = rf.check(EveryBarLong, lambda df: rf.SpreadGate(EveryBarLong(), df, 0.10), data, date(2026, 9, 1), seeds=1)
        self.assertIn("checks", res)


class CurrencyCapTests(unittest.TestCase):
    def test_cap(self):
        import pandas as pd
        T = lambda inst, h0, h1: {"inst": inst, "entry": pd.Timestamp(f"2026-06-01 {h0:02d}:00", tz="UTC"),  # noqa: E731
                                  "exit": pd.Timestamp(f"2026-06-01 {h1:02d}:00", tz="UTC"), "r": 1.0}
        tr = [T("EUR_USD", 1, 10), T("GBP_USD", 2, 10), T("AUD_USD", 3, 10), T("EUR_GBP", 4, 10), T("NZD_USD", 11, 12)]
        kept = rf.currency_cap(tr, 2)
        names = [t["inst"] for t in kept]
        self.assertNotIn("AUD_USD", names)            # third open USD trade
        self.assertIn("EUR_GBP", names)               # EUR 1, GBP 1 open: allowed
        self.assertIn("NZD_USD", names)               # earlier ones closed by then
