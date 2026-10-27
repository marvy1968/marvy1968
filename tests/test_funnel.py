import unittest

import numpy as np
import pandas as pd


def _daily(n=400, drift=0.15, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-02", periods=n)
    t = np.arange(n)
    c = 100 + drift * t + 3 * np.sin(t / 5.0) + rng.normal(0, 0.4, n)
    o = np.r_[c[0], c[:-1]] + rng.normal(0, 0.2, n)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.6, "low": np.minimum(o, c) - 0.6, "close": c,
                         "volume": 1e6 * (1 + 0.5 * (np.sin(t / 3.0) > 0.6))}, index=idx)


class FunnelTests(unittest.TestCase):
    def test_uptrend_gives_longs_only_with_stop_below_and_tp_above(self):
        from alpacabot.funnel import frame
        f = frame(_daily())
        longs = f[f["long_entry"]]
        self.assertGreater(len(longs), 0)
        self.assertFalse(f["short_entry"].any())                    # stage 2 all the way: no shorts
        self.assertTrue((longs["long_stop"] < longs["close"]).all())
        self.assertTrue((longs["long_tp"] > longs["close"]).all())
        dist = longs["close"] - longs["long_stop"]
        self.assertTrue((dist >= longs["atr"] * 0.999).all() and (dist <= longs["atr"] * 3.001).all())   # 1-3 ATR: never the wrong side
        self.assertTrue(np.allclose(longs["long_tp"] - longs["close"], 1.5 * dist))

    def test_downtrend_gives_shorts_mirrored(self):
        from alpacabot.funnel import frame
        f = frame(_daily(drift=-0.15, seed=2))
        self.assertGreater(int(f["short_entry"].sum()), 0)
        self.assertFalse(f["long_entry"].any())
        s = f[f["short_entry"]]
        self.assertTrue((s["short_stop"] > s["close"]).all() and (s["short_tp"] < s["close"]).all())

    def test_backtest_runs_and_reports(self):
        from alpacabot.funnel import backtest
        d = _daily()
        r = backtest({"AAA": d}, d.index[200].date(), d.index[-1].date())
        self.assertGreater(r["trades"], 0)
        self.assertEqual(r["shorts"], 0)
        self.assertEqual(sum(r["exits"].values()), r["trades"])


if __name__ == "__main__":
    unittest.main()


class FunnelShortVariantTests(unittest.TestCase):
    def test_market_filter_and_side_selection(self):
        from alpacabot.funnel import backtest, market_down
        d = _daily(drift=-0.15, seed=2)
        a, b = d.index[200].date(), d.index[-1].date()
        allowed = backtest({"AAA": d}, a, b, sides=("short",), short_ok=market_down(d))
        blocked = backtest({"AAA": d}, a, b, sides=("short",), short_ok=market_down(d) & False)
        self.assertGreater(allowed["shorts"], 0)
        self.assertEqual((allowed["longs"], blocked["trades"]), (0, 0))
        strict = backtest({"AAA": d}, a, b, sides=("short",), short_rules={"need": 4})
        self.assertLessEqual(strict["trades"], backtest({"AAA": d}, a, b, sides=("short",))["trades"])


class FunnelLiveTests(unittest.TestCase):
    class B:
        def __init__(self, shortable=True, held=None):
            self.sent, self.oco, self.orders, self.short_ok, self.held = [], [], {}, shortable, held or {}
        def equity(self): return 50000.0
        def is_session(self, day): return True
        def quote(self, s): return (100.0, 100.0)
        def shortable(self, s): return self.short_ok
        def positions(self): return dict(self.held)
        def submit_order(self, *a, **k):
            self.sent.append(a)
            return {"id": a[-1] if isinstance(a[-1], str) else a[0], "status": "filled", "filled_qty": str(a[1]), "filled_avg_price": "100"}
        def submit_oco(self, sym, qty, limit, stop, coid, side="sell"):
            self.oco.append((sym, qty, limit, stop, side))
            return {"id": "oco-" + sym, "status": "new"}
        def order(self, oid): return {"id": oid, "status": "filled", "filled_qty": "10", "filled_avg_price": "100"}
        def cancel_order(self, oid): pass

    def _runner(self, b, tmp, entry):
        from alpacabot.swing_paper import SwingRunner
        r = SwingRunner(b, state_dir=tmp, fetch=lambda *a: pd.DataFrame(), sleep=lambda *_: None)
        r.state["plan"] = {"made": "2025-10-13", "exits": [], "entries": [entry]}
        return r

    def test_short_entry_sells_then_places_a_buy_side_oco_and_books_short_pnl(self):
        import json
        import tempfile
        from datetime import datetime
        from pathlib import Path
        et = __import__("zoneinfo").ZoneInfo("America/New_York")
        with tempfile.TemporaryDirectory() as d:
            b = self.B()
            r = self._runner(b, d, {"symbol": "XYZ", "qty": 10, "stop": 104.0, "side": -1, "strategy": "funnel", "signal_close": 100.0,
                                    "zone_tp": 94.0, "atr": 2.0})
            r.open(datetime(2025, 10, 14, 9, 20, tzinfo=et), wait_until=datetime(2025, 10, 14, 9, 20, tzinfo=et))
            self.assertEqual(b.sent[0][:3], ("XYZ", 10, "sell"))
            self.assertEqual(b.oco, [("XYZ", 10, 94.0, 104.0, "buy")])
            pos = r.state["positions"]["XYZ"]
            self.assertEqual((pos["side"], pos["strategy"]), (-1, "funnel"))
            r.record("XYZ", r.state["positions"].pop("XYZ"), 94.0, "zone", datetime(2025, 10, 15, 16, 20, tzinfo=et))
            t = json.loads((Path(d) / "trades.jsonl").read_text().splitlines()[-1])
            self.assertEqual((t["side"], t["pnl"], t["r"], t["strategy"]), ("short", 60.0, 1.5, "funnel"))

    def test_oversold_entry_waits_for_10am_and_skips_a_broken_stop(self):
        """Owner 2026-10-24: oversold entries are sent at 10:00 (open(late=True)), not at the open; funnel entries keep the open;
        an oversold stock already at its stop by then is skipped."""
        import tempfile
        from datetime import datetime
        et = __import__("zoneinfo").ZoneInfo("America/New_York")
        t = datetime(2025, 10, 14, 9, 20, tzinfo=et)
        with tempfile.TemporaryDirectory() as d:
            b = self.B()
            r = self._runner(b, d, {"symbol": "OSD", "qty": 10, "stop": 97.0, "side": 1, "strategy": "oversold", "signal_close": 100.0,
                                    "zone_tp": 104.0, "atr": 2.0})
            r.state["plan"]["entries"].append({"symbol": "FUN", "qty": 5, "stop": 97.0, "side": 1, "strategy": "funnel", "signal_close": 100.0,
                                               "zone_tp": 104.0, "atr": 2.0})
            r.open(t, wait_until=t)
            self.assertEqual([x[0] for x in b.sent], ["FUN"])                          # the funnel buys at the open, oversold waits
            self.assertNotIn("OSD", r.state["positions"])
            r.open(t, wait_until=t, late=True)
            self.assertEqual([x[0] for x in b.sent], ["FUN", "OSD"])                   # 10:00: the deferred oversold entry
            self.assertIn("OSD", r.state["positions"])
            self.assertEqual(r.open(t, wait_until=t, late=True), {})                   # never twice
        with tempfile.TemporaryDirectory() as d:
            b = self.B()
            b.quote = lambda s: (96.5, 96.6)                                            # already under its 97.00 stop at 10:00
            r = self._runner(b, d, {"symbol": "OSD", "qty": 10, "stop": 97.0, "side": 1, "strategy": "oversold", "signal_close": 100.0,
                                    "zone_tp": 104.0, "atr": 2.0})
            r.open(t, wait_until=t)
            r.open(t, wait_until=t, late=True)
            self.assertEqual(b.sent, [])

    def test_short_skipped_when_not_borrowable_or_another_bot_holds_it(self):
        import tempfile
        from datetime import datetime
        et = __import__("zoneinfo").ZoneInfo("America/New_York")
        e = {"symbol": "XYZ", "qty": 10, "stop": 104.0, "side": -1, "strategy": "funnel", "signal_close": 100.0, "zone_tp": 94.0, "atr": 2.0}
        for b in (self.B(shortable=False), self.B(held={"XYZ": 25})):
            with tempfile.TemporaryDirectory() as d:
                r = self._runner(b, d, dict(e))
                r.open(datetime(2025, 10, 14, 9, 20, tzinfo=et), wait_until=datetime(2025, 10, 14, 9, 20, tzinfo=et))
                self.assertEqual(b.sent, [])
                self.assertNotIn("XYZ", r.state["positions"])

    def test_funnel_plan_adds_whole_share_entries_with_its_own_risk(self):
        import tempfile
        from datetime import datetime
        from alpacabot.funnel import frame
        from alpacabot.swing_paper import SwingRunner
        et = __import__("zoneinfo").ZoneInfo("America/New_York")
        full = _daily()
        with tempfile.TemporaryDirectory() as d:
            r = SwingRunner(self.B(), state_dir=d, fetch=lambda *a: pd.DataFrame(), sleep=lambda *_: None)
            f = frame(full, (r.zone_config().get("funnel") or {}).get("rules"))           # the LIVE funnel rules (zones.json)
            cut = int(next(i for i in np.flatnonzero(f["long_entry"].to_numpy()) if i >= 199))   # the runner needs 200 days of history
            bars = full.iloc[:cut + 1]
            exits, entries = r.funnel_plan({"AAA": bars}, {"AAA": float(bars["close"].iloc[-1])}, {}, [], [], 50000.0, 50000.0, False,
                                           datetime(2025, 10, 14, 16, 20, tzinfo=et))
            self.assertEqual(len(entries), 1)
            e = entries[0]
            self.assertEqual((e["symbol"], e["side"], e["strategy"]), ("AAA", 1, "funnel"))
            self.assertIsInstance(e["qty"], int)
            risk = (e["signal_close"] - e["stop"]) * e["qty"]
            self.assertLessEqual(risk, 50000.0 * 0.005 + 1e-6)
            self.assertLessEqual(e["qty"] * e["signal_close"], 50000.0 * 0.334)

    def test_funnel_shorts_stay_off_unless_switched_on(self):
        import tempfile
        from datetime import datetime
        from alpacabot.funnel import frame
        from alpacabot.swing_paper import SwingRunner
        et = __import__("zoneinfo").ZoneInfo("America/New_York")
        full = _daily(drift=-0.15, seed=2)
        f = frame(full)
        cut = int(next(i for i in np.flatnonzero(f["short_entry"].to_numpy()) if i >= 199))
        bars = full.iloc[:cut + 1]
        args = ({"AAA": bars}, {"AAA": float(bars["close"].iloc[-1])}, {}, [], [], 50000.0, 50000.0, False, datetime(2025, 10, 14, 16, 20, tzinfo=et))
        with tempfile.TemporaryDirectory() as d:
            r = SwingRunner(self.B(), state_dir=d, fetch=lambda *a: pd.DataFrame(), sleep=lambda *_: None)
            self.assertEqual(r.funnel_plan(*args)[1], [])                        # zones.json: shorts off (they lost in the check)
            r.zone_config = lambda: {"live": True, "funnel": {"live": True, "shorts": True}}
            self.assertEqual(r.funnel_plan(*args)[1][0]["side"], -1)


class TrendStrengthDownTests(unittest.TestCase):
    def test_strong_downtrend_scores_three_and_weakest_ranked(self):
        from alpacabot.funnel import trend_strength_down, weakest_per_sector
        spy = _daily(drift=0.05, seed=5)
        down = trend_strength_down(_daily(drift=-0.15, seed=2), spy)      # (prices stay positive)
        up = trend_strength_down(_daily(drift=0.15, seed=3), spy)
        self.assertEqual(int(down["votes"].iloc[-1]), 3)
        self.assertLess(int(up["votes"].iloc[-1]), 3)                   # a rising stock is never "strongly negative"
        self.assertFalse(bool(up["ma_dn"].iloc[-1]) or bool(up["rs_dn"].iloc[-1]))
        w = weakest_per_sector({"D": down, "U": up}, {"D": "XLK", "U": "XLK"}, n=1)
        self.assertTrue(bool(w["D"].iloc[-1]) and not bool(w["U"].iloc[-1]))


class SupportResistanceVoteTests(unittest.TestCase):
    def test_sr_votes_mark_a_retest_of_a_twice_touched_low(self):
        from alpacabot.funnel import sr_votes
        from alpacabot.indicators import atr
        lows = [105, 103, 100, 103, 106, 104, 100.2, 103, 106, 107, 105, 100.3, 104]   # 100 touched, then retested
        idx = pd.bdate_range("2026-01-05", periods=len(lows))
        lo = np.array(lows, float)
        d = pd.DataFrame({"open": lo + 1.5, "high": lo + 3, "low": lo, "close": lo + 2, "volume": 1e6}, index=idx)
        a = atr(d, 3).bfill()
        sup, res = sr_votes(d, a, lookback=60, wing=2, tol_atr=0.5, min_touches=2)
        self.assertTrue(bool(sup.iloc[11]))          # the third test of ~100 is at a strong (2-touch) support
        self.assertFalse(bool(sup.iloc[4]))

    def test_six_vote_mode_runs(self):
        from alpacabot.funnel import frame
        f4, f6 = frame(_daily()), frame(_daily(), {"sr_rsi": True})
        self.assertIn("long_entry", f6)
        self.assertGreaterEqual(int(f4["long_entry"].sum()) + 1, 1)


class OversoldModeTests(unittest.TestCase):
    def test_oversold_entry_needs_majority_and_volume(self):
        import numpy as np
        import pandas as pd
        from alpacabot.funnel import frame
        n = 260
        idx = pd.date_range("2025-01-01", periods=n, freq="B")
        c = np.linspace(100, 160, n)
        c[-1] = c[-2] * 0.90                                   # a sharp one-day drop to the lower band
        d = pd.DataFrame({"open": c, "high": c * 1.005, "low": c * 0.995, "close": c, "volume": 1e6}, index=idx)
        d.loc[d.index[-1], "volume"] = 3e6
        f = frame(d, {"oversold": True})
        self.assertTrue(bool(f["long_entry"].iloc[-1]) or not bool(f["long_entry"].iloc[-2]))
        d2 = d.copy()
        d2.loc[d2.index[-1], "volume"] = 1e5                   # no volume -> no entry
        self.assertFalse(bool(frame(d2, {"oversold": True})["long_entry"].iloc[-1]))


class IndexTrendTests(unittest.TestCase):
    def _bars(self, up: bool):
        import numpy as np
        import pandas as pd
        n = 260
        idx = pd.bdate_range("2025-01-01", periods=n)
        c = np.linspace(100, 140, n) if up else np.linspace(140, 100, n)
        return pd.DataFrame({"open": c, "high": c * 1.005, "low": c * 0.995, "close": c, "volume": 1e6}, index=idx)

    def _runner(self, tmp):
        from alpacabot.swing_paper import SwingRunner

        class B:
            def equity(self): return 50000.0
        r = SwingRunner(B(), state_dir=tmp, fetch=lambda *a: __import__("pandas").DataFrame(), sleep=lambda *_: None)
        (__import__("pathlib").Path(tmp) / "zones_override.json").write_text(__import__("json").dumps(
            {"index_trend": {"live": True, "gate_longs": True, "symbols": ["SPY", "QQQ"], "atr_risk_pct": 0.005, "max_frac": 0.30, "stop_atr": 4.0}}))
        return r

    def test_sleeve_buys_above_the_200_day_and_sells_below_and_the_gate_blocks_longs(self):
        import tempfile
        from datetime import datetime
        et = __import__("zoneinfo").ZoneInfo("America/New_York")
        now = datetime(2026, 10, 27, 16, 20, tzinfo=et)
        with tempfile.TemporaryDirectory() as d:
            r = self._runner(d)
            up, down = self._bars(True), self._bars(False)
            stock_long = {"symbol": "XYZ", "qty": 5, "stop": 90.0, "side": 1, "strategy": "oversold"}
            stock_short = {"symbol": "ABC", "qty": 5, "stop": 110.0, "side": -1, "strategy": "funnel"}
            ex, en = r.index_trend_plan(up, {"SPY": up, "QQQ": up}, {}, [], [dict(stock_long), dict(stock_short)], 50000.0, 50000.0, False, now)
            syms = [e["symbol"] for e in en]
            self.assertEqual(syms, ["XYZ", "ABC", "SPY", "QQQ"])                      # risk on: nothing blocked, both index entries
            spy = next(e for e in en if e["symbol"] == "SPY")
            self.assertEqual(spy["strategy"], "trend200")
            self.assertLessEqual(spy["qty"] * spy["signal_close"], 50000 * 0.30 + 1)     # capped at 30% of equity
            self.assertLess(spy["stop"], spy["signal_close"])
            held = {"SPY": {"qty": 10, "entry": 100.0, "stop": 90.0, "strategy": "trend200", "side": 1}}
            ex, en = r.index_trend_plan(down, {"SPY": down, "QQQ": down}, held, [], [dict(stock_long), dict(stock_short)], 50000.0, 50000.0, False, now)
            self.assertEqual(ex, ["SPY"])                                                  # the sleeve sells below the 200-day average
            self.assertEqual([e["symbol"] for e in en], ["ABC"])                           # risk off: the long is blocked, the short stays, no index entry
