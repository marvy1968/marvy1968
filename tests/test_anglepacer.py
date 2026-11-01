import unittest
from datetime import date

import numpy as np
import pandas as pd

from alpacabot import anglepacer as ap


def daily(seed, n=1500, drift=0.0006):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-02", periods=n)
    c = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.015, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.003, n))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.01, "low": np.minimum(o, c) * 0.99, "close": c, "volume": 1e6}, index=idx)


def fiveminute(seed, days=60):
    rng = np.random.default_rng(seed)
    idx = []
    for d in pd.bdate_range("2024-01-02", periods=days):
        idx += list(pd.date_range(d + pd.Timedelta(hours=9, minutes=30), periods=78, freq="5min"))
    idx = pd.DatetimeIndex(idx).tz_localize(ap.NY)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, len(idx))))
    return pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 1.0}, index=idx)


class AnglePacerTests(unittest.TestCase):
    def test_ols_line(self):
        c = np.arange(50, dtype=float) * 2 + 5
        slope, r2, fit = ap.ols(c, 20)
        self.assertAlmostEqual(slope[-1], 2.0)
        self.assertAlmostEqual(r2[-1], 1.0)
        self.assertAlmostEqual(fit[-1], c[-1])
        self.assertTrue(np.isnan(slope[18]))

    def test_future_data_does_not_change_past_signals(self):
        raw = {f"S{i}": daily(i) for i in range(3)}
        cut = {s: d.iloc[:900] for s, d in raw.items()}
        full_dd = {s: ap.stock_daily(d) for s, d in raw.items()}
        cut_dd = {s: ap.stock_daily(d) for s, d in cut.items()}
        mf = ap.build(full_dd, {s: ap.stock_weekly(d) for s, d in full_dd.items()}, "stock")
        mc = ap.build(cut_dd, {s: ap.stock_weekly(d) for s, d in cut_dd.items()}, "stock")
        for A, B in zip(mf["S"], mc["S"]):
            for n in ap.NS:
                for k in ("str", "hstr", "r2", "acc", "fit"):
                    np.testing.assert_allclose(A["feat"][n][k][:B["n"]], B["feat"][n][k], equal_nan=True, rtol=1e-9, atol=1e-12)
            np.testing.assert_array_equal(ap.direction(A, 0.05, 20, 0.35)[:B["n"]], ap.direction(B, 0.05, 20, 0.35))
        tf, _ = ap.simulate(mf, (0.05, 20, 0.35))
        tc, _ = ap.simulate(mc, (0.05, 20, 0.35))
        last = min(B["tc"][-1] for B in mc["S"])
        done = lambda tr: [(t["sym"], t["i"], t["j"], round(t["R"], 9)) for t in tr if t["t_out"] < last - 25 * 86400e9]
        self.assertEqual(done(tf), done(tc))

    def test_hourly_buckets(self):
        h = ap.stock_hourly(fiveminute(1, days=2))
        self.assertEqual(len(h), 14)
        last = pd.Timestamp(h["tclose"].iloc[6], tz="UTC").tz_convert(ap.NY)
        self.assertEqual((last.hour, last.minute), (16, 0))

    def test_weekly_close_not_before_friday(self):
        dd = ap.stock_daily(daily(2, n=30))
        w = ap.stock_weekly(dd)
        for t in w["tclose"]:
            ts = pd.Timestamp(t, tz="UTC").tz_convert(ap.NY)
            self.assertEqual((ts.dayofweek, ts.hour), (4, 16))

    def test_walk_stop_first_and_fill_bar_no_tp(self):
        A = {"o": [100, 100, 100], "h": [100, 110, 110], "l": [100, 99.5, 95], "c": [100, 100, 100], "cabs": np.zeros(3), "cfrac": 0.0,
             "tc": [0, 1, 2], "n": 3}
        self.assertAlmostEqual(ap.walk(A, 1, 1, 100.0, 96.0, 105.0, 1.0), -1.0)   # fill bar 1 touches TP (not counted), bar 2 touches both -> stop

    def test_end_to_end_synthetic(self):
        raw = {f"S{i}": daily(i, drift=0.0008 * ((i % 3) - 1)) for i in range(6)}
        dd = {s: ap.stock_daily(d) for s, d in raw.items()}
        mk = ap.build(dd, {s: ap.stock_weekly(d) for s, d in dd.items()}, "stock")
        r = ap.evaluate(mk, "synthetic", date(2023, 10, 1), seeds=2, log=lambda m: None)
        if "error" not in r:
            self.assertIn("pass", r)
            self.assertTrue(ap.row(r).startswith("| synthetic"))


if __name__ == "__main__":
    unittest.main()
