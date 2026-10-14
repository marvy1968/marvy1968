import unittest

import numpy as np
import pandas as pd

from fxbot.engine import Book, _nights, metrics, portfolio, simulate
from fxbot.strategies import MaCrossBigBuild, LienFader, AsianBreakout, ConsensusMR, Donchian, EmaPullback, NR7Breakout, RangeBoxFade, RangeMR, RegimeSwitch, TsMom, efficiency_ratio


def bars(mid, spread=0.0002, freq="D", start="2016-01-04 22:00"):
    idx = pd.date_range(start, periods=len(mid), freq=freq, tz="UTC")
    m = np.asarray(mid, float)
    h = spread / 2
    o = np.r_[m[0], m[:-1]]
    hi, lo = np.maximum(o, m) * 1.0005, np.minimum(o, m) * 0.9995
    return pd.DataFrame({"bo": o - h, "bh": hi - h, "bl": lo - h, "bc": m - h, "ao": o + h, "ah": hi + h, "al": lo + h, "ac": m + h,
                         "volume": 100.0}, index=idx)


class Always:
    """Long on bar 0's close only, exit signal on bar 3."""
    max_bars = 10 ** 6

    def __init__(self, side=1, stop=0.05):
        self.side, self.stop = side, stop

    def frame(self, m):
        n = len(m)
        e = pd.Series([i == 0 for i in range(n)], index=m.index)
        x = pd.Series([i == 3 for i in range(n)], index=m.index)
        none = pd.Series(False, index=m.index)
        return pd.DataFrame({"long": e if self.side == 1 else none, "short": e if self.side == -1 else none, "exit_long": x, "exit_short": x,
                             "stop": self.stop, "rank": 0.0}, index=m.index)


class EngineTests(unittest.TestCase):
    def test_long_buys_ask_next_open_and_sells_bid_flat_market_loses_the_spread(self):
        d = bars([1.1] * 8)
        t = simulate("EUR_USD", d, Always(1))[0]
        self.assertAlmostEqual(t.entry_px, d["ao"].iloc[1])
        self.assertAlmostEqual(t.exit_px, d["bo"].iloc[4])                    # exit decided at bar 3's close, filled at bar 4's open
        self.assertAlmostEqual(t.exit_px - t.entry_px, -0.0002)
        s = simulate("EUR_USD", d, Always(-1))[0]
        self.assertAlmostEqual(s.entry_px, d["bo"].iloc[1])
        self.assertAlmostEqual(s.exit_px, d["ao"].iloc[4])

    def test_stop_is_checked_first_and_a_gap_fills_at_the_open(self):
        d = bars([1.1, 1.1, 1.1, 1.0, 1.0, 1.0])                               # bar 3 opens far below the stop
        d.iloc[3, d.columns.get_loc("bo")] = 1.0
        d.iloc[3, d.columns.get_loc("ao")] = 1.0002
        t = simulate("EUR_USD", d, Always(1, stop=0.02))[0]
        self.assertEqual(t.reason, "stop")
        self.assertAlmostEqual(t.exit_px, 1.0)                                # the gap open, not the 1.08 stop

    def test_rollover_nights_and_wednesday_triple(self):
        a = pd.Timestamp("2026-10-12 18:00", tz="America/New_York").tz_convert("UTC")     # Monday after 17:00
        self.assertEqual(_nights(a, a + pd.Timedelta(hours=24)), 1)                      # past Tuesday 17:00
        self.assertEqual(_nights(a, a + pd.Timedelta(hours=12)), 0)                      # Tuesday 06:00: no rollover yet
        self.assertEqual(_nights(a, a + pd.Timedelta(days=2)), 4)                        # Tue 1 + Wed 3
        self.assertEqual(_nights(a, a + pd.Timedelta(hours=1)), 0)

    def test_usd_pnl_conversion_and_risk_sizing(self):
        d = bars(np.linspace(150, 151.5, 8), spread=0.02)
        t = simulate("USD_JPY", d, Always(1, stop=0.5))[0]
        fills, _ = portfolio([t], Book(equity=100_000, risk_pct=0.005, leverage=50, max_positions=1))
        units = fills["units"].iloc[0]
        self.assertAlmostEqual(units * 0.5 / t.entry_px, 500, delta=2)        # 0.5 JPY stop on that many units = $500 = 0.5%
        exp = units * (t.exit_px - t.entry_px) / t.exit_px - fills["financing"].iloc[0]
        self.assertAlmostEqual(fills["pnl"].iloc[0], exp, places=6)

    def test_placebo_keeps_entry_counts(self):
        rng = np.random.default_rng(3)
        d = bars(1.1 * np.exp(np.cumsum(rng.normal(0, 0.006, 600))))
        real = Donchian(20, 10, 2.0).frame(pd.DataFrame({k: (d["b" + k[0]] + d["a" + k[0]]) / 2 for k in ("open", "high", "low", "close")}))
        p = simulate("EUR_USD", d, Donchian(20, 10, 2.0), placebo_seed=1)
        self.assertGreater(len(p), 0)
        self.assertLessEqual(len(p), int(real["long"].sum() + real["short"].sum()))


class TargetTests(unittest.TestCase):
    def test_target_fills_at_its_level_after_the_stop_check(self):
        class T(Always):
            def frame(self, m):
                f = super().frame(m)
                f["exit_long"] = False
                f["target"] = 0.01
                return f
        d = bars([1.10, 1.10, 1.105, 1.12, 1.12, 1.12])
        t = simulate("EUR_USD", d, T(1, stop=0.05))[0]
        self.assertEqual(t.reason, "target")
        self.assertAlmostEqual(t.exit_px, t.entry_px + 0.01)


class LienFaderTests(unittest.TestCase):
    def test_fades_a_false_break_of_yesterdays_low(self):
        idx = pd.date_range("2016-01-04 22:00", periods=24 * 40, freq="h", tz="UTC")
        c = pd.Series(1.1000 + np.random.default_rng(1).normal(0, 0.0004, len(idx)), index=idx)   # noise: flat prices make ADX undefined
        ny = idx.tz_convert("America/New_York")
        day = (ny - pd.Timedelta(hours=17)).normalize()
        days = sorted(set(day))
        last = days[-1]
        prev = days[-2]
        c[day == prev] = np.linspace(1.0990, 1.1010, int((day == prev).sum()))      # yesterday: 1.0990..1.1010
        k = np.flatnonzero(day == last)
        c.iloc[k[:5]] = 1.0970                                                      # dips 20 pips under yesterday's low
        c.iloc[k[5:]] = 1.1030                                                      # then closes 20 pips over yesterday's high
        m = pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c + 0.0001, "low": c - 0.0001, "close": c})
        f = LienFader(adx_max=101).frame(m)
        hits = f.index[f["long"]]
        self.assertEqual(list(hits), [idx[k[5]]])
        self.assertAlmostEqual(f.loc[hits[0], "stop"], 0.0030)
        self.assertAlmostEqual(f.loc[hits[0], "target"], 0.0060)
        self.assertFalse(f["short"][day == last].any())


class StrategyTests(unittest.TestCase):
    def _m(self, c):
        c = pd.Series(c, index=pd.date_range("2016-01-04", periods=len(c), freq="D", tz="UTC"))
        return pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c * 1.001, "low": c * 0.999, "close": c})

    def test_efficiency_ratio_extremes(self):
        self.assertAlmostEqual(efficiency_ratio(pd.Series(np.arange(50.0)), 20).iloc[-1], 1.0)
        self.assertAlmostEqual(efficiency_ratio(pd.Series([1.0, 2.0] * 25), 20).iloc[-1], 0.0)

    def test_families_produce_both_sides_and_finite_stops(self):
        rng = np.random.default_rng(5)
        m = self._m(1.1 * np.exp(np.cumsum(rng.normal(0, 0.006, 900))))
        for s in (Donchian(), TsMom(63, 21), RangeMR(adx_max=30), RegimeSwitch(), ConsensusMR(), EmaPullback(100), NR7Breakout(), MaCrossBigBuild()):
            f = s.frame(m)
            self.assertGreater(int(f["long"].sum()), 0, s.name)
            self.assertGreater(int(f["short"].sum()), 0, s.name)
            self.assertTrue(np.isfinite(f["stop"].iloc[-1]))

    def test_asian_breakout_first_signal_after_0700_only(self):
        idx = pd.date_range("2016-01-04 00:00", periods=24 * 30, freq="h", tz="UTC")
        rng = np.random.default_rng(2)
        c = pd.Series(1.1 + rng.normal(0, 0.0004, len(idx)).cumsum() * 0.2, index=idx)
        last = idx.normalize()[-1]
        day = (idx.normalize() == last)
        base = c[day & (idx.hour < 7)].mean()
        c[day & (idx.hour < 7)] = base                                     # a tight Asian range on the last day
        c[day & (idx.hour >= 7)] = base + 0.01                             # then a London breakout up
        m = pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c + 0.0001, "low": c - 0.0001, "close": c})
        f = AsianBreakout(0.0, 16).frame(m)
        hits = f.index[f["long"] & (f.index.normalize() == last)]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].hour, 7)                                  # the 07:00 bar closes 08:00; the 06:00 bar is still Asia
        self.assertFalse(f["short"][f.index.normalize() == last].any())
        self.assertTrue(f.loc[hits[0], "stop"] > 0)

    def test_rangebox_never_fires_in_a_trend_and_entries_sit_at_the_box_edge(self):
        # the frozen gate (47 of 50 closes inside the bands) is very strict: even pure noise rarely passes it, so only properties are tested
        rng = np.random.default_rng(6)
        m = self._m(1.1 + rng.normal(0, 0.002, 1500))
        f = RangeBoxFade().frame(m)
        c, lo_box = m["close"], m["low"].rolling(40).min()
        w = m["high"].rolling(40).max() - lo_box
        self.assertTrue((c[f["long"]] <= (lo_box + 0.15 * w)[f["long"]] + 1e-12).all())
        trend = self._m(1.1 * np.exp(np.cumsum(np.full(800, 0.003))))     # a straight trend: never a box
        g = RangeBoxFade().frame(trend)
        self.assertEqual(int(g["long"].sum()) + int(g["short"].sum()), 0)

    def test_walk_forward_runs_end_to_end(self):
        from fxbot import research
        rng = np.random.default_rng(9)
        data = {i: bars(1.1 * np.exp(np.cumsum(rng.normal(0, 0.006, 2600))), start="2015-01-05 22:00") for i in ("EUR_USD", "GBP_USD")}
        res = research.walk_forward("fx_donchian_d", data, placebo_runs=2)
        self.assertIn("passes", res)
        self.assertEqual(len(res["grid"]), 8)


if __name__ == "__main__":
    unittest.main()
