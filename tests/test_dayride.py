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
        return self.orders.get(oid, {"id": oid, "status": "new"})

    def cancel_order(self, oid):
        self.cancelled.append(oid)
        if oid in self.orders:
            self.orders[oid]["status"] = "canceled"


class DayRideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.b = Broker()
        self.data = {"UP": five(True), "FLAT": five(True, end=datetime(2026, 10, 27, 11, 0, tzinfo=ET)) * 0 + 100}

        def fetch(sym, tf, start, end, feed):
            return daily() if tf == "1Day" else self.m5
        self.m5 = five(True)
        cfg = {**dr.RIDE, "paused": False, "symbols": ["UP"]}
        self.r = dr.DayRide(self.b, state_dir=self.tmp.name, cfg=cfg, fetch=fetch)
        self.r.news_fetch = lambda url: []                     # no network: Fed + jobs-report fallback calendar

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
        r2 = dr.DayRide(self.b, state_dir=self.tmp.name, cfg={**dr.RIDE, "paused": False, "symbols": ["UP"]}, fetch=lambda *a: daily() if a[1] == "1Day" else self.m5)
        r2.news_fetch = lambda url: []
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


class AdoptTests(unittest.TestCase):
    def test_lost_ride_is_adopted_with_its_stop(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            b = Broker()
            b.held = {"ABBV": 2, "AMT": -4, "GONE": 0}
            b.order_by_client_id = lambda cid: {"id": "stop-" + cid}
            with open(f"{tmp}/journal.jsonl", "w") as f:
                for sym, side, qty in (("ABBV", 1, 2), ("AMT", -1, 4), ("GONE", 1, 1), ("DONE", 1, 1)):
                    f.write(json.dumps({"t": "2026-10-28T11:08:38-04:00", "kind": "open", "symbol": sym, "side": side, "qty": qty,
                                        "fill": 100.0, "stop": 99.0}) + "\n")
                f.write(json.dumps({"t": "2026-10-28T11:30:00-04:00", "kind": "close", "symbol": "DONE"}) + "\n")
            r = dr.DayRide(b, state_dir=tmp, cfg={**dr.RIDE, "paused": False, "symbols": []}, fetch=lambda *a: daily())
            r.news_fetch = lambda url: []
            r.state = {"date": "2026-10-28", "done": [], "open": {}}
            r.adopt(datetime(2026, 10, 28, 12, 0, tzinfo=ET))
            self.assertEqual(sorted(r.state["open"]), ["ABBV", "AMT"])
            self.assertEqual(r.state["open"]["ABBV"]["stop_id"], "stop-rd-s-ABBV-202610281108")
            self.assertEqual(r.state["open"]["AMT"]["side"], -1)
            r.step(datetime(2026, 10, 28, 13, 0, 30, tzinfo=ET))                 # Fed day: adopted rides are flattened too
            self.assertEqual(r.state["open"], {})


class RideNewsTests(unittest.TestCase):
    def test_high_impact_usd_release_pauses_and_flattens(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            b = Broker()
            r = dr.DayRide(b, state_dir=tmp, cfg={**dr.RIDE, "paused": False, "symbols": ["UP"]}, fetch=lambda *a: daily() if a[1] == "1Day" else five(True))
            r.news_fetch = lambda url: [{"title": "ISM Manufacturing PMI", "country": "USD", "date": "2026-10-27T10:00:00-04:00", "impact": "High"},
                                        {"title": "ECB", "country": "EUR", "date": "2026-10-27T12:00:00-04:00", "impact": "High"}]
            r.state = {"date": "2026-10-27", "done": [], "open": {"UP": {"side": 1, "qty": 5, "entry": 100.0, "stop": 99.0, "stop_id": None,
                                                                         "opened": "x"}}}
            r.adopted = True
            r.step(datetime(2026, 10, 27, 10, 50, 30, tzinfo=ET))          # within an hour after the ISM release
            self.assertEqual(r.state["open"], {})
            self.assertTrue(r.state["news_logged"].startswith("news pause: USD ISM"))
            r.last_slot = None
            r.step(datetime(2026, 10, 27, 11, 5, 30, tzinfo=ET))           # window over; the EUR release does not pause stocks
            self.assertIn("UP", r.state["open"])

    def test_config(self):
        self.assertEqual((dr.RIDE["news_before"], dr.RIDE["news_after"]), (60, 60))


class MissedFlatTests(unittest.TestCase):
    """2026-10-29: a 403 on the first 15:55 exit stopped the loop and ECL / GOOGL stayed open overnight."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.b = Broker()
        self.r = dr.DayRide(self.b, state_dir=self.tmp.name, cfg={**dr.RIDE, "paused": False, "symbols": []}, fetch=lambda *a: daily())
        self.r.news_fetch = lambda url: []
        self.r.adopted = True
        pos = lambda: {"side": 1, "qty": 2, "entry": 100.0, "stop": 99.0, "stop_id": None, "opened": "x"}   # noqa: E731
        self.r.state = {"date": "2026-10-29", "done": [], "open": {"ECL": pos(), "GOOGL": pos()}}
        self.b.held = {"ECL": 2, "GOOGL": 2}                               # still held at the broker

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_failed_exit_does_not_block_the_others(self):
        real = self.b.submit_market

        def boom(sym, qty, cid, side="buy"):
            if sym == "ECL":
                raise RuntimeError("403 insufficient qty available")
            return real(sym, qty, cid, side)
        self.b.submit_market = boom
        self.r.step(datetime(2026, 10, 29, 15, 55, 30, tzinfo=ET))
        self.assertEqual(sorted(self.r.state["open"]), ["ECL"])          # GOOGL closed, ECL kept for the next try

    def test_carried_over_ride_closes_at_the_next_open(self):
        self.r.step(datetime(2026, 10, 30, 9, 31, 30, tzinfo=ET))
        self.assertEqual(self.r.state["open"], {})
        self.assertEqual(self.r.state["carried"], [])

    def test_exit_waits_for_the_stop_cancel(self):
        so = self.b.submit_order("ECL", 2, "sell", "stop", "day", "x", stop_price=99.0)
        self.r.state["open"]["ECL"]["stop_id"] = so["id"]
        self.r.exit("ECL", datetime(2026, 10, 29, 15, 55, 30, tzinfo=ET), "15:55 flat")
        self.assertIn(so["id"], self.b.cancelled)
        self.assertNotIn("ECL", self.r.state["open"])


if __name__ == "__main__":
    unittest.main()


class PendingExitTests(unittest.TestCase):
    """2026-10-30: the 09:30 exits sat in pending_new; the 09:38 retry sent a second order and opened GOOGL long / ECL short."""

    def test_a_working_exit_is_never_sent_twice(self):
        with tempfile.TemporaryDirectory() as d:
            b = Broker()
            sent = []

            def slow_market(sym, qty, cid, side="buy"):
                b.n += 1
                o = {"id": f"m{b.n}", "status": "pending_new", "symbol": sym, "qty": qty, "side": side}
                b.orders[o["id"]] = o
                sent.append(o["id"])
                return o
            b.submit_market = slow_market
            b.held = {"GOOGL": -3}
            r = dr.DayRide(b, state_dir=d, cfg={**dr.RIDE, "paused": False, "symbols": []}, fetch=lambda *a: daily())
            r._wait = lambda o, now, tries=10, sleep=1.0: b.orders.get(o["id"], o) if o and o.get("id") else {"status": "rejected"}
            r.state = {"date": "2026-10-30", "done": [], "open": {"GOOGL": {"side": -1, "qty": 3, "entry": 281.0, "stop": 285.0, "stop_id": None,
                                                                             "opened": "x"}}}
            now = datetime(2026, 10, 30, 9, 30, 45, tzinfo=ET)
            r.exit("GOOGL", now, "carried over")
            r.exit("GOOGL", now + timedelta(minutes=8), "carried over")       # still pending: no second order
            self.assertEqual(len(sent), 1)
            b.orders[sent[0]].update(status="filled", filled_avg_price="281.40")
            r.exit("GOOGL", now + timedelta(minutes=9), "carried over")
            self.assertEqual((len(sent), r.state["open"]), (1, {}))


class RideLockTests(unittest.TestCase):
    def test_ride_keeps_half_the_best_gain(self):
        with tempfile.TemporaryDirectory() as d:
            b = Broker()
            r = dr.DayRide(b, state_dir=d, cfg={**dr.RIDE, "paused": False, "symbols": []}, fetch=lambda *a: daily())
            idx = pd.date_range("2026-10-30 14:00", periods=4, freq="5min", tz="UTC")
            m = pd.DataFrame({"open": [100, 101, 103, 102], "high": [101, 103, 104, 102.5], "low": [99.8, 100.5, 102, 101.9],
                              "close": [101, 103, 102.5, 101.9]}, index=idx, dtype=float)
            pos = {"side": 1, "qty": 2, "entry": 100.0, "stop": 98.0, "stop_id": None, "opened": "2026-10-30T10:00:00-04:00"}
            r.state = {"date": "2026-10-30", "done": [], "open": {"X": pos}}
            now = datetime(2026, 10, 30, 10, 20, 30, tzinfo=ET)
            self.assertTrue(r.locked_out("X", pos, m, now))              # best +2R (104), lock 102, close 101.9 -> out
            self.assertNotIn("X", r.state["open"])
            pos2 = dict(pos, entry=100.0, stop=90.0)                     # risk 10: +0.4R best -> not armed
            r.state["open"]["Y"] = pos2
            self.assertFalse(r.locked_out("Y", pos2, m, now))


class PausedRideTests(unittest.TestCase):
    def test_paused_ride_takes_no_new_entries(self):
        self.assertTrue(dr.RIDE.get("paused"))                       # owner 2026-10-30: paused live
        with tempfile.TemporaryDirectory() as d:
            b = Broker()
            m5 = five(True)
            r = dr.DayRide(b, state_dir=d, cfg={**dr.RIDE, "symbols": ["UP"]}, fetch=lambda s, tf, *a: daily() if tf == "1Day" else m5)
            r.news_fetch = lambda url: []
            r.step(datetime(2026, 10, 27, 11, 0, 30, tzinfo=ET))
            self.assertEqual(r.state["open"], {})
            self.assertEqual(b.orders, {})


class GoneAtBrokerTests(unittest.TestCase):
    def test_ride_closed_outside_the_bot_is_not_closed_again(self):
        with tempfile.TemporaryDirectory() as d:
            b = Broker()
            b.held = {}                                                   # the owner already closed it
            r = dr.DayRide(b, state_dir=d, cfg={**dr.RIDE, "paused": False, "symbols": []}, fetch=lambda *a: daily())
            r.state = {"date": "2026-10-30", "done": [], "open": {"BAC": {"side": -1, "qty": 17, "entry": 50.0, "stop": 51.0, "stop_id": None,
                                                                           "opened": "x"}}}
            r.exit("BAC", datetime(2026, 10, 30, 15, 55, 30, tzinfo=ET), "15:55 flat")
            self.assertEqual(r.state["open"], {})
            self.assertFalse([o for o in b.orders.values() if o["id"].startswith("m")])
