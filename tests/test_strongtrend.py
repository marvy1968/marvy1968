import unittest
from datetime import date

import numpy as np
import pandas as pd

from fxbot import strongtrend as st


def _daily(drift, n=400, seed=0, noise=0.004):
    idx = pd.bdate_range("2024-01-01", periods=n)
    r = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(np.full(n, drift) + r.normal(0, noise, n)))
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) * 1.003, np.minimum(o, c) * 0.997
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1.0}, index=idx)


class VoteTests(unittest.TestCase):
    def test_strong_up_and_down(self):
        self.assertEqual(st.votes(_daily(0.006)).iloc[-1]["state"], 1)
        self.assertEqual(st.votes(_daily(-0.006)).iloc[-1]["state"], -1)

    def test_flat_market_is_not_strong(self):
        v = st.votes(_daily(0.0, noise=0.01, seed=3))
        self.assertLess((v["state"] != 0).mean(), 0.5)

    def test_two_of_three_rule(self):
        v = st.votes(_daily(0.006))
        row = v.iloc[-1]
        self.assertGreaterEqual(int((row[["adx", "ema", "aroon"]] == 1).sum()), 2)

    def test_shuffle_keeps_share(self):
        s = st.votes(_daily(0.002, noise=0.01))["state"]
        sh = st.shuffled(s, 1)
        self.assertEqual(sorted(s.tolist()), sorted(sh.tolist()))
        self.assertEqual(len(sh), len(s))


class GateTests(unittest.TestCase):
    def test_intraday_bars_read_yesterdays_state(self):
        idx = pd.date_range("2024-01-01", periods=24 * 420, freq="h", tz="UTC")
        c = 1.0 * np.exp(np.cumsum(np.full(len(idx), 0.0003)))
        m = pd.DataFrame({"open": c, "high": c * 1.0005, "low": c * 0.9995, "close": c}, index=idx)
        d = st.daily_of(m)
        s = st.votes(d)["state"]
        on = st.state_on_bars(m, s)
        self.assertEqual(len(on), len(m))
        self.assertEqual(on[-1], int(s.iloc[-2]))
        self.assertEqual(on[-1], 1)

    def test_wrapper_blocks_against_trend(self):
        class Always:
            max_bars = 5

            def frame(self, m):
                return pd.DataFrame({"long": True, "short": True, "exit_long": False, "exit_short": False, "stop": 0.01, "rank": 0.0}, index=m.index)
        m = _daily(0.006)
        f = st.StrongTrendFx(Always()).frame(m)
        self.assertTrue(f["long"].iloc[-1])
        self.assertFalse(f["short"].iloc[-50:].any())

    def test_verdict(self):
        self.assertEqual(st.verdict({"trades": 6, "net": 2.0}, {"net": 1.0}, 19, 20), "PASS")
        self.assertEqual(st.verdict({"trades": 4, "net": 2.0}, {"net": 1.0}, 19, 20), "fail")
        self.assertEqual(st.verdict({"trades": 6, "net": 2.0}, {"net": 3.0}, 19, 20), "fail")


class AgeScaledTests(unittest.TestCase):
    def test_long_trend_accepts_mild_readings(self):
        v = st.votes(_daily(0.0015, n=500, noise=0.006, seed=5))
        self.assertGreaterEqual(int(v["age"].iloc[-1]), st.LONG_AGE)
        self.assertGreaterEqual((v["state_age"].iloc[-100:] == 1).mean(), (v["state"].iloc[-100:] == 1).mean())
        self.assertFalse((v["state_age"].iloc[-100:] == -1).any())

    def test_short_trend_uses_strict_rule(self):
        v = st.votes(_daily(0.006))
        young = v[(v["age"].abs() < st.LONG_AGE)]
        self.assertTrue((young["state_age"] == young["state"]).all())


if __name__ == "__main__":
    unittest.main()
