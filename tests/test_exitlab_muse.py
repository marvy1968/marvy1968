import unittest
from datetime import date

import numpy as np
import pandas as pd

from alpacabot import exitlab as xl


def daily(c):
    c = np.asarray(c, float)
    o = np.r_[c[0], c[:-1]]
    idx = pd.bdate_range("2026-01-05", periods=len(c))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.2, "low": np.minimum(o, c) - 0.2, "close": c, "volume": 1e6}, index=idx)


def one(d, ex, at=20, stop_gap=2.0):
    sig = pd.DataFrame(index=d.index)
    e = np.zeros(len(d), bool)
    e[at] = True
    sig["entry"], sig["stop"], sig["tp"], sig["exit"] = e, d["close"] - stop_gap, np.inf, False
    return xl.simulate(d, sig, ex, date(2026, 1, 1), date(2027, 1, 1), slip_bps=0.0)


class StockMuseExitTests(unittest.TestCase):
    def test_williams_and_crabel(self):
        c = np.r_[np.full(21, 100.0), 99.5, 100.5, 101.0, np.full(10, 101.0)]
        w = one(daily(c), "williams")[0]
        self.assertGreater(w["r"], 0)
        cr = one(daily(c), "crabel")[0]
        self.assertLessEqual(cr["days"], 2)

    def test_raschke_flat_day6(self):
        c = np.r_[np.full(21, 100.0), np.linspace(100, 104, 30)]
        r = one(daily(c), "raschke")[0]
        self.assertLessEqual(r["days"], 7)
        self.assertGreater(r["r"], 0)

    def test_ponsi_locks_profit(self):
        c = np.r_[np.full(21, 100.0), np.linspace(100, 110, 20), np.linspace(110, 97, 20)]
        self.assertGreater(one(daily(c), "ponsi")[0]["r"], 1.0)

    def test_turtle_10day_low(self):
        c = np.r_[np.full(21, 100.0), np.linspace(100, 106, 10), np.linspace(106, 99, 15)]
        r = one(daily(c), "turtle")[0]
        self.assertEqual(r["reason"], "signal")

    def test_wstair(self):
        c = np.r_[np.full(21, 100.0), 101, 102, 103, 102.5, 101.5, np.full(10, 101.5)]
        self.assertGreater(one(daily(c), "wstair")[0]["r"], 0)

    def test_registered(self):
        self.assertEqual(len(set(xl.EXITS + xl.MUSE_EXITS)), 21)

    def test_topdown_spyreg_blocks_in_spy_downtrend(self):
        idx = pd.bdate_range("2024-01-01", periods=400)
        rng = np.random.default_rng(1)
        c = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 400)))
        d = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6}, index=idx)
        spy = pd.Series(np.r_[np.linspace(400, 500, 200), np.linspace(500, 400, 200)], index=idx)
        base, gated = xl.signals("topdown", d, spy)["entry"], xl.signals("topdown_spyreg", d, spy)["entry"]
        self.assertTrue((gated <= base).all())                                     # only removes entries
        self.assertEqual(int(gated.iloc[221:].sum()), 0)                           # none while SPY is under a falling SMA20
        self.assertEqual(int(gated.iloc[:200].sum()), int(base.iloc[:200].sum()))  # untouched while SPY rises


if __name__ == "__main__":
    unittest.main()
