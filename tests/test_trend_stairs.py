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


class NoStopResearchTests(unittest.TestCase):
    def test_no_stop_rides_through_the_stop_price(self):
        import numpy as np
        from alpacabot.engine import ladder_walk
        sell = lambda px: px                                     # noqa: E731
        o = np.array([100.0, 99.0, 98.5, 101.0])
        h = np.array([100.1, 99.2, 99.0, 101.5])
        l = np.array([99.5, 98.5, 98.0, 100.5])
        c = np.array([99.6, 98.8, 98.9, 101.2])
        lad = {"tp_fracs": [], "tp_depth_atr": []}
        j, pnl, why = ladder_walk(o, h, l, c, 0, 4, 100.0, 99.0, np.inf, 1.0, 1.0, lad, sell)
        self.assertEqual(why, "stop")
        j, pnl, why = ladder_walk(o, h, l, c, 0, 4, 100.0, 99.0, np.inf, 1.0, 1.0, {**lad, "no_stop": "all"}, sell)
        self.assertEqual((why, round(pnl, 2)), ("eod", 1.2))      # held to the close


if __name__ == "__main__":
    unittest.main()


class FixedRTests(unittest.TestCase):
    def test_fixed_r_ladder_walk_semantics(self):
        import numpy as np
        from alpacabot.engine import ladder_walk
        sell = lambda px: px                                     # noqa: E731
        lad = {"tp_fracs": [1.0], "tp_depth_atr": [0.0], "lock": []}
        # entry 100, original risk 2: cut 0.5R = stop 99, out at 1.5R = 103
        o = np.array([100.0, 100.2, 100.5]); h = np.array([100.3, 100.6, 103.2]); l = np.array([99.6, 99.4, 100.4]); c = np.array([100.1, 100.4, 103.0])
        j, pnl, why = ladder_walk(o, h, l, c, 0, 3, 100.0, 99.0, 103.0, 1.0, 1.0, lad, sell)
        self.assertEqual((j, why, round(pnl, 6)), (2, "zone100", 3.0))               # +1.5R of the original risk 2 = 3.0, all out
        l2 = np.array([99.6, 98.9, 100.4])
        j, pnl, why = ladder_walk(o, h, l2, c, 0, 3, 100.0, 99.0, 103.0, 1.0, 1.0, lad, sell)
        self.assertEqual((j, why, round(pnl, 6)), (1, "stop", -1.0))                 # stopped at 0.5R = 1.0 against

    def test_funnel_stop_frac_scales_the_stop_only(self):
        sys_path = None
        import pandas as pd  # noqa: F401
        from alpacabot.funnel import frame
        import sys
        sys.path.insert(0, "tests")
        from test_funnel import _daily
        d = _daily()
        a, b = frame(d), frame(d, {"stop_frac": 0.5})
        i = a.index[a["long_entry"]][0]
        dist = a.loc[i, "close"] - a.loc[i, "long_stop"]
        self.assertAlmostEqual(float(b.loc[i, "close"] - b.loc[i, "long_stop"]), 0.5 * float(dist), places=9)
        self.assertAlmostEqual(float(b.loc[i, "long_tp"]), float(a.loc[i, "long_tp"]), places=9)      # take-profit unchanged


if __name__ == "__main__":
    unittest.main()


class OversoldGateTests(unittest.TestCase):
    def _day(self, **over):
        n = 30
        idx = pd.date_range("2026-10-19 09:30", periods=n, freq="5min", tz="America/New_York")
        base = {"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 1000.0, "f_rsi14": 50.0, "f_stk": 50.0, "f_willr": -50.0,
                "f_bb_lo": 99.0, "f_bb_mid": 100.0, "f_vwap": 100.0, "f_atr": 0.5}
        base.update(over)
        return pd.DataFrame({k: [v] * n for k, v in base.items()}, index=idx)

    def test_votes(self):
        from alpacabot.confirm import oversold_votes
        self.assertEqual(oversold_votes(self._day(), 10)[0], 0)
        d = self._day(f_rsi14=25.0, f_stk=10.0, f_willr=-90.0, close=98.9)                  # RSI, stochastic, Williams; %B: (98.9-99)/(101-99)<0 -> Bollinger; VWAP: -1.1/0.5 -> VWAP
        k, names = oversold_votes(d, 10)
        self.assertEqual(k, 5, names)
        self.assertEqual(oversold_votes(self._day(f_rsi14=25.0, f_stk=10.0), 10)[0], 2)
        self.assertEqual(oversold_votes(self._day(f_rsi14=float("nan")), 10)[0], 0)          # missing data = not oversold

    def test_gate_filters_longs_only(self):
        from alpacabot.brain import OversoldGate
        from alpacabot.strategies import Signal

        class Base:
            def signals(self, day):
                return [Signal(i=10, side=1, stop_dist=0.5, tag="x"), Signal(i=10, side=-1, stop_dist=0.5, tag="S:x")]
        calm, deep = self._day(), self._day(f_rsi14=25.0, f_stk=10.0, f_willr=-90.0)
        self.assertEqual([s.side for s in OversoldGate(Base(), 3).signals(calm)], [-1])        # the buy is blocked, the short passes
        self.assertEqual(sorted(s.side for s in OversoldGate(Base(), 3).signals(deep)), [-1, 1])
        self.assertEqual([s.side for s in OversoldGate(Base(), 4).signals(deep)], [-1])        # 3 of 5 is not enough for need 4

    def test_live_config_switch(self):
        import json
        from pathlib import Path
        g = json.loads((Path(__file__).parent.parent / "alpacabot" / "zones.json").read_text())["intraday"]["oversold_gate"]
        self.assertEqual((g["live"], g["need"]), (True, 3))


if __name__ == "__main__":
    unittest.main()


class ScalperTests(unittest.TestCase):
    def _day(self):
        import numpy as np
        import pandas as pd
        n = 30
        idx = pd.date_range("2026-10-20 09:30", periods=n, freq="5min", tz="America/New_York")
        c = np.full(n, 100.0)
        d = pd.DataFrame({"open": c, "high": c + 0.2, "low": c - 0.2, "close": c, "volume": 1000.0, "f_atr": 0.4, "f_m15_dir": 1, "f_h1_dir": 1,
                          "f_rsi14": 50.0, "f_stk": 50.0, "f_willr": -40.0, "f_bb_lo": 99.0, "f_bb_mid": 100.0, "f_vwap": 100.0}, index=idx)
        i = 24                                                        # 11:30: an up bar on volume (20 prior bars), 5 of 5 oversold
        d.loc[d.index[i], ["open", "close", "volume", "f_rsi14", "f_stk", "f_willr", "f_vwap"]] = [99.5, 99.7, 3000.0, 25.0, 10.0, -90.0, 101.0]
        d.loc[d.index[i], "f_bb_lo"] = 99.8
        return d, i

    def test_signal_and_only_tags(self):
        from alpacabot.brain import OnlyTags, ScalperSignals

        class Stub:
            def signals(self, day):
                from alpacabot.strategies import Signal
                return [Signal(i=len(day) - 1, side=1, stop_dist=1.0, tag="williams_r")]
        d, i = self._day()
        sc = ScalperSignals(Stub(), need=3).box_signals(d)
        self.assertEqual([(s.i, s.side, s.tag, s.max_bars) for s in sc], [(i, 1, "scalper", 6)])
        self.assertAlmostEqual(sc[0].stop_dist, 0.5 * 0.4)
        self.assertAlmostEqual(sc[0].target_px, 99.7 + 1.25 * 0.4)
        out = OnlyTags(ScalperSignals(Stub(), need=3), {"scalper"}).signals(d)
        self.assertTrue(out and all(s.tag == "scalper" for s in out))
        d2 = d.copy()
        d2.loc[d2.index[i], "volume"] = 900.0                         # no volume: no setup
        self.assertEqual(ScalperSignals(Stub()).box_signals(d2), [])
        d3 = d.copy()
        d3["f_h1_dir"] = -1                                           # the 1-hour trend is down: no setup
        self.assertEqual(ScalperSignals(Stub()).box_signals(d3), [])


class VelezTests(unittest.TestCase):
    def _day(self):
        import numpy as np
        import pandas as pd
        n = 60
        idx = pd.date_range("2026-10-20 09:30", periods=n, freq="5min", tz="America/New_York")
        c = 100 + 0.05 * np.arange(n)                         # a steady uptrend: the simple 20 average rises and sits above the 200
        d = pd.DataFrame({"open": c - 0.02, "high": c + 0.1, "low": c - 0.1, "close": c, "volume": 1000.0, "f_atr": 0.3, "f_sma200": 95.0}, index=idx)
        d["f_sma20"] = d["close"].rolling(20, min_periods=1).mean()
        d["x_velez"] = d["close"] < d["f_sma20"]
        return d

    class Stub:
        def signals(self, day):
            return []

    def test_vbs_three_lower_closes_then_a_bar_through_the_high(self):
        from alpacabot.brain import VelezSignals
        d = self._day()
        i = 40
        for k, px in zip((i - 4, i - 3, i - 2, i - 1), (103.1, 103.0, 102.9, 102.8)):   # three lower closes in a row
            d.loc[d.index[k], ["open", "high", "low", "close"]] = [px + 0.05, px + 0.1, px - 0.1, px]
        d.loc[d.index[i], ["open", "high", "low", "close"]] = [102.8, 103.2, 102.7, 103.1]   # an up bar through the prior high
        sig = VelezSignals(self.Stub(), setups=("velez_vbs",)).box_signals(d)
        self.assertTrue(any(s.i == i and s.tag == "velez_vbs" and s.side == 1 for s in sig))
        s = next(s for s in sig if s.i == i)
        self.assertGreaterEqual(s.stop_dist, 0.5 * 0.3)
        self.assertLessEqual(s.stop_dist, 2.0 * 0.3)
        self.assertAlmostEqual(s.target_px, 103.1 + 1.5 * s.stop_dist)
        self.assertEqual((s.exit_col, s.max_bars), ("x_velez", 18))

    def test_no_signals_in_a_downtrend(self):
        from alpacabot.brain import VelezSignals
        d = self._day()
        d["f_sma200"] = 120.0                                  # the 20 is under the 200: no Velez environment
        self.assertEqual(VelezSignals(self.Stub()).box_signals(d), [])

    def test_halt_and_rbi(self):
        from alpacabot.brain import VelezSignals
        d = self._day()
        i = 40
        ma = float(d["f_sma20"].iloc[i])
        d.loc[d.index[i - 1], ["open", "high", "low", "close"]] = [ma + 0.2, ma + 0.25, ma + 0.1, ma + 0.12]    # a red bar near the 20
        d.loc[d.index[i], ["open", "high", "low", "close"]] = [ma + 0.1, ma + 0.4, ma + 0.05, ma + 0.35]       # an up bar tagging it and closing above
        tags = {s.tag for s in VelezSignals(self.Stub()).box_signals(d) if s.i == i}
        self.assertTrue({"velez_halt", "velez_rbi"} & tags)


class VelezResearchTests(unittest.TestCase):
    def test_nrb_trap_orb_masks_fire_and_filters_run(self):
        import numpy as np
        import pandas as pd
        from alpacabot.brain import VELEZ_RESEARCH, VELEZ_TOD, VelezSignals
        n = 60
        idx = pd.date_range("2026-10-20 09:30", periods=n, freq="5min", tz="America/New_York")
        c = 100 + 0.05 * np.arange(n)
        d = pd.DataFrame({"open": c - 0.02, "high": c + 0.1, "low": c - 0.1, "close": c, "volume": 1000.0, "f_atr": 0.3, "f_sma200": 95.0}, index=idx)
        d["f_sma20"] = d["close"].rolling(20, min_periods=1).mean()
        d["x_velez"] = d["close"] < d["f_sma20"]
        i = 30
        d.loc[d.index[i - 1], ["open", "high", "low", "close"]] = [c[i - 1], c[i - 1] + 0.03, c[i - 1] - 0.03, c[i - 1]]     # a narrow range bar
        d.loc[d.index[i], ["open", "high", "low", "close"]] = [c[i - 1], c[i - 1] + 0.2, c[i - 1] - 0.02, c[i - 1] + 0.15]   # closes up through its high

        class Stub:
            def signals(self, day):
                return []
        sig = VelezSignals(Stub(), setups=("velez_nrb",)).box_signals(d)
        self.assertTrue(any(s.i == i and s.tag == "velez_nrb" for s in sig))
        # every research setup and both filters run without error on a plain frame
        VelezSignals(Stub(), setups=VELEZ_RESEARCH, look_left=True).box_signals(d)
        VelezSignals(Stub(), tod=VELEZ_TOD).box_signals(d)


class BrainConfigTests(unittest.TestCase):
    def test_default_brain_uses_the_committed_expert_lists(self):
        import tempfile
        from alpacabot.paper import default_brain
        with tempfile.TemporaryDirectory() as d:
            b = default_brain(d)
        self.assertNotIn("dual_regime", b.experts)
        self.assertIn("dual_regime", b.short_experts)
        self.assertNotIn("williams_r", b.short_experts)
        self.assertNotIn("velez_180", b.experts + b.short_experts)


class VelezResearch2Tests(unittest.TestCase):
    def test_bottoming_tail_and_all_masks_run(self):
        import numpy as np
        import pandas as pd
        from alpacabot.brain import VELEZ_RESEARCH2, VelezSignals
        n = 60
        idx = pd.date_range("2026-10-20 09:30", periods=n, freq="5min", tz="America/New_York")
        c = 100 + 0.05 * np.arange(n)
        d = pd.DataFrame({"open": c - 0.02, "high": c + 0.1, "low": c - 0.1, "close": c, "volume": 1000.0, "f_atr": 0.3, "f_sma200": 95.0}, index=idx)
        d["f_sma20"] = d["close"].rolling(20, min_periods=1).mean()
        d["x_velez"] = d["close"] < d["f_sma20"]
        i = 40
        px = float(d["f_sma20"].iloc[i])
        d.loc[d.index[i], ["open", "high", "low", "close"]] = [px + 0.3, px + 0.35, px - 0.4, px + 0.33]   # a long lower wick, closing near its high, at the 20

        class Stub:
            def signals(self, day):
                return []
        sig = VelezSignals(Stub(), setups=VELEZ_RESEARCH2).box_signals(d)
        self.assertTrue(any(s.i == i and s.tag == "velez_bt" for s in sig))


class VelezHybridTests(unittest.TestCase):
    def test_trend_filter_and_one_bar_stop(self):
        import numpy as np
        import pandas as pd
        from alpacabot.brain import VelezHybrid
        from alpacabot.strategies import Signal
        n = 40
        idx = pd.date_range("2026-10-20 09:30", periods=n, freq="5min", tz="America/New_York")
        c = 100 + 0.05 * np.arange(n)
        d = pd.DataFrame({"open": c, "high": c + 0.1, "low": c - 0.2, "close": c, "f_atr": 0.3, "f_sma200": 95.0}, index=idx)
        d["f_sma20"] = d["close"].rolling(20, min_periods=1).mean()

        class Stub:
            def signals(self, day):
                return [Signal(i=30, side=1, stop_dist=1.0, tag="x"), Signal(i=31, side=-1, stop_dist=1.0, tag="S:x")]
        out = VelezHybrid(Stub(), env=True, one_bar=True).signals(d)
        self.assertEqual([s.side for s in out], [1])                 # the short is against the rising 20 over the 200
        self.assertAlmostEqual(out[0].stop_dist, 0.2 + 0.1 * 0.3)    # under the signal bar's low + 0.1 ATR


class VelezHybridTuneTests(unittest.TestCase):
    def test_stop_and_target_tune_only_on_listed_experts(self):
        import numpy as np
        import pandas as pd
        from alpacabot.brain import VelezHybrid
        from alpacabot.strategies import Signal
        n = 40
        idx = pd.date_range("2026-10-20 09:30", periods=n, freq="5min", tz="America/New_York")
        c = 100 + 0.05 * np.arange(n)
        d = pd.DataFrame({"open": c, "high": c + 0.1, "low": c - 0.2, "close": c, "f_atr": 0.3, "f_sma200": 95.0}, index=idx)
        d["f_sma20"] = d["close"].rolling(20, min_periods=1).mean()

        class Stub:
            def signals(self, day):
                return [Signal(i=30, side=1, stop_dist=0.4, tag="connors_rsi2"), Signal(i=31, side=1, stop_dist=0.4, tag="range_box")]
        out = VelezHybrid(Stub(), env=True, stop_r=1.5, target_r=0.5, tags={"connors_rsi2"}).signals(d)
        a, b = out
        self.assertAlmostEqual(a.stop_dist, 0.6)
        self.assertAlmostEqual(a.target_px, float(c[30]) + 0.2)
        self.assertEqual((b.stop_dist, b.target_px), (0.4, 0.0))      # not an expert in the list: untouched


class VelezShortMirrorTests(unittest.TestCase):
    def test_vss_short_on_the_mirrored_chart(self):
        import numpy as np
        import pandas as pd
        from alpacabot.brain import VelezSignals
        n = 60
        idx = pd.date_range("2026-10-20 09:30", periods=n, freq="5min", tz="America/New_York")
        c = 100 - 0.05 * np.arange(n)                         # a steady downtrend: the 20 SMA falls and sits under the 200
        d = pd.DataFrame({"open": c + 0.02, "high": c + 0.1, "low": c - 0.1, "close": c, "volume": 1000.0, "f_atr": 0.3, "f_sma200": 105.0}, index=idx)
        d["f_sma20"] = d["close"].rolling(20, min_periods=1).mean()
        d["x_velez"], d["x_velez_s"] = d["close"] < d["f_sma20"], d["close"] > d["f_sma20"]
        i = 40
        for k, px in zip((i - 4, i - 3, i - 2, i - 1), (97.9, 98.0, 98.1, 98.2)):   # three higher closes (a 3-bar rally)
            d.loc[d.index[k], ["open", "high", "low", "close"]] = [px - 0.05, px + 0.1, px - 0.1, px]
        d.loc[d.index[i], ["open", "high", "low", "close"]] = [98.2, 98.3, 97.8, 97.9]   # a down bar through the prior low

        class Stub:
            def signals(self, day):
                return []
        sig = VelezSignals(Stub(), setups=("velez_vbs",), sides=(-1,)).box_signals(d)
        s = next(s for s in sig if s.i == i)
        self.assertEqual((s.side, s.tag, s.exit_col), (-1, "S:velez_vbs", "x_velez_s"))
        self.assertAlmostEqual(s.target_px, 97.9 - 1.5 * s.stop_dist)
