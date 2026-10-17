import unittest

import numpy as np
import pandas as pd


def _uptrend(days=8, seed=3):
    rng = np.random.default_rng(seed)
    idx = []
    for d in pd.bdate_range("2026-08-03", periods=days):
        idx += list(pd.date_range(d + pd.Timedelta(hours=9, minutes=30), periods=78, freq="5min", tz="America/New_York"))
    idx = pd.DatetimeIndex(idx)
    t = np.arange(len(idx))
    c = 100 + 0.03 * t + 0.8 * np.sin(t / 6.0) + rng.normal(0, 0.05, len(idx))       # rising with regular pullbacks
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.05, "low": np.minimum(o, c) - 0.05, "close": c,
                         "volume": 1000.0 + rng.integers(0, 500, len(idx))}, index=idx)


class TrendStairsTests(unittest.TestCase):
    def test_features_include_the_stochastics_and_the_sell_vote(self):
        from alpacabot.experts import features_both
        f = features_both(_uptrend())
        for col in ("f_stk", "f_std", "f_h1_stoch", "f_m15_stoch", "x_stairs", "m_x_stairs"):
            self.assertIn(col, f)

    def test_signals_only_long_in_an_uptrend_with_a_tp1_above_and_the_sell_vote_exit(self):
        from alpacabot.brain import TrendStairsSignals
        from alpacabot.experts import features_both

        class Empty:
            def signals(self, day):
                return []
        df = _uptrend()
        df = df.join(features_both(df))
        day = df[df.index.date == df.index[-1].date()]
        sigs = TrendStairsSignals(Empty(), need=2, h1_stoch_max=100.0).signals(day)
        self.assertTrue(sigs)
        for s in sigs:
            self.assertEqual(s.side, 1)
            self.assertEqual(s.tag, "trend_stairs")
            self.assertEqual(s.exit_col, "x_stairs")
            c, a = float(day["close"].iloc[s.i]), float(day["f_atr"].iloc[s.i])
            self.assertAlmostEqual(s.target_px, c + 1.3 * a, places=6)
            self.assertTrue(0.8 * a - 1e-9 <= s.stop_dist <= 2.0 * a + 1e-9)

    def test_stair_step_starts_at_half_r(self):
        from alpacabot.engine import ladder_walk
        lad = {"tp_fracs": [], "tp_depth_atr": [], "trail_from_entry_r": 0.5, "trail_start_r": 0.5}
        sell = lambda px: px                                     # noqa: E731
        # entry 100, risk 1: price reaches 100.4 (below +0.5R: no trail), then 100.8 (stop -> 100.3), then drops
        o = np.array([100.0, 100.3, 100.7, 100.5])
        h = np.array([100.1, 100.4, 100.8, 100.5])
        l = np.array([99.9, 100.0, 100.6, 100.2])
        c = np.array([100.0, 100.3, 100.7, 100.25])
        j, pnl, why = ladder_walk(o, h, l, c, 0, 4, 100.0, 99.0, np.inf, 1.0, 1.0, lad, sell)
        self.assertEqual(j, 3)
        self.assertAlmostEqual(pnl, 0.3, places=6)               # sold at the stair (best 100.8 - 0.5R)


if __name__ == "__main__":
    unittest.main()


class TrendStairsSixVoteTests(unittest.TestCase):
    def test_sr_rsi_mode_runs_and_respects_levels(self):
        from alpacabot.brain import TrendStairsSignals
        from alpacabot.experts import features_both

        class Empty:
            def signals(self, day):
                return []
        df = _uptrend()
        df = df.join(features_both(df))
        day = df[df.index.date == df.index[-1].date()].copy()
        day.attrs["levels"] = [{"price": float(day["low"].min()), "touches": 3, "strong": True}]
        sigs = TrendStairsSignals(Empty(), need6=2, h1_stoch_max=100.0, sr_rsi=True).signals(day)
        self.assertTrue(all(s.tag == "trend_stairs" for s in sigs))
        day.attrs["levels"] = []
        none = TrendStairsSignals(Empty(), need6=6, h1_stoch_max=100.0, sr_rsi=True).signals(day)
        self.assertEqual(none, [])                                  # 6 of 6 is impossible without an S/R level


if __name__ == "__main__":
    unittest.main()


class TrendStairsSidesConfirmTests(unittest.TestCase):
    def test_sides_filter_and_confirmation(self):
        from alpacabot.brain import TrendStairsSignals
        from alpacabot.experts import features_both

        class Empty:
            def signals(self, day):
                return []
        df = _uptrend()
        df = df.join(features_both(df))
        day = df[df.index.date == df.index[-1].date()]
        base = TrendStairsSignals(Empty(), need=2, h1_stoch_max=100.0).signals(day)
        self.assertTrue(base)
        self.assertEqual(TrendStairsSignals(Empty(), need=2, h1_stoch_max=100.0, sides=(-1,)).signals(day), [])   # uptrend: no shorts
        strict = TrendStairsSignals(Empty(), need=2, h1_stoch_max=100.0, confirm=1.0).signals(day)
        self.assertLessEqual(len(strict), len(base))


if __name__ == "__main__":
    unittest.main()


class TrendStairsListTests(unittest.TestCase):
    def test_select_needs_todays_list_and_tp_box_changes_the_exit(self):
        from alpacabot.brain import TrendStairsSignals
        from alpacabot.experts import features_both

        class Empty:
            def signals(self, day):
                return []
        df = _uptrend()
        df = df.join(features_both(df))
        day = df[df.index.date == df.index[-1].date()].copy()
        self.assertIn("x_stairs_tp", day)
        kw = dict(need=2, h1_stoch_max=100.0, select=True)
        self.assertEqual(TrendStairsSignals(Empty(), **kw).signals(day), [])              # no list attached: nothing
        day.attrs["ts_ok"] = (False, True)
        self.assertEqual(TrendStairsSignals(Empty(), **kw).signals(day), [])              # on the short list only, in an uptrend
        day.attrs["ts_ok"] = (True, False)
        sigs = TrendStairsSignals(Empty(), tp_zone=True, **kw).signals(day)
        self.assertTrue(sigs and all(s.exit_col == "x_stairs_tp" for s in sigs))


if __name__ == "__main__":
    unittest.main()
