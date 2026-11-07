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
