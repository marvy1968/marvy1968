import json
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from alpacabot import trendopt as to
from alpacabot import universe
from alpacabot import vwapday as vd

ET = ZoneInfo("America/New_York")
TODAY = pd.Timestamp("2026-11-03")


def make(sym, days=25, today=TODAY):
    """AAA climbs with rising volume today until 12:50 then falls hard; every other day / name drifts up slowly on flat volume."""
    frames, p = [], 100.0
    for d in pd.bdate_range(end=today, periods=days):
        idx = pd.date_range(d + pd.Timedelta(hours=9, minutes=30), periods=390, freq="1min", tz=ET)
        t = np.arange(390)
        if sym == "AAA" and d == today:
            c = p + np.where(t < 200, 0.1 * t, 20 - 0.3 * (t - 200)) + 0.15 * np.sin(t / 2.3)
            v = 2000 * 1.01 ** np.minimum(t, 200)
        else:
            c = p + 0.02 * t + 0.05 * np.sin(t / 2.3)
            v = np.full(390, 2000.0)
        p = c[-1]
        o = np.r_[c[0], c[:-1]]
        frames.append(pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.02, "low": np.minimum(o, c) - 0.02, "close": c, "volume": v},
                                   index=idx))
    return pd.concat(frames)


def daily(m):
    g = m.groupby(m.index.tz_convert(ET).tz_localize(None).normalize())
    d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
                      "volume": g["volume"].sum()})
    d.index = d.index.tz_localize(ET)
    return d


MIN = {s: make(s) for s in ("AAA", "BBB")}


def fake_fetch(sym, tf, start, end, feed):
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    m = MIN[sym]
    if tf == "1Day":
        d = daily(m)
        return d[(d.index >= a) & (d.index < b)]
    if tf == "5Min":
        r = m.resample("5min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                                                  "volume": "sum"}).dropna()
        return r[(r.index >= a) & (r.index < b)]          # includes the forming bar: b5 must drop it
    return m[(m.index >= a) & (m.index < b)]


class FakeBroker:
    def __init__(self):
        self.now, self.orders, self.pos, self.n = None, {}, {}, 0

    def px(self, sym):
        m = MIN[sym]
        return float(m[m.index <= self.now]["close"].iloc[-1])

    def quote(self, sym):
        p = self.px(sym)
        return p, p

    def equity(self):
        return 100_000.0

    def positions(self):
        return {k: v for k, v in self.pos.items() if v}

    def shortable(self, sym):
        return True

    def _new(self, **kw):
        self.n += 1
        oid = f"o{self.n}"
        self.orders[oid] = {"id": oid, **kw}
        return self.orders[oid]

    def submit_market(self, sym, qty, cid=None, side="buy"):
        p = self.px(sym)
        self.pos[sym] = self.pos.get(sym, 0) + (qty if side == "buy" else -qty)
        return self._new(symbol=sym, qty=qty, side=side, type="market", status="filled", filled_avg_price=p, filled_qty=qty, cid=cid)

    def submit_order(self, sym, qty, side, type_, tif, cid, stop_price=None):
        return self._new(symbol=sym, qty=qty, side=side, type=type_, status="new", stop_price=stop_price, cid=cid)

    def cancel_order(self, oid):
        if self.orders[oid]["status"] != "filled":
            self.orders[oid]["status"] = "canceled"

    def order(self, oid):
        return self.orders[oid]

    def _req(self, *a, **k):
        raise RuntimeError("no calendar in the test")


def feats(sym="AAA"):
    m = MIN[sym]
    fl = to.daily_flags(daily(m[m.index < TODAY.tz_localize(ET)]))
    fl[TODAY.date()] = fl["_next"]
    return to.features(to.bars5(vd.minute_frame(m)), fl)


class FeatureTests(unittest.TestCase):
    def test_no_lookahead(self):
        f = feats()
        cut = f.index[f["d"] == TODAY.date()][10]
        m = MIN["AAA"]
        fl = to.daily_flags(daily(m[m.index < TODAY.tz_localize(ET)]))
        fl[TODAY.date()] = fl["_next"]
        g = to.features(to.bars5(vd.minute_frame(m)).iloc[:cut + 1], fl)
        cols = ["sma20", "vwap", "atr", "angle", "relvol", "rsi", "long", "short"]
        pd.testing.assert_frame_equal(f.loc[:cut, cols].reset_index(drop=True), g[cols].reset_index(drop=True))

    def test_signal_and_daily_gate(self):
        f = feats()
        t = f[f["d"] == TODAY.date()]
        self.assertTrue(t["long"].any())
        self.assertFalse(t["short"].any())
        self.assertFalse(f[f["d"] < TODAY.date()]["long"].any())          # flat volume: relvol never clears 1.2
        fl = to.daily_flags(daily(MIN["AAA"][MIN["AAA"].index < TODAY.tz_localize(ET)]))
        self.assertEqual(fl["_next"], (True, False))

    def test_rsi_no_losses_is_100(self):
        self.assertEqual(float(feats()[lambda x: x["d"] == TODAY.date()]["rsi"].iloc[5]), 100.0)


class WalkTests(unittest.TestCase):
    def day(self, c, sma=None, vw=None, k0=10):
        c = np.asarray(c, float)
        o = np.r_[c[0], c[:-1]]
        n = len(c)
        return pd.DataFrame({"k": np.arange(k0, k0 + n), "o": o, "h": np.maximum(o, c) + 0.01, "l": np.minimum(o, c) - 0.01, "c": c,
                             "sma20": sma if sma is not None else c - 5, "vwap": vw if vw is not None else c - 5})

    def test_hard_stop(self):
        d = self.day([100, 100, 99.5, 98.5, 98])
        ev = to.walk(d, 1, 1, 100.0, 1.0)
        self.assertEqual(ev[0][3], "hard_stop")
        self.assertAlmostEqual(ev[0][2], 99.0)

    def test_trail_exits_at_next_open(self):
        d = self.day([100, 100.5, 101.2, 102.0, 101.4, 101.0, 100.8])
        ev = to.walk(d, 1, 1, 100.0, 1.0)            # peak close 102 = +2R, trail at 101.5; close 101.4 -> out at next open
        self.assertEqual(ev[0][3], "virtual_trail")
        self.assertEqual(ev[0][0], 5)
        self.assertAlmostEqual(ev[0][2], 101.4)

    def test_three_candles(self):
        c = [100, 100.1, 100.05, 100.02, 100.01, 100.0, 100.0]
        d = self.day(c, sma=np.full(7, 100.5), vw=np.full(7, 100.5))
        ev = to.walk(d, 1, 1, 100.0, 1.0)
        self.assertEqual(ev[0][3], "3candle")
        self.assertEqual(ev[0][0], 4)                # closes 1, 2, 3 below -> out at bar 4's open

    def test_flatten(self):
        d = self.day([100, 100.1, 100.2, 100.3], k0=73)
        ev = to.walk(d, 1, 1, 100.0, 1.0)
        self.assertEqual(ev[0][3], "flatten_1545")
        self.assertEqual(ev[0][0], 2)

    def test_short_mirror(self):
        d = self.day([100, 100, 100.5, 101.5], sma=np.full(4, 90.0), vw=np.full(4, 90.0))
        ev = to.walk(d, 1, -1, 100.0, 1.0)
        self.assertEqual(ev[0][3], "hard_stop")
        self.assertAlmostEqual(ev[0][2], 101.0)


class BacktestTests(unittest.TestCase):
    def test_runs_and_writes(self):
        def load(s, tf, a, b):
            m = MIN[s]
            if tf == "1Day":
                return daily(m)[lambda x: x.index.date <= b]
            return m
        with tempfile.TemporaryDirectory() as d, mock.patch.object(universe, "STOCKS", ["AAA", "BBB"]):
            lines = to.backtest(load, (TODAY + pd.Timedelta(days=1)).date(), d, log=lambda *_: None, seeds=3, months=1)
            self.assertIn("| all |", lines[3])
            tr = pd.read_csv(f"{d}/trendopt-{(TODAY + pd.Timedelta(days=1)):%Y%m%d}/trades.csv")
            self.assertEqual(set(tr["symbol"]), {"AAA"})
            self.assertTrue((tr["side"] == 1).all())


class LiveTests(unittest.TestCase):
    def run_day(self, d, b, live, until=datetime(2026, 11, 3, 15, 50, 10, tzinfo=ET)):
        t = datetime(2026, 11, 3, 9, 40, 10, tzinfo=ET)
        while t <= until:
            b.now = pd.Timestamp(t) - pd.Timedelta(minutes=1)
            live.step(t)
            t += timedelta(minutes=1)
        return [json.loads(x) for x in open(f"{d}/journal.jsonl")]

    def test_full_day(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(universe, "STOCKS", ["AAA", "BBB"]):
            b = FakeBroker()
            live = to.TrendOptLive(b, state_dir=d, fetch=fake_fetch, sleep=lambda *_: None)
            j = self.run_day(d, b, live)
            opens = [x for x in j if x["kind"] == "open"]
            self.assertTrue(opens)
            self.assertTrue(all(x["symbol"] == "AAA" and x["side"] == 1 for x in opens))
            self.assertGreaterEqual(datetime.fromisoformat(opens[0]["t"]).time(), datetime(2026, 11, 3, 9, 45).time())
            self.assertTrue(all(o["cid"].startswith("to-") for o in b.orders.values()))
            self.assertEqual(b.positions(), {})
            self.assertEqual(live.state["open"], {})
            tr = [json.loads(x) for x in open(f"{d}/trades.jsonl")]
            self.assertTrue(all(x["final"] for x in tr))
            self.assertLessEqual(len(opens), to.CFG["max_new"])
            q = opens[0]["qty"]
            self.assertLessEqual(q * opens[0]["fill"], 0.25 * 100_000 + 1e-6)       # notional cap

    def test_filled_stop_is_booked_not_exited(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(universe, "STOCKS", ["AAA"]):
            b = FakeBroker()
            live = to.TrendOptLive(b, state_dir=d, fetch=fake_fetch, sleep=lambda *_: None)
            self.run_day(d, b, live, until=datetime(2026, 11, 3, 10, 30, 10, tzinfo=ET))
            self.assertIn("AAA", live.state["open"])
            pos = live.state["open"]["AAA"]
            b.orders[pos["stop_id"]].update(status="filled", filled_avg_price=pos["stop"])
            b.pos["AAA"] = 0
            n_orders = len(b.orders)
            t = datetime(2026, 11, 3, 10, 31, 10, tzinfo=ET)
            b.now = pd.Timestamp(t) - pd.Timedelta(minutes=1)
            live.step(t)
            self.assertNotIn("AAA", live.state["open"])
            self.assertEqual(len(b.orders), n_orders)                          # no market "exit" that would open a short
            tr = [json.loads(x) for x in open(f"{d}/trades.jsonl")]
            self.assertEqual(tr[-1]["reason"], "hard_stop")
            self.assertAlmostEqual(tr[-1]["r_trade"], -1.0, places=6)

    def test_forming_bar_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            live = to.TrendOptLive(FakeBroker(), state_dir=d, fetch=fake_fetch, sleep=lambda *_: None)
            b = live.b5("AAA", datetime(2026, 11, 3, 10, 2, 10, tzinfo=ET))
            last = b[b["d"] == TODAY.date()]["k"].max()
            self.assertEqual(last, 5)                                          # 09:55-10:00 closed; 10:00-10:05 still forming


if __name__ == "__main__":
    unittest.main()
