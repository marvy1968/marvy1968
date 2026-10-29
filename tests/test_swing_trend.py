import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import alpacabot.swing_paper as sp


def _daily(drift, n=420, seed=0):
    idx = pd.bdate_range("2025-01-01", periods=n)
    c = 100 * np.exp(np.cumsum(np.full(n, drift) + np.random.default_rng(seed).normal(0, 0.004, n)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.003, "low": np.minimum(o, c) * 0.997, "close": c, "volume": 1e6}, index=idx)


class SwingTrendFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = sp.SwingRunner(None, state_dir=self.tmp.name, base_equity=50000.0)
        self.bars = {"UP": _daily(0.003), "DN": _daily(-0.003), "UP2": _daily(0.003, seed=2)}
        self._db, self._tf = sp.daily_bars, sp.TREND_FILTERS
        sp.daily_bars = lambda sym, now, fetch=None, days=260: self.bars[sym]
        self.now = datetime(2026, 10, 28, 16, 30, tzinfo=ZoneInfo("America/New_York"))

    def tearDown(self):
        sp.daily_bars, sp.TREND_FILTERS = self._db, self._tf
        self.tmp.cleanup()

    def test_entries_need_a_long_up_trend_and_reversals_exit(self):
        sp.TREND_FILTERS = {"long": True, "reversal": True}
        held = {"DN": {"qty": 5, "entry": 90.0, "side": 1}, "UP2": {"qty": 5, "entry": 90.0, "side": 1}}
        exits, entries = self.r.trend_filters(held, [], [{"symbol": "UP"}, {"symbol": "DN"}], self.now)
        self.assertEqual([e["symbol"] for e in entries], ["UP"])
        self.assertEqual(exits, ["DN"])

    def test_live_setting_gates_topdown_only(self):
        sp.TREND_FILTERS = {"long": ("topdown",), "reversal": True}
        _, entries = self.r.trend_filters({}, [], [{"symbol": "DN"}, {"symbol": "DN", "strategy": "oversold"}], self.now)
        self.assertEqual(entries, [{"symbol": "DN", "strategy": "oversold"}])
        self.assertEqual(self._tf, {"long": ("topdown",), "reversal": True})

    def test_off_changes_nothing(self):
        sp.TREND_FILTERS = None
        held = {"DN": {"qty": 5, "entry": 90.0, "side": 1}}
        self.assertEqual(self.r.trend_filters(held, [], [{"symbol": "DN"}], self.now), ([], [{"symbol": "DN"}]))


class CloseOnlyTests(unittest.TestCase):
    def test_close_only_the_named_symbol(self):
        class B:
            def __init__(self):
                self.sent, self.cancelled = [], []

            def cancel_order(self, oid):
                self.cancelled.append(oid)

            def submit_order(self, sym, qty, side, typ, tif, cid):
                self.sent.append(sym)
                return {"id": sym}

            def order(self, oid):
                return {"status": "filled", "filled_avg_price": "61.80"}
        with tempfile.TemporaryDirectory() as tmp:
            b = B()
            r = sp.SwingRunner(b, state_dir=tmp, base_equity=50000.0, sleep=lambda *_: None)
            r.settle_stops = lambda now: None
            r.state["positions"] = {"NFLX": {"qty": 157, "entry": 63.04, "stop": 62.26, "stop_order_id": "s1", "entry_date": "2026-10-27"},
                                    "XOM": {"qty": 14, "entry": 180.12, "stop": 165.9, "stop_order_id": "s2"}}
            out = r.close_all(datetime(2026, 10, 28, 10, 30, tzinfo=ZoneInfo("America/New_York")), only=["NFLX"])
            self.assertEqual(list(out), ["NFLX"])
            self.assertEqual((b.sent, b.cancelled), (["NFLX"], ["s1"]))
            self.assertEqual(list(r.state["positions"]), ["XOM"])


class StairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

        class B:
            def __init__(self):
                self.cancelled, self.oco, self.stops = [], [], []

            def cancel_order(self, oid):
                self.cancelled.append(oid)

            def submit_oco(self, sym, qty, tp, stop, cid, side="sell"):
                self.oco.append((sym, tp, stop))
                return {"id": cid}

            def submit_order(self, sym, qty, side, typ, tif, cid, stop_price=None):
                self.stops.append((sym, stop_price))
                return {"id": cid}
        self.b = B()
        self.r = sp.SwingRunner(self.b, state_dir=self.tmp.name, base_equity=50000.0)
        self._db, self._st = sp.daily_bars, sp.STAIR
        self.d = _daily(0.003)
        sp.daily_bars = lambda sym, now, fetch=None, days=260: self.d
        self.now = datetime(2026, 10, 28, 16, 30, tzinfo=ZoneInfo("America/New_York"))

    def tearDown(self):
        sp.daily_bars, sp.STAIR = self._db, self._st
        self.tmp.cleanup()

    def test_steps_up_and_never_down(self):
        sp.STAIR = {"step_r": 1.0}
        close = float(self.d["close"].iloc[-1])
        held = {"UP": {"qty": 10, "entry": close - 25.0, "stop": close - 35.0, "oco": True, "zone_tp": close + 50, "stop_order_id": "o1"}}
        self.r.stair_trail(held, [], self.now)                       # 2.5 R up -> step 2 -> stop at entry + 1 R
        p = held["UP"]
        self.assertEqual(p["stair"], 2)
        self.assertAlmostEqual(p["stop"], round(close - 15.0, 2))
        self.assertEqual(self.b.cancelled, ["o1"])
        self.assertEqual(self.b.oco[-1][1:], (close + 50, round(close - 15.0, 2)))
        self.r.stair_trail(held, [], self.now)                       # same close: no new order
        self.assertEqual(len(self.b.oco), 1)

    def test_not_before_one_r_and_not_short_or_off(self):
        sp.STAIR = {"step_r": 1.0}
        close = float(self.d["close"].iloc[-1])
        held = {"A": {"qty": 1, "entry": close - 5.0, "stop": close - 15.0}, "S": {"qty": 1, "entry": close + 5, "stop": close + 15, "side": -1}}
        self.r.stair_trail(held, [], self.now)
        self.assertEqual((self.b.oco, self.b.stops), ([], []))
        sp.STAIR = None
        held = {"UP": {"qty": 1, "entry": close - 25.0, "stop": close - 35.0}}
        self.r.stair_trail(held, [], self.now)
        self.assertEqual(self.b.stops, [])


class LogOnlyTests(unittest.TestCase):
    def test_live_switches(self):
        import json
        from pathlib import Path
        z = json.loads(Path(sp.__file__).with_name("zones.json").read_text())["swing"]
        self.assertFalse(z["topdown"]["new_entries"])
        self.assertFalse(z["funnel"]["new_entries"])
        self.assertFalse(z["oversold"]["new_entries"])
        self.assertTrue(z["index_trend"]["live"])


if __name__ == "__main__":
    unittest.main()


class WeekPicksPlanTests(unittest.TestCase):
    def test_batch_becomes_entries_and_old_picks_exit(self):
        import json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            r = sp.SwingRunner(None, state_dir=d, base_equity=50000.0)
            log = Path(d) / "wp.json"
            log.write_text(json.dumps({"batches": [{"made": "2026-10-29", "picks": [
                {"symbol": "CAT", "close": 700.0, "stop_10d_low": 680.0, "tp_est": 740.0},
                {"symbol": "BAD", "close": 50.0, "stop_10d_low": 51.0, "tp_est": 55.0}]}]}))
            r.zone_config = lambda: {"live": True, "weekpicks": {"live": True, "risk_pct": 0.005, "max_frac": 0.15, "hold_sessions": 5, "log": str(log)}}
            spy = pd.DataFrame({"close": 1.0}, index=pd.bdate_range("2026-10-20", "2026-10-29"))
            held = {"GOOGL": {"qty": 3, "entry": 300.0, "stop": 290.0, "side": 1, "strategy": "weekpicks", "entry_date": "2026-10-23"},
                    "NEW": {"qty": 3, "entry": 300.0, "stop": 290.0, "side": 1, "strategy": "weekpicks", "entry_date": "2026-10-28"}}
            now = datetime(2026, 10, 29, 16, 25, tzinfo=ZoneInfo("America/New_York"))
            exits, entries = r.weekpicks_plan(spy, {"CAT": 700.0}, held, [], [], 50000.0, 30000.0, False, now)
            self.assertEqual(exits, ["GOOGL"])                                  # 5 sessions held (23, 26, 27, 28, 29)
            self.assertEqual([e["symbol"] for e in entries], ["CAT"])          # BAD: stop above the close -> skipped
            e = entries[0]
            self.assertEqual((e["strategy"], e["zone_tp"], e["stop"]), ("weekpicks", 740.0, 680.0))
            self.assertEqual(e["qty"], int(min(50000 * 0.005 / (20 + 700 * sp.SLIP_BPS / 1e4), 50000 * 0.15 / 700)))
            self.assertTrue(json.loads(log.read_text())["batches"][0]["ordered"])
            _, again = r.weekpicks_plan(spy, {"CAT": 700.0}, {}, [], [], 50000.0, 30000.0, False, now)
            self.assertEqual(again, [])                                         # a batch is ordered once
