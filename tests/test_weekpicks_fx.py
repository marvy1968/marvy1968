import unittest
from datetime import date

import numpy as np
import pandas as pd

from fxbot import weekpicks_fx as wfx


def fx(seed, n=900):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-02", periods=n, freq="B", tz="UTC") + pd.Timedelta(hours=21)
    c = np.exp(np.cumsum(rng.normal(0, 0.005, n)))
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) * 1.003, np.minimum(o, c) * 0.997
    sp = 0.00005
    return pd.DataFrame({"bo": o - sp, "bh": h - sp, "bl": l - sp, "bc": c - sp, "ao": o + sp, "ah": h + sp, "al": l + sp, "ac": c + sp,
                         "volume": 1000}, index=idx)


class WeekPicksFxTests(unittest.TestCase):
    def test_short_trade_hits_target(self):
        t = pd.Timestamp("2026-10-01")
        n = 17
        o = np.full(n, 1.0)
        h, l, c = o + 0.002, o - 0.002, o.copy()
        l[11], h[11] = 0.97, 1.0005                                   # day after entry falls through the target, under the stop
        arr = {"X": ({t: 10}, o, h, l, c)}
        r = wfx.trade(arr, "X", t, -1, 0.01, 0.0)
        self.assertAlmostEqual(r, 0.015 / 0.002)

    def test_report_runs(self):
        raw = {s: fx(i) for i, s in enumerate(["EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY"])}
        out = wfx.report(raw, date(2026, 6, 1), seeds=2)
        self.assertTrue(any(x.startswith("- PURE") for x in out))
        self.assertTrue(any(x.startswith("- HYBRID") for x in out))


if __name__ == "__main__":
    unittest.main()
