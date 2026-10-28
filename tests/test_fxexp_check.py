import datetime as dt
import unittest

import numpy as np
import pandas as pd

from fxbot import fxexp_check as fc


def _m5(closes, spread=0.0002):
    c = np.asarray(closes, float)
    idx = pd.date_range("2025-03-03 08:00", periods=len(c), freq="5min", tz="UTC")
    d = {}
    for side, off in (("b", -spread / 2), ("a", spread / 2)):
        d[side + "o"], d[side + "c"] = c + off, c + off
        d[side + "h"], d[side + "l"] = c + 0.0001 + off, c - 0.0001 + off
    d["volume"] = 100.0
    return pd.DataFrame(d, index=idx)


class ZerosTests(unittest.TestCase):
    def test_long_limit_fills_below_the_figure_and_hits_target(self):
        # price sinks to just above a round figure, well under its 20-bar mean, then rebounds
        base = np.r_[np.full(60, 1.1060), np.linspace(1.1060, 1.0985, 20), np.full(10, 1.0985), np.linspace(1.0985, 1.1100, 40)]
        tr = fc.zeros_trades(_m5(base), "EUR_USD")
        self.assertGreaterEqual(len(tr), 1)
        self.assertTrue(all(np.isfinite(t["r"]) for t in tr))
        self.assertGreater(tr[0]["r"], 1.5)                              # the rebound reaches the 2R target (a better-than-limit fill adds a little)
        self.assertLess(tr[0]["r"], 2.5)

    def test_no_signal_in_a_flat_market(self):
        self.assertEqual(fc.zeros_trades(_m5(np.full(200, 1.1050)), "EUR_USD"), [])


class EvaluateTests(unittest.TestCase):
    def test_pass_needs_every_window_oos_trades_pf_and_the_reference(self):
        today = dt.date(2026, 10, 27)
        w = fc.windows(today, True)
        mk = lambda a, n, r: [{"date": a + dt.timedelta(days=i % 300), "r": r if i % 3 else -1.0, "pair": "X"} for i in range(n)]   # noqa: E731
        t = mk(w["train 3y"][0], 60, 2.0) + mk(w["select 1y"][0], 60, 2.0) + mk(w["OOS last 1y"][0], 60, 2.0)
        ev = fc.evaluate(t, t, w, ref_avg_r=0.0)
        self.assertTrue(ev["pass"])
        self.assertFalse(fc.evaluate(t, t, w, ref_avg_r=5.0)["pass"])      # does not beat the coin-flip reference
        self.assertFalse(fc.evaluate(t[:-50], t[:-50], w, 0.0)["pass"])    # too few OOS trades


if __name__ == "__main__":
    unittest.main()


class StageTests(unittest.TestCase):
    def test_a_stage_needs_the_earlier_ones_trades_profit_and_the_placebo(self):
        today = dt.date(2026, 10, 27)
        win = [{"date": today - dt.timedelta(days=i % 80), "r": 1.0 if i % 3 else -1.0} for i in range(120)]
        rows = fc.stage_table(win, [[{"date": today - dt.timedelta(days=3), "r": 0.0}]], today)
        self.assertEqual([r["pass"] for r in rows], [True, True, True])
        rows = fc.stage_table(win, [[{"date": today - dt.timedelta(days=3), "r": 5.0}]], today)       # placebo best beats it: stop at stage 1
        self.assertEqual([r["pass"] for r in rows], [False, False, False])
        self.assertIn("stop here", fc.stage_lines("x", rows)[0])
