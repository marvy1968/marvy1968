import tempfile
import unittest
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from alpacabot import dayride as dr

ET = ZoneInfo("America/New_York")


def five(up=True, days=6, end=datetime(2026, 10, 27, 11, 0, tzinfo=ET)):
    rows = []
    for d in pd.bdate_range(end=end.date(), periods=days):
        for j in range(78):
            t = pd.Timestamp(f"{d.date()} 09:30", tz=ET) + pd.Timedelta(minutes=5 * j)
            if t + pd.Timedelta(minutes=5) > pd.Timestamp(end):
                break
            rows.append(t)
    idx = pd.DatetimeIndex(rows).tz_convert("UTC")
    n = len(idx)
    base = np.linspace(100, 100, n)
    today = idx.tz_convert(ET).date == end.date()
    base[today] = 100 + (1 if up else -1) * np.linspace(0, 4, today.sum())
    c = base + np.random.default_rng(0).normal(0, 0.02, n)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.02, "low": np.minimum(o, c) - 0.02, "close": c, "volume": 1e4}, index=idx)


def daily(n=40):
    idx = pd.bdate_range(end="2026-10-27", periods=n).tz_localize(ET).tz_convert("UTC")
    c = np.full(n, 100.0)
    return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1e6}, index=idx)


class Broker:
    def __init__(self):
        self.orders, self.cancelled, self.n = {}, [], 0
        self.held = {}

    def positions(self):
        return dict(self.held)

    def equity(self):
        return 50000.0

    def shortable(self, sym):
        return True

    def submit_market(self, sym, qty, cid, side="buy"):
        self.n += 1
        o = {"id": f"m{self.n}", "status": "filled", "filled_avg_price": "102.00", "symbol": sym, "qty": qty, "side": side}
        self.orders[o["id"]] = o
        return o

    def submit_order(self, sym, qty, side, typ, tif, cid, stop_price=None):
        self.n += 1
        o = {"id": f"s{self.n}", "status": "new", "stop_price": stop_price, "symbol": sym}
        self.orders[o["id"]] = o
        return o

    def order(self, oid):
        return self.orders[oid]

    def cancel_order(self, oid):
        self.cancelled.append(oid)


class DayRideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.b = Broker()
        self.data = {"UP": five(True), "FLAT": five(True, end=datetime(2026, 10, 27, 11, 0, tzinfo=ET)) * 0 + 100}

        def fetch(sym, tf, start, end, feed):
            return daily() if tf == "1Day" else self.m5
        self.m5 = five(True)
        cfg = {**dr.RIDE, "symbols": ["UP"]}
        self.r = dr.DayRide(self.b, state_dir=self.tmp.name, cfg=cfg, fetch=fetch)

    def tearDown(self):
        self.tmp.cleanup()

    def test_enters_on_a_hard_move_with_a_broker_stop_and_caps_size(self):
        now = datetime(2026, 10, 27, 11, 0, 30, tzinfo=ET)
        self.r.step(now)
        pos = self.r.state["open"]["UP"]
        self.assertEqual(pos["side"], 1)
        self.assertLessEqual(pos["qty"] * pos["entry"], 0.05 * 50000 / 3 + 1)
        self.assertEqual(self.b.orders[pos["stop_id"]]["stop_price"], round(102.0 - 0.5 * 2.0, 2))
        self.r.last_slot = None
        self.r.step(now + timedelta(minutes=5))                   # one ride per stock per day
        self.assertEqual(len([o for o in self.b.orders.values() if o["id"].startswith("m")]), 1)

    def test_no_entry_in_a_held_stock_or_outside_hours(self):
        self.b.held = {"UP": 10}
        self.r.step(datetime(2026, 10, 27, 11, 0, 30, tzinfo=ET))
        self.assertEqual(self.r.state["open"], {})
        self.b.held = {}
        self.r.last_slot = None
        self.r.step(datetime(2026, 10, 27, 15, 5, 30, tzinfo=ET))
        self.assertEqual(self.r.state["open"], {})

    def test_flat_at_1555_and_on_fed_day(self):
        self.r.step(datetime(2026, 10, 27, 11, 0, 30, tzinfo=ET))
        self.assertIn("UP", self.r.state["open"])
        self.r.last_slot = None
        self.r.step(datetime(2026, 10, 27, 15, 55, 30, tzinfo=ET))
        self.assertEqual(self.r.state["open"], {})
        self.assertTrue(self.b.cancelled)
        r2 = dr.DayRide(self.b, state_dir=self.tmp.name, cfg={**dr.RIDE, "symbols": ["UP"]}, fetch=lambda *a: daily() if a[1] == "1Day" else self.m5)
        r2.state = {"date": "2026-10-28", "done": [], "open": {"UP": {"side": 1, "qty": 5, "entry": 100.0, "stop": 99.0, "stop_id": None,
                                                                       "opened": "x"}}}
        r2.step(datetime(2026, 10, 28, 13, 0, 30, tzinfo=ET))
        self.assertEqual(r2.state["open"], {})

    def test_reversal_closes(self):
        self.r.step(datetime(2026, 10, 27, 11, 0, 30, tzinfo=ET))
        self.m5 = five(False)
        self.r.last_slot = None
        self.r.step(datetime(2026, 10, 27, 11, 5, 30, tzinfo=ET))
        self.assertNotIn("UP", self.r.state["open"])

    def test_live_config(self):
        self.assertEqual((dr.RIDE["gross_cap"], dr.RIDE["max_open"], dr.RIDE["risk_pct"]), (0.05, 3, 0.0025))
        self.assertEqual(dr.RIDE["flat_at"], dtime(15, 55))


if __name__ == "__main__":
    unittest.main()
