import unittest

import numpy as np
import pandas as pd


class InBoxTests(unittest.TestCase):
    def _bars(self, n=900, seed=3):
        rng = np.random.default_rng(seed)
        idx = pd.date_range("2022-01-03", periods=n, freq="h", tz="UTC")
        c = 1.10 + 0.004 * np.sin(np.arange(n) / 9.0) + rng.normal(0, 0.0004, n)           # a sideways pair
        return pd.DataFrame({"open": np.r_[c[0], c[:-1]], "high": c + 0.0005, "low": c - 0.0005, "close": c, "volume": 1.0}, index=idx)

    def _base(self):
        from fxbot.strategies import RangeMR
        return RangeMR(bb_k=1.0, adx_max=100.0)

    def test_only_removes_signals_and_sets_box_stop_and_target(self):
        from fxbot.strategies import InBox
        from alpacabot.indicators import atr
        m = self._bars()
        base = self._base()
        f0, f = base.frame(m), InBox(base, tp_frac=0.5).frame(m)
        self.assertFalse((f["long"] & ~f0["long"]).any() or (f["short"] & ~f0["short"]).any())       # never adds a signal
        self.assertTrue((f["exit_long"] == f0["exit_long"]).all())                                    # the expert's exits stay
        n = int(f["long"].sum() + f["short"].sum())
        self.assertGreater(n, 0)
        a = atr(m, 14)
        top, bot = m["high"].shift(1).rolling(100).max(), m["low"].shift(1).rolling(100).min()
        c = m["close"]
        for i in np.flatnonzero((f["long"] | f["short"]).to_numpy()):
            long_ = bool(f["long"].iloc[i])
            if long_:
                self.assertAlmostEqual(float(f["stop"].iloc[i]), float(c.iloc[i] - (bot.iloc[i] - 0.5 * a.iloc[i])), places=9)   # Sell ALL: 0.5 ATR under the box
                self.assertAlmostEqual(float(f["target"].iloc[i]), float(top.iloc[i] - c.iloc[i]), places=9)                      # the far edge
                self.assertTrue(c.iloc[i] < f["zone_px"].iloc[i] < top.iloc[i])                                                 # TP1 between price and the edge
                self.assertLessEqual(float(c.iloc[i]), float((top.iloc[i] + bot.iloc[i]) / 2) + 1e-12)                          # lower half only
            else:
                self.assertAlmostEqual(float(f["stop"].iloc[i]), float((top.iloc[i] + 0.5 * a.iloc[i]) - c.iloc[i]), places=9)
                self.assertGreaterEqual(float(c.iloc[i]), float((top.iloc[i] + bot.iloc[i]) / 2) - 1e-12)                       # upper half only
            self.assertGreaterEqual(float(f["target"].iloc[i]), 0.75 * float(f["stop"].iloc[i]) - 1e-12)                        # R:R floor

    def test_far_edge_first_target_and_registered_check(self):
        from fxbot.strategies import InBox
        from fxbot.research import IN_BOX_EXPERTS, inbox_check
        m = self._bars()
        f = InBox(self._base(), tp_frac=1.0).frame(m)
        i = int(np.flatnonzero((f["long"] | f["short"]).to_numpy())[0])
        self.assertAlmostEqual(float(f["zone_px"].iloc[i]), float(f["zone2_px"].iloc[i]), places=9)
        self.assertEqual(set(IN_BOX_EXPERTS), {"lien", "rangemr", "consensus", "f0"})
        self.assertTrue(callable(inbox_check))


if __name__ == "__main__":
    unittest.main()
