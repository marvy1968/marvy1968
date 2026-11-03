import unittest

import numpy as np
import pandas as pd

from fxbot.research import FAMILIES
from fxbot.strategies import CandlePattern


def bars(n=400, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    c = 1.10 + np.cumsum(rng.normal(0, 0.0005, n))
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + 0.0003, np.minimum(o, c) - 0.0003
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)


class CandleTests(unittest.TestCase):
    def test_engulfing_and_pin(self):
        o = [1.10, 1.095, 1.085, 1.090]
        c = [1.095, 1.100, 1.088, 1.0915]
        h = [1.101, 1.101, 1.090, 1.092]
        l = [1.094, 1.093, 1.084, 1.087]
        m = pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC"))
        self.assertEqual(list(np.flatnonzero(CandlePattern("engulf").frame(m)["long"].to_numpy())), [1])
        self.assertEqual(list(np.flatnonzero(CandlePattern("pin").frame(m)["long"].to_numpy())), [3])

    def test_mirror_shooting_star(self):
        m = pd.DataFrame({"open": [1.1000], "high": [1.1035], "low": [1.0985], "close": [1.0995]},
                         index=pd.date_range("2024-01-01", periods=1, freq="h", tz="UTC"))
        self.assertTrue(CandlePattern("pin").frame(m)["short"].iloc[0])
        self.assertFalse(CandlePattern("pin").frame(m)["long"].iloc[0])

    def test_families_build_and_frame(self):
        m = bars()
        for name in ("fx_candle_box_h1", "fx_boxmr_ref_h1"):
            gran, make, grid = FAMILIES[name]
            self.assertEqual(gran, "H1")
            strat = make(**{k: v[0] for k, v in grid.items()})
            f = strat.frame(m)
            self.assertEqual(len(f), len(m))
            self.assertTrue(set(["long", "short", "stop"]).issubset(f.columns))


if __name__ == "__main__":
    unittest.main()
