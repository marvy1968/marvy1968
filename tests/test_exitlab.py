import datetime as dt
import unittest

import numpy as np
import pandas as pd

from alpacabot import exitlab as xl


def _bars(closes):
    c = np.asarray(closes, float)
    idx = pd.bdate_range("2024-01-02", periods=len(c), tz="America/New_York")
    return pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1e6}, index=idx)


def _sig(d, at, stop, tp=np.inf):
    s = pd.DataFrame(index=d.index)
    s["entry"] = False
    s.iloc[at, s.columns.get_loc("entry")] = True
    s["stop"], s["tp"], s["exit"] = stop, tp, False
    return s


class ExitLabTests(unittest.TestCase):
    def setUp(self):
        self.d = _bars([100.0] * 30 + [100 + i for i in range(1, 31)] + [130 - 3 * i for i in range(1, 15)])
        self.a, self.b = dt.date(2020, 1, 1), dt.date(2030, 1, 1)

    def test_fixed_target_hit(self):
        t = xl.simulate(self.d, _sig(self.d, 29, 95.0), "fixed_1r", self.a, self.b, slip_bps=0)
        self.assertEqual(t[0]["reason"], "target")
        self.assertAlmostEqual(t[0]["r"], 1.0, places=6)

    def test_time_exit(self):
        t = xl.simulate(self.d, _sig(self.d, 29, 95.0), "time_10", self.a, self.b, slip_bps=0)
        self.assertEqual(t[0]["reason"], "signal")
        self.assertEqual(t[0]["days"], 11)

    def test_atr_trail_locks_gain(self):
        t = xl.simulate(self.d, _sig(self.d, 29, 95.0), "atr_trail_3", self.a, self.b, slip_bps=0)
        self.assertEqual(t[0]["reason"], "stop")
        self.assertGreater(t[0]["r"], 3.0)                 # rode the run up, gave back only ~3 ATR

    def test_initial_stop_and_no_lookahead(self):
        d = _bars([100.0] * 30 + [99, 97, 94, 90])
        t = xl.simulate(d, _sig(d, 29, 95.0), "fixed_3r", self.a, self.b, slip_bps=0)
        self.assertEqual(t[0]["reason"], "stop")
        self.assertAlmostEqual(t[0]["r"], -1.25, places=6)   # 94 open gaps through the 95 stop: filled at the open, not the stop

    def test_short_test_shape(self):
        bars = {"SPY": self.d, "QQQ": self.d, "AAA": self.d}
        sh = xl.short_test(bars, self.d["close"], dt.date(2024, 4, 20))
        self.assertEqual(set(sh), set(xl.STRATEGIES))
        self.assertEqual(set(sh["topdown"]), set(xl.EXITS))
        self.assertFalse(sh["topdown"]["own"]["pass"])


if __name__ == "__main__":
    unittest.main()


class CreditGateTests(unittest.TestCase):
    def test_gate_off_when_junk_bonds_lag_treasuries(self):
        from alpacabot.research import credit_gate
        idx = pd.bdate_range("2024-01-02", periods=120, tz="America/New_York")
        hyg = pd.DataFrame({"close": np.r_[np.linspace(100, 110, 80), np.linspace(110, 95, 40)]}, index=idx)
        ief = pd.DataFrame({"close": np.full(120, 100.0)}, index=idx)
        g = credit_gate(hyg, ief)
        self.assertTrue(bool(g.iloc[10]))                  # no 50-day history yet: open
        self.assertTrue(bool(g.iloc[79]))                  # HYG/IEF rising: open
        self.assertFalse(bool(g.iloc[-1]))                 # falling: 20-day under the 50-day, closed


class RankedTopDownTests(unittest.TestCase):
    def test_rank_reads_the_supplied_series_and_keeps_entries(self):
        from alpacabot.research import _RankedTopDown
        from alpacabot.strategies import GatedSectorTopDown, SectorTopDown
        d = _bars(np.r_[np.linspace(100, 100, 60), np.linspace(100, 160, 80)])
        gated = GatedSectorTopDown(SectorTopDown(d["close"]), None)
        rk = pd.Series(np.arange(len(d), dtype=float), index=d.index)
        r = _RankedTopDown(gated, rk)
        f = r.frame(d)
        self.assertTrue((f["entry"] == gated.frame(d)["entry"]).all())
        self.assertEqual(r.rank(f, 10), 10.0)
        self.assertLess(r.rank(f.assign(rank_alt=np.nan), 10), -1e8)
