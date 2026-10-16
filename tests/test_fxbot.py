import unittest

import numpy as np
import pandas as pd

from fxbot.engine import Book, _nights, metrics, portfolio, simulate
from fxbot.strategies import F0Reversion, MaCrossBigBuild, LienFader, AsianBreakout, ConsensusMR, Donchian, EmaPullback, NR7Breakout, RangeBoxFade, RangeMR, RegimeSwitch, TsMom, efficiency_ratio


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


class FirewallTests(unittest.TestCase):
    def test_third_trade_against_usd_is_refused(self):
        from fxbot.engine import Trade
        t0 = pd.Timestamp("2020-01-06 10:00", tz="UTC")
        mk = lambda inst, side, px, k: Trade(inst, side, t0 + pd.Timedelta(hours=k), t0 + pd.Timedelta(days=5), px, px, px * 0.01, "time", 10, 0)  # noqa: E731
        trades = [mk("EUR_USD", 1, 1.1, 0), mk("USD_JPY", -1, 150.0, 1), mk("GBP_USD", 1, 1.3, 2), mk("USD_CAD", 1, 1.35, 3)]
        fills, _ = portfolio(trades, Book(max_positions=4, max_per_currency=2))
        self.assertEqual(list(fills["instrument"]), ["EUR_USD", "USD_JPY", "USD_CAD"])   # GBP_USD would be a 3rd short-USD bet


class NoLookAheadTests(unittest.TestCase):
    def test_a_trade_is_sized_on_equity_without_unclosed_pnl(self):
        from fxbot.engine import Trade
        t0 = pd.Timestamp("2020-01-06 10:00", tz="UTC")
        big_win = Trade("EUR_USD", 1, t0, t0 + pd.Timedelta(days=10), 1.10, 1.20, 0.01, "signal", 50, 0)      # still open when the next one starts
        nxt = Trade("GBP_USD", 1, t0 + pd.Timedelta(days=1), t0 + pd.Timedelta(days=2), 1.30, 1.30, 0.01, "time", 5, 0)
        later = Trade("AUD_USD", 1, t0 + pd.Timedelta(days=20), t0 + pd.Timedelta(days=21), 0.70, 0.70, 0.01, "time", 5, 0)
        fills, _ = portfolio([big_win, nxt, later], Book(equity=100_000, risk_pct=0.005, max_positions=4, leverage=1000))
        u = dict(zip(fills["instrument"], fills["units"]))
        self.assertEqual(u["GBP_USD"], 50_000)                      # 0.5% of 100k / 0.01: the open winner is not counted yet
        self.assertGreater(u["AUD_USD"], 50_000)                    # after it closed, its profit is


class ProfitLockTests(unittest.TestCase):
    def test_breakeven_after_one_r_then_trail(self):
        from fxbot.strategies import Locked

        class Hold(Always):
            def frame(self, m):
                f = super().frame(m)
                f["exit_long"] = False
                return f
        # +1R (0.01) reached on bar 2, then price falls back through the entry: the locked trade exits at break-even, the plain one at -1R
        path = [1.10, 1.10, 1.112, 1.104, 1.095, 1.085, 1.085]
        d = bars(path, spread=0.0)
        plain = simulate("EUR_USD", d, Hold(1, stop=0.01))[0]
        locked = simulate("EUR_USD", d, Locked(Hold(1, stop=0.01), 1.0, 2.0, 1.0))[0]
        self.assertEqual(plain.reason, "stop")
        self.assertAlmostEqual(plain.exit_px, plain.entry_px - 0.01, places=6)
        self.assertEqual(locked.reason, "lock")
        self.assertAlmostEqual(locked.exit_px, locked.entry_px, places=6)

    def test_lock_never_uses_the_bar_that_set_the_new_high(self):
        from fxbot.strategies import Locked

        class Hold(Always):
            def frame(self, m):
                f = super().frame(m)
                f["exit_long"] = False
                return f
        # one wide bar makes +3R and dips below entry in the same bar: the stop cannot move inside that bar
        d = bars([1.10, 1.10, 1.10, 1.10], spread=0.0)
        d.iloc[2, d.columns.get_loc("bh")] = 1.14
        d.iloc[2, d.columns.get_loc("bl")] = 1.095
        t = simulate("EUR_USD", d, Locked(Hold(1, stop=0.01), 1.0, 2.0, 1.0))[0]
        self.assertNotEqual(t.reason, "lock")


class ZoneTests(unittest.TestCase):
    class Hold(Always):
        def frame(self, m):
            f = super().frame(m)
            f["exit_long"] = False
            return f

    def _zoned(self, d, **kw):
        from fxbot.strategies import Zoned
        return simulate("EUR_USD", d, Zoned(self.Hold(1, stop=0.01), lookback=3, **kw))

    def test_exhaustion_zone_takes_profit_at_the_prior_high(self):
        path = [1.10, 1.13, 1.10, 1.10, 1.115, 1.14, 1.14]                      # prior 3-bar high ~1.13 is >= 1R (0.01) above entry
        d = bars(path, spread=0.0)
        t = self._zoned(d, chase_atr=99)[0]
        self.assertEqual(t.reason, "target")
        self.assertLess(t.exit_px, 1.14)

    def test_range_profile_targets_the_average(self):
        from fxbot.strategies import Zoned
        class At3(self.Hold):
            def frame(self, m):
                f = super().frame(m)
                f["long"] = [i == 3 for i in range(len(m))]
                return f
        path = [1.12, 1.12, 1.12, 1.10, 1.105, 1.115, 1.12, 1.12]
        d = bars(path, spread=0.0)
        t = simulate("EUR_USD", d, Zoned(At3(1, stop=0.005), profile="range", lookback=3, chase_atr=99))[0]
        self.assertEqual(t.reason, "target")
        self.assertAlmostEqual(t.exit_px, float(np.mean(path[1:4])), places=6)   # the 3-bar average at the signal bar

    def test_entry_zone_skips_a_chased_fill(self):
        from fxbot.strategies import Zoned

        class Late(self.Hold):                                                     # signal on bar 20, once ATR exists
            def frame(self, m):
                f = super().frame(m)
                f["long"] = [i == 20 for i in range(len(m))]
                return f
        d = bars([1.10 + 0.001 * (i % 2) for i in range(30)], spread=0.0)
        d.iloc[21, d.columns.get_loc("ao")] = 1.20                               # the next open gaps far past the signal close
        self.assertEqual(simulate("EUR_USD", d, Zoned(Late(1, stop=0.01), lookback=3)), [])
        d.iloc[21, d.columns.get_loc("ao")] = 1.1005                             # a normal fill is taken
        self.assertEqual(len(simulate("EUR_USD", d, Zoned(Late(1, stop=0.01), lookback=3))), 1)

    def test_exhaustion_bar_exits_after_a_big_run(self):
        path = [1.10, 1.10, 1.10, 1.125, 1.13, 1.12, 1.12, 1.12]               # +2.5R then a bar closing under the last bar's low
        d = bars(path, spread=0.0)
        t = self._zoned(d, chase_atr=99, min_r=50, default_r=50)[0]           # targets out of reach: only the exhaustion bar can exit
        self.assertEqual(t.reason, "exhausted")
        self.assertGreater(t.exit_px, t.entry_px)


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
        for s in (Donchian(), TsMom(63, 21), RangeMR(adx_max=30), RegimeSwitch(), ConsensusMR(), EmaPullback(100), NR7Breakout(), MaCrossBigBuild(), F0Reversion(50)):
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

    def test_every_family_builds_and_simulates(self):
        from fxbot import research
        rng = np.random.default_rng(11)
        d = bars(1.1 * np.exp(np.cumsum(rng.normal(0, 0.003, 1500))), freq="h")
        for name, (gran, cls, grid) in research.FAMILIES.items():
            p = research._grid(grid)[0]
            trades = simulate("EUR_USD", d, cls(**p))
            self.assertIsInstance(trades, list, name)

    def test_direction_first_blocks_counter_trend_entries(self):
        from fxbot.strategies import DirectionZoneMap

        class Both:
            max_bars, name = 10, "both"
            def frame(self, m):
                n = len(m)
                t = pd.Series([i % 7 == 0 for i in range(n)], index=m.index)
                none = pd.Series(False, index=m.index)
                return pd.DataFrame({"long": t, "short": t, "exit_long": none, "exit_short": none, "stop": 0.002, "rank": 0.0}, index=m.index)
        idx = pd.date_range("2016-01-04 22:00", periods=24 * 140, freq="h", tz="UTC")
        c = pd.Series(1.10 * np.exp(np.linspace(0, 0.25, len(idx))), index=idx)        # a steady uptrend
        m = pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c * 1.0005, "low": c * 0.9995, "close": c})
        trend = DirectionZoneMap(Both(), style="trend").frame(m)
        late = trend.index > idx[24 * 90]
        self.assertGreater(int(trend["long"][late].sum()), 0)
        self.assertEqual(int(trend["short"][late].sum()), 0)                           # up bias: no shorts

    def test_walk_forward_runs_end_to_end(self):
        from fxbot import research
        rng = np.random.default_rng(9)
        data = {i: bars(1.1 * np.exp(np.cumsum(rng.normal(0, 0.006, 2600))), start="2015-01-05 22:00") for i in ("EUR_USD", "GBP_USD")}
        res = research.walk_forward("fx_donchian_d", data, placebo_runs=2)
        self.assertIn("passes", res)
        self.assertEqual(len(res["grid"]), 8)


if __name__ == "__main__":
    unittest.main()


class FxLadderSimTests(unittest.TestCase):
    def test_scaled_exits_on_bid_ask(self):
        import numpy as np
        import pandas as pd
        from alpacabot.research import LADDER_VARIANTS
        from fxbot.engine import simulate
        from fxbot.strategies import Laddered, _frame

        class One:
            max_bars, name = 500, "one long"

            def frame(self, m):
                L = pd.Series(False, index=m.index)
                L.iloc[30] = True
                none = pd.Series(False, index=m.index)
                f = _frame(m.index, L, none, none, none, pd.Series(0.0030, index=m.index))
                f["zone_px"] = np.where(L, float(m["close"].iloc[30]) + 0.0030, np.nan)
                return f
        n = 120
        idx = pd.date_range("2026-01-05", periods=n, freq="1h", tz="UTC")
        mid = np.r_[np.full(31, 1.1000), 1.1000 + np.arange(1, n - 30) * 0.0002]     # flat, then a steady climb
        df = pd.DataFrame({"bo": mid - 0.00005, "bh": mid + 0.0001, "bl": mid - 0.0002, "bc": mid - 0.00005,
                           "ao": mid + 0.00005, "ah": mid + 0.0002, "al": mid - 0.0001, "ac": mid + 0.00005, "volume": 1.0}, index=idx)
        t = simulate("EUR_USD", df, Laddered(One(), LADDER_VARIANTS["owner (cut 25/50, TP 25/50, trail 1 ATR)"]))
        self.assertEqual(len(t), 1)
        self.assertGreater(t[0].exit_px, t[0].entry_px + 0.0030 * 0.5)               # banked at the zone, the rest rode higher
        self.assertTrue(t[0].reason in ("eod", "lock", "time"))


class BoxGatedTests(unittest.TestCase):
    def _bars(self, n=700, seed=3):
        import numpy as np
        import pandas as pd
        rng = np.random.default_rng(seed)
        idx = pd.date_range("2022-01-03", periods=n, freq="h", tz="UTC")
        c = 1.10 + 0.004 * np.sin(np.arange(n) / 9.0) + rng.normal(0, 0.0004, n)           # a sideways pair: swings around one level
        return pd.DataFrame({"open": np.r_[c[0], c[:-1]], "high": c + 0.0005, "low": c - 0.0005, "close": c, "volume": 1.0}, index=idx)

    def test_gate_only_removes_signals_never_adds_and_keeps_exits(self):
        from fxbot.strategies import BoxGated, RangeMR
        m = self._bars()
        base = RangeMR(bb_k=1.0, adx_max=100.0)
        f0 = base.frame(m)
        for pos in ("valid", "half", "edge"):
            g = BoxGated(base, position=pos).frame(m)
            self.assertFalse((g["long"] & ~f0["long"]).any())
            self.assertFalse((g["short"] & ~f0["short"]).any())
            self.assertTrue((g["exit_long"] == f0["exit_long"]).all())
        valid = BoxGated(base, position="valid").frame(m)
        edge = BoxGated(base, position="edge").frame(m)
        self.assertLessEqual(int(edge["long"].sum()), int(valid["long"].sum()))              # a tighter gate keeps no more signals

    def test_families_register_a_plain_twin_for_every_gate(self):
        from fxbot.research import BOX_GATE_EXPERTS, FAMILIES
        for n in BOX_GATE_EXPERTS:
            for suffix in ("plain", "valid", "half", "edge"):
                self.assertIn(f"fx_boxgate_{n}_{suffix}", FAMILIES)


class RangeBoxBreakoutTests(unittest.TestCase):
    def _bars(self, drift=0.0, n=600):
        rng = np.random.default_rng(5)
        idx = pd.date_range("2022-01-03", periods=n, freq="h", tz="UTC")
        c = 1.10 + 0.0035 * np.sin(np.arange(n) / 8.0) + rng.normal(0, 0.0003, n)
        c[450:] += np.cumsum(np.full(n - 450, drift))                      # after bar 450 the pair leaves the box (when drift > 0)
        return pd.DataFrame({"open": np.r_[c[0], c[:-1]], "high": c + 0.0004, "low": c - 0.0004, "close": c, "volume": 1.0}, index=idx)

    def test_off_is_unchanged_and_only_takes_signals_outside_the_box(self):
        from fxbot.strategies import RangeBox200
        m = self._bars(drift=0.0006)
        plain, only, also = RangeBox200().frame(m), RangeBox200(breakout="only").frame(m), RangeBox200(breakout="also").frame(m)
        self.assertEqual(RangeBox200(breakout="off").frame(m)["long"].tolist(), plain["long"].tolist())
        self.assertTrue(only["long"].any())                                    # the breakout out of the box was bought
        top = m["high"].shift(1).rolling(100).max()
        self.assertTrue((m["close"][only["long"]] > top[only["long"]]).all())  # every breakout buy is a close beyond the top edge
        self.assertTrue((only["stop"][only["long"]] > 0).all())
        self.assertTrue((also["long"] | also["short"]).sum() >= (plain["long"] | plain["short"]).sum())

    def test_no_breakout_signal_without_adx_support(self):
        from fxbot.strategies import RangeBox200
        m = self._bars(drift=0.0006)
        none = RangeBox200(breakout="only", adx_min=1000.0).frame(m)
        self.assertFalse((none["long"] | none["short"]).any())


class HybridZoneTests(unittest.TestCase):
    def test_plan_picks_the_level_where_box_fib_and_swings_agree(self):
        from alpacabot.zonemap import hybrid_plan, hybrid_trade
        top, bot, a = 110.0, 90.0, 1.0                     # 0.618 retracement = 97.64, 0.786 = 94.28
        levels = [{"price": 97.5, "touches": 3, "strong": True}, {"price": 104.0, "touches": 2, "strong": True}]
        p = hybrid_plan(top, bot, a, levels, 1)
        self.assertAlmostEqual(p["entry"], 97.5)                     # the strong swing level next to the 0.618 level (97.64) scores highest
        self.assertGreaterEqual(p["score"], 3)
        self.assertIn(104.0, p["targets"])
        tr = hybrid_trade(p, bot, top, a, 1, 98.0)
        self.assertAlmostEqual(tr["stop_dist"], 98.0 - (97.5 - 0.5))   # Sell ALL = 0.5 ATR under the agreed level
        self.assertTrue(tr["tp1"] - 98.0 >= tr["stop_dist"])            # TP1 at least 1R away
        self.assertTrue(tr["tp2"] == 0.0 or tr["tp2"] > tr["tp1"])
        s = hybrid_plan(top, bot, a, levels, -1)
        self.assertGreater(s["entry"], 100.0)                           # the short's entry level is in the upper part of the box

    def test_hybrid_frame_signals_carry_two_take_profit_lines(self):
        from fxbot.strategies import HybridBox
        rng = np.random.default_rng(9)
        idx = pd.date_range("2022-01-03", periods=700, freq="h", tz="UTC")
        c = 1.10 + 0.004 * np.sin(np.arange(700) / 9.0) + rng.normal(0, 0.0004, 700)
        m = pd.DataFrame({"open": np.r_[c[0], c[:-1]], "high": c + 0.0006, "low": c - 0.0006, "close": c, "volume": 1.0}, index=idx)
        f = HybridBox().frame(m)
        sig = f["long"] | f["short"]
        self.assertTrue(sig.any())
        self.assertTrue((f["stop"][sig] > 0).all())
        lng = f["long"]
        self.assertTrue((f["zone_px"][lng] > m["close"][lng]).all())   # TP1 above a long's entry
        ok2 = lng & f["zone2_px"].notna()
        self.assertTrue((f["zone2_px"][ok2] > f["zone_px"][ok2]).all())


class RangeBoxFailedTests(unittest.TestCase):
    def test_fades_only_after_a_close_outside_and_back_inside(self):
        from fxbot.strategies import RangeBoxFailed
        rng = np.random.default_rng(4)
        n = 700
        idx = pd.date_range("2022-01-03", periods=n, freq="h", tz="UTC")
        c = 1.10 + 0.0035 * np.sin(np.arange(n) / 8.0) + rng.normal(0, 0.0003, n)
        c[600], c[601], c[602] = c[599] - 0.006, c[599] - 0.0062, c[599] - 0.0025   # dips out under the box, then closes back inside
        m = pd.DataFrame({"open": np.r_[c[0], c[:-1]], "high": c + 0.0004, "low": c - 0.0004, "close": c, "volume": 1.0}, index=idx)
        f = RangeBoxFailed().frame(m)
        self.assertEqual(int(f["long"].sum()), 1)                                    # the false break is faded exactly once
        bot = m["low"].shift(1).rolling(100).min()
        for i in np.flatnonzero(f["long"].to_numpy()):
            prior = m["close"].iloc[max(0, i - 6):i]
            self.assertTrue((prior < bot.iloc[max(0, i - 6):i]).any())               # a close below the box came first
            self.assertGreater(m["close"].iloc[i], bot.iloc[i - 1] - 1e-9 if i else 0)
            self.assertGreater(f["zone_px"].iloc[i], m["close"].iloc[i])               # TP1 (box middle) above the entry
        self.assertTrue((f["stop"][f["long"] | f["short"]] > 0).all())


class VolumeConfirmTests(unittest.TestCase):
    def test_volume_gate_only_removes_signals_and_mid_keeps_volume(self):
        from fxbot.data import mid
        from fxbot.strategies import RangeBox200
        rng = np.random.default_rng(6)
        n = 700
        idx = pd.date_range("2022-01-03", periods=n, freq="h", tz="UTC")
        c = 1.10 + 0.004 * np.sin(np.arange(n) / 9.0) + rng.normal(0, 0.0004, n)
        sp = 0.00008
        df = pd.DataFrame({"bo": np.r_[c[0], c[:-1]] - sp, "bh": c + 0.0006 - sp, "bl": c - 0.0006 - sp, "bc": c - sp,
                           "ao": np.r_[c[0], c[:-1]] + sp, "ah": c + 0.0006 + sp, "al": c - 0.0006 + sp, "ac": c + sp,
                           "volume": rng.integers(50, 500, n).astype(float)}, index=idx)
        m = mid(df)
        self.assertIn("volume", m)
        a, b = RangeBox200().frame(m), RangeBox200(vol_mult=1.5).frame(m)
        self.assertFalse((b["long"] & ~a["long"]).any())
        self.assertLessEqual(int(b["long"].sum() + b["short"].sum()), int(a["long"].sum() + a["short"].sum()))


class BoxConfirmTests(unittest.TestCase):
    def _frame(self, strong=True, vol=1000.0, comeback=False):
        n = 400
        idx = pd.date_range("2022-01-03", periods=n, freq="h", tz="UTC")
        rng = np.random.default_rng(2)
        c = 1.10 + 0.002 * np.sin(np.arange(n) / 7.0) + rng.normal(0, 0.0002, n)
        o = np.r_[c[0], c[:-1]]
        h_, l_ = np.maximum(o, c) + 0.0003, np.minimum(o, c) - 0.0003
        v = np.full(n, 100.0)
        b = 350
        top = h_[250:350].max()
        if strong:                                       # big body closing at its high, well above the box, on heavy volume
            o[b], c[b], l_[b], h_[b] = top - 0.0005, top + 0.004, top - 0.0006, top + 0.0041
        else:                                            # a poke above with a long upper wick, small body, no volume
            o[b], c[b], l_[b], h_[b] = top - 0.0002, top + 0.0002, top - 0.0004, top + 0.004
        v[b] = vol
        if comeback:
            o[b + 1], c[b + 1], h_[b + 1], l_[b + 1] = c[b], top - 0.0015, c[b] + 0.0001, top - 0.0016
        return pd.DataFrame({"open": o, "high": h_, "low": l_, "close": c, "volume": v}, index=idx), b

    def test_confirmed_breakout_is_traded_with_and_failed_one_is_faded(self):
        from alpacabot.confirm import confirm_signals
        for strong, vol, comeback, want in ((True, 1000.0, False, ("long", "cont")), (False, 100.0, True, ("short", "fail"))):
            m, b = self._frame(strong, vol, comeback)
            top = m["high"].shift(1).rolling(100).max().to_numpy()
            bot = m["low"].shift(1).rolling(100).min().to_numpy()
            a = (m["high"] - m["low"]).rolling(14).mean().to_numpy()
            al = np.full(len(m), np.nanmean(a) * 0.5)                 # current ATR above its long-run level: volatility expanding
            r = confirm_signals(m["open"], m["high"], m["low"], m["close"], m["volume"], a, al, top, bot, np.ones(len(m), bool), mode="both")
            k = [x for x in np.flatnonzero(r["long"] | r["short"]) if x >= b]       # the bar the test builds (earlier noise may also signal)
            self.assertTrue(len(k) >= 1)
            i = k[0]
            self.assertEqual(("long" if r["long"][i] else "short", r["kind"][i]), want)
            self.assertGreater(r["stop"][i], 0)


class VoteTests(unittest.TestCase):
    def test_a_lower_vote_never_confirms_fewer_breakouts(self):
        from alpacabot.confirm import confirm_signals
        m, _ = BoxConfirmTests()._frame(True, 1000.0, False)
        top = m["high"].shift(1).rolling(100).max().to_numpy()
        bot = m["low"].shift(1).rolling(100).min().to_numpy()
        a = (m["high"] - m["low"]).rolling(14).mean().to_numpy()
        al = np.full(len(m), np.nanmean(a))
        ok = np.ones(len(m), bool)
        n = {vt: int(confirm_signals(m["open"], m["high"], m["low"], m["close"], m["volume"], a, al, top, bot, ok, mode="continuation",
                                     vote=vt)["long"].sum()) for vt in (0.6, 0.8, 1.0)}
        self.assertGreaterEqual(n[0.6], n[0.8])
        self.assertGreaterEqual(n[0.8], n[1.0])
