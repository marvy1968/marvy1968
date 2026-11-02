import unittest
from datetime import date

import numpy as np
import pandas as pd

from alpacabot import anglepacer as ap
from alpacabot import stairstep as ss


def daily(seed, n=1700, drift=0.0006, start="2016-06-01"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    c = 50 * np.exp(np.cumsum(drift + rng.normal(0, 0.015, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.003, n))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.01, "low": np.minimum(o, c) * 0.99, "close": c,
                         "volume": rng.uniform(1e6, 3e6, n)}, index=idx)


def flat_A(n=80, px=100.0):
    """A minimal trade frame: flat prices, ATR 1, no pivots."""
    A = {k: np.full(n, px) for k in ("o", "h", "l", "c")}
    A.update({"n": n, "atr": np.ones(n), "sma": np.full(n, px), "bbu": np.full(n, px + 50), "bbl": np.full(n, px - 50), "k": np.full(n, 50.0),
              "d": np.full(n, 50.0), "rsi": np.full(n, 50.0)})
    for k in ("sl", "sl_i", "sl_prev", "sh", "sh_i", "sh_prev"):
        A[k] = np.full(n, np.nan)
    return A


class StairStepTests(unittest.TestCase):
    def test_future_data_does_not_change_past(self):
        full = ap.stock_daily(daily(1))
        cut = full.iloc[:1100].copy()
        a, b = ss.prep(full, ap.stock_weekly(full)), ss.prep(cut, ap.stock_weekly(cut))
        for k in ("stage", "wstr", "str", "k", "d", "rsi", "sl", "sl_i", "sh", "vw_anchored_1", "vw_anchored_-1", "vw_weekly_1", "sma", "atr"):
            np.testing.assert_allclose(a[k][:1100], b[k], equal_nan=True, rtol=1e-9, atol=1e-9, err_msg=k)
        sig_a = [ss.entry_signal(a, t, 1, "A", a["vw_anchored_1"]) for t in range(200, 1100)]
        sig_b = [ss.entry_signal(b, t, 1, "A", b["vw_anchored_1"]) for t in range(200, 1100)]
        self.assertEqual(sig_a, sig_b)

    def test_pivot_known_two_bars_later(self):
        lo = np.array([10, 9, 8, 7, 8, 9, 10, 11.0])
        p = ss._pivots(lo, lo + 1)
        self.assertTrue(np.isnan(p["sl"][4]))
        self.assertEqual(p["sl"][5], 7.0)
        self.assertEqual(p["sl_i"][5], 3)

    def test_stop_first_for_whole_size(self):
        A = flat_A()
        A["h"][1], A["l"][1] = 103.0, 97.0          # touches the 1R target and the stop: stop first
        R, k, why, nt = ss.manage(A, 1, 1, 100.0, 98.0, "A", 0.0, np.full(80, np.nan))
        self.assertEqual((k, nt, why), (1, 0, "stop"))
        self.assertAlmostEqual(R, -1.0)

    def test_partials_then_breakeven(self):
        A = flat_A()
        A["h"][1] = 102.1                          # +1R (2 pts) filled: 1/3 at 102, stop to breakeven
        A["l"][2] = 99.0                           # back below entry: rest out at breakeven (no costs)
        R, k, why, nt = ss.manage(A, 1, 1, 100.0, 98.0, "A", 0.0, np.full(80, np.nan))
        self.assertEqual((k, nt, why), (2, 1, "breakeven"))
        self.assertAlmostEqual(R, 1 / 3)

    def test_variant_b_quarters(self):
        A = flat_A()
        A["h"][1] = 101.6                          # 0.5R (101) and 0.75R (101.5) filled
        A["c"][1] = 100.0
        A["l"][2] = 98.5                           # stop moved to -0.5R = 99 -> rest out at 99
        R, k, why, nt = ss.manage(A, 1, 1, 100.0, 98.0, "B", 0.0, np.full(80, np.nan))
        self.assertEqual((k, nt), (2, 2))
        self.assertAlmostEqual(R, 0.25 * 0.5 + 0.25 * 0.75 + 0.5 * -0.5)

    def test_stop_rule(self):
        A = flat_A()
        A["sl"][5], A["sl_i"][5] = 98.5, 2
        st = ss.stop_for(A, 5, 1, 100.0)           # swing 98.25 (1.75 ATR) vs SMA-1ATR 99 (1 ATR, nearer and >= 1 ATR) -> 99
        self.assertAlmostEqual(st, 99.0)
        A["sma"][6] = 100.5
        self.assertAlmostEqual(ss.stop_for(A, 6, 1, 100.0), 99.0)   # no swing at t=6; SMA-1 ATR = 99.5 is < 1 ATR -> 1 ATR floor

    def test_end_to_end_synthetic(self):
        names = {f"S{i}": ("XLK" if i % 2 else "XLF") for i in range(6)}
        raw = {s: ap.stock_daily(daily(i, drift=0.0008 * ((i % 3) - 1))) for i, s in enumerate(names)}
        for e, sd in (("XLK", 50), ("XLF", 51), ("XLV", 52), ("SPY", 53)):
            raw[e] = ap.stock_daily(daily(sd, drift=0.0004))
        cal = raw["SPY"].index
        U = {s: ss.align(ss.prep(raw[s], ap.stock_weekly(raw[s])), cal) for s in names}
        etf = {e: raw[e]["close"].reindex(cal).to_numpy(float) for e in ("XLK", "XLF", "XLV")}
        spy = raw["SPY"]["close"].to_numpy(float)
        tr = ss.simulate(U, etf, spy, cal, lambda s, t: names[s], "A", (1, -1), 63, 2, "anchored", mode="none")
        self.assertTrue(len(tr) > 0)
        for t in tr:
            self.assertTrue(np.isfinite(t["R"]))
            self.assertGreaterEqual(t["k"], t["j"])
        self.assertLessEqual(max(sum(1 for t in tr if t["j"] <= d <= t["k"]) for d in range(len(cal))), 8)


if __name__ == "__main__":
    unittest.main()
