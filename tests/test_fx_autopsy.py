import unittest
from datetime import date

import numpy as np
import pandas as pd

from fxbot import autopsy as ap


class AutopsyTests(unittest.TestCase):
    def test_pass_and_fail_rule(self):
        rng = np.random.default_rng(1)
        rows = []
        for yr in (2018, 2023):
            for k in range(300):
                sess = ["asia", "london", "overlap", "ny"][k % 4]
                r = -1.0 if sess == "asia" else (1.2 if k % 3 else -1.0)      # every asia trade loses, in both halves
                rows.append({"date": date(yr, 3, 1), "r": r, "session": sess, "weekday": "Mon",
                             "atr_pct": rng.random(), "box_atr": rng.random(), "edge": rng.random(), "spread_atr": rng.random()})
        lines = ap.autopsy(pd.DataFrame(rows), date(2026, 11, 4))
        self.assertTrue(any("session = asia" in l for l in lines))
        self.assertTrue(lines[-1].endswith("PASS"))

    def test_noise_fails(self):
        rng = np.random.default_rng(2)
        rows = [{"date": date(2018 if k < 400 else 2023, 3, 1), "r": float(rng.choice([1.0, -1.0])),
                 "session": ["asia", "london", "overlap", "ny"][rng.integers(4)], "weekday": "Tue",
                 "atr_pct": rng.random(), "box_atr": rng.random(), "edge": rng.random(), "spread_atr": rng.random()} for k in range(800)]
        lines = ap.autopsy(pd.DataFrame(rows), date(2026, 11, 4))
        self.assertTrue(lines[-1].endswith("FAIL"))

    def test_sessions(self):
        self.assertEqual([ap._session(h) for h in (20, 2, 3, 8, 12, 17)], ["asia", "asia", "london", "overlap", "ny", "roll"])

    def test_traits_from_real_strategy(self):
        idx = pd.date_range("2024-01-01", periods=900, freq="h", tz="UTC")
        rng = np.random.default_rng(5)
        c = 1.10 + 0.004 * np.sin(np.arange(900) / 15) + rng.normal(0, 0.0003, 900)
        o = np.r_[c[0], c[:-1]]
        h, l = np.maximum(o, c) + 0.0004, np.minimum(o, c) - 0.0004
        d = {}
        for k, v in (("o", o), ("h", h), ("l", l), ("c", c)):
            d["b" + k], d["a" + k] = v - 0.0001, v + 0.0001
        df = pd.DataFrame(d, index=idx)
        from fxbot import live as fl
        tr = ap.trades_with_traits(fl.STRATS["boxmr_h1"]["make"], {"EUR_USD": df}, date(2024, 1, 1), date(2025, 1, 1))
        if len(tr):
            self.assertTrue(set(ap.TRAITS).issubset(tr.columns))
            self.assertTrue(((tr["edge"] >= -0.5) & (tr["edge"] <= 1.5)).all())


if __name__ == "__main__":
    unittest.main()
