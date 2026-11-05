import json
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from alpacabot import vwapday as vd
from alpacabot import vwaplive as vl

ET = ZoneInfo("America/New_York")
TODAY = pd.Timestamp("2026-11-03")
POOL = {"XLK": ["AAA", "BBB", "CCC"], "XLF": ["EEE", "FFF", "GGG"]}


def make_minutes(sym):
    rng = np.random.default_rng(abs(hash(sym)) % 1000)
    frames = []
    days = pd.bdate_range(end=TODAY, periods=25)
    for d in days:
        idx = pd.date_range(d + pd.Timedelta(hours=9, minutes=30), periods=390, freq="1min", tz=ET)
        if sym == "AAA" and d == TODAY:
            c = np.r_[100 + 0.5 * np.sin(np.linspace(0, 6, 30)) * 0.5 + 0.25, np.linspace(100.5, 103, 60), np.full(300, 103.0)]
            v = np.r_[np.full(30, 9000.0), np.full(360, 2000.0)]
        elif sym == "SPY":
            c = np.linspace(500, 503, 390)
            v = np.full(390, 5e4)
        else:
            c = 100 + np.cumsum(rng.normal(0, 0.02, 390))
            v = np.full(390, 2000.0)
        o = np.r_[c[0], c[:-1]]
        frames.append(pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.01, "low": np.minimum(o, c) - 0.01, "close": c, "volume": v}, index=idx))
    return pd.concat(frames)


MIN = {s: make_minutes(s) for s in ["SPY"] + [x for v in POOL.values() for x in v]}


def fake_fetch(sym, tf, start, end, feed):
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    if tf == "1Day":
        d = pd.bdate_range(end=TODAY - pd.Timedelta(days=1), periods=120)
        c = np.full(len(d), 100.0)
        df = pd.DataFrame({"open": c, "high": c + 1.0, "low": c - 1.0, "close": c, "volume": 1e6}, index=d.tz_localize(ET))
        return df[(df.index >= a) & (df.index < b)]
    m = MIN[sym]
    return m[(m.index >= a) & (m.index + pd.Timedelta(minutes=1) <= b)]


class FakeBroker:
    def __init__(self):
        self.now, self.orders, self.pos, self.n = None, {}, {}, 0

    def px(self, sym):
        m = MIN[sym]
        m = m[m.index <= self.now]
        return float(m["close"].iloc[-1])

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
        return self._new(symbol=sym, qty=qty, side=side, type="market", status="filled", filled_avg_price=p, filled_qty=qty)

    def submit_order(self, sym, qty, side, type_, tif, cid, stop_price=None):
        return self._new(symbol=sym, qty=qty, side=side, type=type_, status="new", stop_price=stop_price)

    def cancel_order(self, oid):
        self.orders[oid]["status"] = "canceled"

    def order(self, oid):
        return self.orders[oid]

    def _req(self, *a, **k):
        raise RuntimeError("no calendar in the test")


class VwapLiveTests(unittest.TestCase):
    def test_full_day_orb_long(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(vd, "SECTOR", {s: e for e, ss in POOL.items() for s in ss}), \
                mock.patch.object(vd, "ETFS", ["XLK", "XLF"]), mock.patch.object(vd, "MEMBER_FROM", {}):
            b = FakeBroker()
            cfg = {**vl.VWAP_LIVE, "strategies": ["orb"], "start_date": "", "log_only": False}
            live = vl.VwapLive(b, state_dir=d, cfg=cfg, fetch=fake_fetch, sleep=lambda *_: None)
            t = datetime(2026, 11, 3, 9, 50, 10, tzinfo=ET)
            while t <= datetime(2026, 11, 3, 15, 52, 10, tzinfo=ET):
                b.now = pd.Timestamp(t) - pd.Timedelta(minutes=1)
                live.step(t)
                t += timedelta(minutes=1)
            j = [json.loads(x) for x in open(f"{d}/journal.jsonl")]
            self.assertIn("AAA", next(x for x in j if x["kind"] == "inplay")["names"])
            opens = [x for x in j if x["kind"] == "open"]
            self.assertEqual(len(opens), 1)
            self.assertEqual(opens[0]["symbol"], "AAA")
            self.assertLessEqual(opens[0]["qty"] * opens[0]["fill"], 25_000 + 1e-6)
            risk = opens[0]["qty"] * abs(opens[0]["fill"] - opens[0]["stop"])
            self.assertLessEqual(risk, 0.0005 * 100_000 + 1e-6)
            trades = [json.loads(x) for x in open(f"{d}/trades.jsonl")]
            self.assertTrue(trades and trades[-1]["final"])
            self.assertEqual(b.positions(), {})
            self.assertEqual(live.state["open"], {})
            self.assertIn("vwap", [x["reason"] for x in trades] + ["vwap"])

    def test_paused_takes_no_trades(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(vd, "SECTOR", {s: e for e, ss in POOL.items() for s in ss}), \
                mock.patch.object(vd, "ETFS", ["XLK", "XLF"]), mock.patch.object(vd, "MEMBER_FROM", {}):
            b = FakeBroker()
            live = vl.VwapLive(b, state_dir=d, cfg={**vl.VWAP_LIVE, "paused": True}, fetch=fake_fetch, sleep=lambda *_: None)
            t = datetime(2026, 11, 3, 9, 50, 10, tzinfo=ET)
            while t <= datetime(2026, 11, 3, 11, 0, 10, tzinfo=ET):
                b.now = pd.Timestamp(t) - pd.Timedelta(minutes=1)
                live.step(t)
                t += timedelta(minutes=1)
            self.assertEqual(b.positions(), {})

    def test_start_date_gate(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(vd, "SECTOR", {s: e for e, ss in POOL.items() for s in ss}), \
                mock.patch.object(vd, "ETFS", ["XLK", "XLF"]), mock.patch.object(vd, "MEMBER_FROM", {}):
            b = FakeBroker()
            live = vl.VwapLive(b, state_dir=d, cfg={**vl.VWAP_LIVE, "start_date": "2026-11-04"}, fetch=fake_fetch, sleep=lambda *_: None)
            t = datetime(2026, 11, 3, 9, 50, 10, tzinfo=ET)
            while t <= datetime(2026, 11, 3, 11, 0, 10, tzinfo=ET):
                b.now = pd.Timestamp(t) - pd.Timedelta(minutes=1)
                live.step(t)
                t += timedelta(minutes=1)
            self.assertEqual(b.positions(), {})

    def test_log_only_places_no_orders(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(vd, "SECTOR", {s: e for e, ss in POOL.items() for s in ss}), \
                mock.patch.object(vd, "ETFS", ["XLK", "XLF"]), mock.patch.object(vd, "MEMBER_FROM", {}):
            b = FakeBroker()
            cfg = {**vl.VWAP_LIVE, "strategies": ["orb"], "start_date": "", "log_only": True}
            live = vl.VwapLive(b, state_dir=d, cfg=cfg, fetch=fake_fetch, sleep=lambda *_: None)
            t = datetime(2026, 11, 3, 9, 50, 10, tzinfo=ET)
            while t <= datetime(2026, 11, 3, 15, 52, 10, tzinfo=ET):
                b.now = pd.Timestamp(t) - pd.Timedelta(minutes=1)
                live.step(t)
                t += timedelta(minutes=1)
            with open(f"{d}/journal.jsonl") as fh:
                j = [json.loads(x) for x in fh]
            self.assertEqual(b.orders, {})
            self.assertFalse(any(x["kind"] == "open" for x in j))
            self.assertTrue(any(x["kind"] == "signal" and x.get("skip_reason") == "log_only" for x in j))
        self.assertTrue(vl.VWAP_LIVE["log_only"])                     # owner 2026-11-04: the live sleeve is log only

    def test_spy_regime_gate(self):
        def fetch(sym, tf, a, b, feed):
            d = pd.bdate_range(end=TODAY - pd.Timedelta(days=1), periods=60).tz_localize(ET)
            c = np.linspace(480, 400, 60)                       # SPY falling under a falling SMA20 -> shorts only
            return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1e6}, index=d)
        with tempfile.TemporaryDirectory() as d:
            b = FakeBroker()
            live = vl.VwapLive(b, state_dir=d, cfg={**vl.VWAP_LIVE, "log_only": False}, fetch=fetch, sleep=lambda *_: None)
            now = datetime(2026, 11, 3, 10, 30, 10, tzinfo=ET)
            self.assertFalse(live.spy_side_ok(1, now))
            self.assertTrue(live.spy_side_ok(-1, now))
            live.state = {**live.state, "open": {}, "traded": [], "n_new": 0}
            live.enter("AAA", "reclaim", 1, {"entry": 100.0, "stop": 99.0}, now, 60)
            self.assertEqual(b.orders, {})
            with open(f"{d}/journal.jsonl") as fh:
                j = [json.loads(x) for x in fh]
            self.assertEqual(j[-1]["skip_reason"], "spy_regime")


if __name__ == "__main__":
    unittest.main()
