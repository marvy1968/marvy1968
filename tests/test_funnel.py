import unittest

import numpy as np
import pandas as pd


def _daily(n=400, drift=0.15, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-02", periods=n)
    t = np.arange(n)
    c = 100 + drift * t + 3 * np.sin(t / 5.0) + rng.normal(0, 0.4, n)
    o = np.r_[c[0], c[:-1]] + rng.normal(0, 0.2, n)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.6, "low": np.minimum(o, c) - 0.6, "close": c,
                         "volume": 1e6 * (1 + 0.5 * (np.sin(t / 3.0) > 0.6))}, index=idx)


class FunnelTests(unittest.TestCase):
    def test_uptrend_gives_longs_only_with_stop_below_and_tp_above(self):
        from alpacabot.funnel import frame
        f = frame(_daily())
        longs = f[f["long_entry"]]
        self.assertGreater(len(longs), 0)
        self.assertFalse(f["short_entry"].any())                    # stage 2 all the way: no shorts
        self.assertTrue((longs["long_stop"] < longs["close"]).all())
        self.assertTrue((longs["long_tp"] > longs["close"]).all())
        dist = longs["close"] - longs["long_stop"]
        self.assertTrue((dist >= longs["atr"] * 0.999).all() and (dist <= longs["atr"] * 3.001).all())   # 1-3 ATR: never the wrong side
        self.assertTrue(np.allclose(longs["long_tp"] - longs["close"], 1.5 * dist))

    def test_downtrend_gives_shorts_mirrored(self):
        from alpacabot.funnel import frame
        f = frame(_daily(drift=-0.15, seed=2))
        self.assertGreater(int(f["short_entry"].sum()), 0)
        self.assertFalse(f["long_entry"].any())
        s = f[f["short_entry"]]
        self.assertTrue((s["short_stop"] > s["close"]).all() and (s["short_tp"] < s["close"]).all())

    def test_backtest_runs_and_reports(self):
        from alpacabot.funnel import backtest
        d = _daily()
        r = backtest({"AAA": d}, d.index[200].date(), d.index[-1].date())
        self.assertGreater(r["trades"], 0)
        self.assertEqual(r["shorts"], 0)
        self.assertEqual(sum(r["exits"].values()), r["trades"])


if __name__ == "__main__":
    unittest.main()
