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


class OversoldConfluenceTests(unittest.TestCase):
    def test_majority_oversold_long_and_overbought_short(self):
        import numpy as np
        import pandas as pd
        from fxbot.strategies import OversoldConfluence
        n = 80
        idx = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
        c = np.full(n, 1.10) + 0.0005 * np.sin(np.arange(n))
        c[-1] = 1.085                                          # a sharp drop: RSI, stochastic, lower band and 1.5 SD all say oversold
        m = pd.DataFrame({"open": c, "high": c + 0.0005, "low": c - 0.0005, "close": c, "volume": 1000.0}, index=idx)
        m.loc[m.index[-1], "volume"] = 3000.0
        f = OversoldConfluence().frame(m)
        self.assertTrue(bool(f["long"].iloc[-1]))
        self.assertFalse(bool(f["short"].iloc[-1]))
        m2 = m.copy()
        m2.loc[m2.index[-1], ["open", "high", "low", "close"]] = [1.115, 1.1155, 1.1145, 1.115]
        self.assertTrue(bool(OversoldConfluence().frame(m2)["short"].iloc[-1]))


class VelezFxTests(unittest.TestCase):
    def test_trend_filter_blocks_counter_trend_entries(self):
        import numpy as np
        import pandas as pd
        from fxbot.strategies import VelezFx

        class Always:
            max_bars = 10

            def frame(self, m):
                t = pd.Series(True, index=m.index)
                return pd.DataFrame({"long": t, "short": t, "exit_long": ~t, "exit_short": ~t, "stop": 0.001, "rank": 0.0}, index=m.index)
        n = 260
        idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
        c = 1.10 + 0.0002 * np.arange(n)                       # an uptrend: the 20 SMA rises over the 200
        m = pd.DataFrame({"open": c, "high": c + 0.0003, "low": c - 0.0003, "close": c}, index=idx)
        f = VelezFx(Always(), env=True).frame(m)
        self.assertTrue(bool(f["long"].iloc[-1]))
        self.assertFalse(bool(f["short"].iloc[-1]))


class BreakRetestTests(unittest.TestCase):
    def test_break_retest_rejection_then_trigger_long_and_cancel_on_close_back_inside(self):
        """Owner 2026-10-25: break above the box, retest of the old top, bullish rejection, entry when price trades above that bar's high;
        no trade when price closes back inside the box first."""
        import numpy as np
        import pandas as pd
        from fxbot.strategies import BreakRetest
        n = 200
        idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
        base = 1.005 + 0.004 * np.sin(np.arange(150) / 8)                      # the box: about 1.001 - 1.009
        seq = [1.0100, 1.0110, 1.0095, 1.0102, 1.0115, 1.0130]                    # break, back to the edge, rejection, trigger
        c = np.r_[base, seq, np.full(n - 150 - len(seq), 1.0135)]
        o = np.r_[c[0], c[:-1]]
        hi, lo = np.maximum(o, c) + 0.0002, np.minimum(o, c) - 0.0002
        lo[152] = 1.0088                                                         # the retest dips to the old top
        m = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c}, index=idx)
        f = BreakRetest(lookback=100, ranging=False).frame(m)
        longs = list(np.flatnonzero(f["long"].to_numpy()))
        self.assertTrue(longs and 152 < longs[0] <= 160, longs)
        self.assertAlmostEqual(float(f["target"].iloc[longs[0]]), 2 * float(f["stop"].iloc[longs[0]]))
        c2 = c.copy()
        c2[152:] = 1.0040                                                        # closes back inside the box: cancelled
        m2 = pd.DataFrame({"open": np.r_[c2[0], c2[:-1]], "high": np.maximum(np.r_[c2[0], c2[:-1]], c2) + 0.0002,
                           "low": np.minimum(np.r_[c2[0], c2[:-1]], c2) - 0.0002, "close": c2}, index=idx)
        self.assertFalse(BreakRetest(lookback=100, ranging=False).frame(m2)["long"].iloc[151:].any())


class PlaybookTests(unittest.TestCase):
    def test_playbook_runs_and_trades_are_bounded(self):
        """Owner 2026-10-25 Break & Retest Playbook (fxbot.playbook): runs on bid/ask bars, one position at a time, losses near -1R."""
        import numpy as np
        import pandas as pd
        from fxbot.playbook import PlaybookRules, simulate_playbook
        rng = np.random.default_rng(7)
        n = 6000
        idx = pd.date_range("2025-06-01", periods=n, freq="h", tz="UTC")
        c = 1.10 + np.cumsum(rng.normal(0, 0.0010, n)) + 0.01 * np.sin(np.arange(n) / 150)
        o = np.r_[c[0], c[:-1]]
        m = pd.DataFrame(index=idx)
        for p, off in (("b", -0.00004), ("a", 0.00004)):
            m[p + "o"], m[p + "c"] = o + off, c + off
            m[p + "h"] = np.maximum(o, c) + 0.0003 + off
            m[p + "l"] = np.minimum(o, c) - 0.0003 + off
        tr = simulate_playbook("EUR_USD", m, PlaybookRules(require_two_touches=False, min_rr=1.5), idx[1000].date(), idx[-1].date())
        for x, y in zip(tr, tr[1:]):
            self.assertGreater(y["entry"], x["exit"])                       # never two positions at once
        self.assertTrue(all(t["r"] > -1.6 for t in tr if t["why"] == "stop"))


class SessionPullbackTests(unittest.TestCase):
    def test_signals_only_inside_the_new_york_session(self):
        import numpy as np
        import pandas as pd
        from fxbot.strategies import SessionPullback
        idx = pd.date_range("2026-01-05", periods=400, freq="1h", tz="UTC")
        c = 1.1 + np.cumsum(np.random.default_rng(1).normal(0, 0.0005, 400))
        m = pd.DataFrame({"open": c, "high": c + 0.0004, "low": c - 0.0004, "close": c, "volume": 100}, index=idx)
        f = SessionPullback(both=True).frame(m)
        hrs = f.index[f["long"] | f["short"]].tz_convert("America/New_York").hour
        self.assertTrue(len(hrs) and all(8 <= h < 12 for h in hrs))
        free = SessionPullback(session=False).frame(m)
        self.assertGreater(int(free["long"].sum()), int(SessionPullback().frame(m)["long"].sum()))


class SessionGapFadeTests(unittest.TestCase):
    def test_signals_only_on_the_london_open_bar_and_fade_the_move(self):
        from fxbot.strategies import SessionGapFade
        idx = pd.date_range("2026-01-05", periods=24 * 40, freq="1h", tz="UTC")
        c = 1.1 + np.cumsum(np.random.default_rng(2).normal(0, 0.0012, len(idx)))
        m = pd.DataFrame({"open": c, "high": c + 0.0006, "low": c - 0.0006, "close": c, "volume": 100}, index=idx)
        f = SessionGapFade(k=0.2).frame(m)
        sig = f[f["long"] | f["short"]]
        self.assertTrue(len(sig) and set(sig.index.hour) == {6})
        self.assertFalse((f["long"] & f["short"]).any())
        up = m["close"] - m["open"].where(m.index.hour == 0).groupby(m.index.normalize()).transform("max")
        self.assertTrue((up[f["long"]] < 0).all() and (up[f["short"]] > 0).all())      # long after a down move, short after an up move
        self.assertTrue((sig["target"] == 0.5 * sig["stop"]).all())


class CarryTrendTests(unittest.TestCase):
    def test_runs_on_synthetic_rates_and_reports_the_three_variants(self):
        import datetime as dt
        from fxbot.research import carry_trend_study
        rng = np.random.default_rng(5)
        idx = pd.bdate_range("2012-01-01", "2016-12-31")
        pairs = ["EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY"]
        cl = {p: pd.Series(np.cumprod(1 + rng.normal(0, 0.005, len(idx))), index=idx) for p in pairs}
        mi = pd.date_range("2011-01-01", "2016-12-01", freq="MS")
        rates = pd.DataFrame({c: np.clip(1 + rng.normal(0, 0.3, len(mi)).cumsum() * 0.05, 0, 6) for c in ["USD", "EUR", "GBP", "JPY", "AUD"]}, index=mi)
        text = "\n".join(carry_trend_study(cl, rates, dt.date(2014, 12, 31), sma=50))
        for kind in ("carry+trend train", "carry+trend validate", "trend only train", "carry only validate"):
            self.assertIn(kind, text)
