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


def _h1(drift=0.0003, days=420, seed=0):
    idx = pd.date_range("2024-01-01", periods=24 * days, freq="h", tz="UTC")
    r = np.random.default_rng(seed)
    c = 1.1 * np.exp(np.cumsum(np.full(len(idx), drift) + r.normal(0, 0.0004, len(idx))))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.0003, "low": np.minimum(o, c) * 0.9997, "close": c}, index=idx)


class TwoFilterTests(unittest.TestCase):
    def test_today_state_needs_a_big_move(self):
        m = _h1(0.0003)
        td = st.today_state(m, st.daily_of(m), True)
        self.assertTrue((td == 1).any())
        self.assertFalse((td == -1).any())
        flat = _h1(0.0, seed=2)
        self.assertLess((st.today_state(flat, st.daily_of(flat), True) != 0).mean(), (td != 0).mean())

    def test_permute_days_keeps_values(self):
        m = _h1(0.0003, days=60)
        td = st.today_state(m, st.daily_of(m), True)
        key = st.day_keys(m.index, True)
        p = st.permute_days(td, key, 4)
        self.assertEqual(len(p), len(td))
        self.assertLess(abs(int((p != 0).sum()) - int((td != 0).sum())), 0.1 * int((td != 0).sum()))   # partial first / last days truncate

    def test_windows_stocks(self):
        idx = pd.date_range("2026-10-27 13:30", periods=78, freq="5min", tz="UTC")       # 09:30-15:55 New York
        ok, last = st.windows(idx, False)
        ny = idx.tz_convert("America/New_York")
        self.assertEqual(str(ny[ok][0].time()), "10:00:00")
        self.assertEqual(str(ny[ok][-1].time()), "14:55:00")
        self.assertEqual(str(ny[last][0].time()), "15:50:00")

    def test_ride_exits_same_day(self):
        idx = pd.date_range("2026-10-27 13:30", periods=78 * 2, freq="5min", tz="UTC")
        idx = idx[(idx.tz_convert("America/New_York").hour < 16)]
        px = np.linspace(100, 110, len(idx))
        df = pd.DataFrame({k: px for k in ("bo", "bh", "bl", "bc", "ao", "ah", "al", "ac")}, index=idx)
        td = np.zeros(len(idx), int)
        ny = idx.tz_convert("America/New_York")
        td[(ny.hour == 11) & (ny.minute == 0)] = 1
        tr = st.ride(df, td, np.full(len(idx), 2.0), False)
        self.assertEqual(len(tr), 1)
        self.assertGreater(tr[0]["r"], 0)

    def test_short_filter_exits_by_day_end(self):
        class Always:
            max_bars = 500

            def frame(self, m):
                return pd.DataFrame({"long": True, "short": True, "exit_long": False, "exit_short": False, "stop": 0.01, "rank": 0.0}, index=m.index)
        m = _h1(0.0003, days=300)
        f = st.FilterFx(Always(), "short", long_fn=lambda d: pd.Series(0, index=d.index)).frame(m)
        ny = m.index.tz_convert("America/New_York")
        self.assertTrue(f["exit_long"][ny.hour == 15].all())
        self.assertFalse(f["long"][ny.hour == 15].any())
        self.assertFalse(f["short"].any())


if __name__ == "__main__":
    unittest.main()
