"""Our OANDA practice runner (fxbot.live) against a fake OANDA: broker-side stop + take-profit on every fill, tags, direction first,
no chasing, the scaling ladder (in and out), break-even after the first scale-out, broker-closed trades in the ledger, own-book safety."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from fxbot import live
from fxbot.oanda import TAG


def candles(n=300, start=1.1000, gran_h=1, end=datetime(2026, 10, 14, 14, tzinfo=timezone.utc), drift=0.0, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=pd.Timestamp(end) - pd.Timedelta(hours=gran_h), periods=n, freq=f"{gran_h}h")
    c = start + np.cumsum(rng.normal(drift, 0.0008, n))
    out = []
    for t, x in zip(idx, c):
        b = {"o": f"{x:.5f}", "h": f"{x + 0.0006:.5f}", "l": f"{x - 0.0006:.5f}", "c": f"{x:.5f}"}
        a = {k: f"{float(v) + 0.0001:.5f}" for k, v in b.items()}
        out.append({"time": t.isoformat(), "bid": b, "ask": a, "volume": 100, "complete": True})
    return out


class FakeOanda:
    def __init__(self):
        self.px = {"EUR_USD": (1.1000, 1.1001)}
        self.trades, self.closed, self.orders, self.stops = {}, {}, [], []
        self.n = 0
        self.other = []                       # trades of another bot

    def summary(self):
        return {"NAV": "58000"}

    def candles(self, inst, gran, count):
        return candles(300 if gran != "D" else 250, gran_h=24 if gran == "D" else 1)

    def price(self, inst):
        return self.px.get(inst, (1.0, 1.0001))

    def open_trades(self):
        return list(self.trades.values()) + self.other

    def trade(self, tid):
        return self.closed[tid]

    def market(self, inst, units, sl, tp, cid):
        self.n += 1
        tid = str(self.n)
        b, a = self.price(inst)
        px = a if units > 0 else b
        self.orders.append({"inst": inst, "units": units, "sl": sl, "tp": tp, "cid": cid})
        self.trades[tid] = {"id": tid, "instrument": inst, "currentUnits": str(units), "unrealizedPL": "0",
                            "clientExtensions": {"id": cid, "tag": TAG}}
        return {"trade_id": tid, "price": px, "units": units}

    def close(self, tid, units=None):
        t = self.trades[tid]
        cur = int(t["currentUnits"])
        n = abs(cur) if units is None else units
        left = abs(cur) - n
        if left:
            t["currentUnits"] = str(left * (1 if cur > 0 else -1))
        else:
            self.trades.pop(tid)
        b, a = self.price(t["instrument"])
        return {"price": b if cur > 0 else a, "pl": 1.0 * n / 1000}

    def set_stop(self, tid, price):
        self.stops.append((tid, price))
        return {}


def runner(tmp, api, now=datetime(2026, 10, 14, 14, 0, 30, tzinfo=timezone.utc)):
    r = live.FxPaper(api=api, state_dir=tmp, clock=lambda: now, sleep=lambda *_: None)
    r.daily_bias = lambda inst: (0, [])           # range day, no map levels unless a test says otherwise
    return r


def frame(m, close):
    """Minimal (m) for try_open: 30 mid bars ending at `close`."""
    idx = pd.date_range(end="2026-10-14 13:00", periods=30, freq="1h", tz="UTC")
    c = np.linspace(close + 0.0040, close, 30)
    return pd.DataFrame({"open": c, "high": c + 0.0005, "low": c - 0.0005, "close": c}, index=idx)


class FxLiveTests(unittest.TestCase):
    def test_market_hours_and_rollover(self):
        self.assertFalse(live.market_open(datetime(2026, 10, 17, 15, tzinfo=timezone.utc)))      # Saturday
        self.assertFalse(live.market_open(datetime(2026, 10, 16, 21, 30, tzinfo=timezone.utc)))  # Friday 17:30 NY
        self.assertTrue(live.market_open(datetime(2026, 10, 18, 21, 30, tzinfo=timezone.utc)))   # Sunday 17:30 NY
        self.assertTrue(live.rollover(datetime(2026, 10, 14, 21, 0, tzinfo=timezone.utc)))       # 17:00 NY

    def test_open_places_stop_and_target_at_broker_and_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            res = r.try_open("lien_h1", "EUR_USD", 1, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], "2026-10-14T13:00:00+00:00")
            self.assertEqual(res, "opened")
            o = api.orders[0]
            self.assertGreater(o["units"], 0)
            self.assertEqual(o["sl"], "1.09710")                                   # fill 1.1001 - 30 pips, at OANDA
            self.assertGreater(float(o["tp"]), 1.1001)                              # take-profit at OANDA too
            g = next(iter(r.groups.values()))
            self.assertEqual(g["tier"], 0)
            risk = o["units"] * 0.0030
            self.assertAlmostEqual(risk / 58000.0, 0.01, places=4)                 # one entry risking 1% of the book
            self.assertEqual(api.trades["1"]["clientExtensions"]["tag"], TAG)

    def test_direction_chase_other_bot_and_kill(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            r.daily_bias = lambda inst: (-1, [])
            bar = "2026-10-14T13:00:00+00:00"
            self.assertTrue(r.try_open("f0_h4", "EUR_USD", 1, 0.003, float("nan"), frame(None, 1.1), 58000, "", [], bar).startswith("against"))
            r.daily_bias = lambda inst: (0, [])
            self.assertTrue(r.try_open("f0_h4", "EUR_USD", 1, 0.003, float("nan"), frame(None, 1.0950), 58000, "", [], bar).startswith("chase"))
            other = [{"id": "x", "instrument": "EUR_USD", "clientExtensions": {}}]
            self.assertEqual(r.try_open("f0_h4", "EUR_USD", 1, 0.003, float("nan"), frame(None, 1.1), 58000, "", other, bar),
                             "another bot holds this pair")
            (Path(tmp) / "KILL_SWITCH").write_text("x")
            self.assertEqual(r.entries_allowed(58000), "kill switch")
            self.assertEqual(api.orders, [])

    def test_take_profit_steps_lock_stop_and_ride(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            r.try_open("lien_h1", "EUR_USD", 1, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], "2026-10-14T13:00:00+00:00")
            gid, g = next(iter(r.groups.items()))
            a, total, z = g["atr"], g["peak_units"], g["zone_tp"]
            self.assertAlmostEqual(float(api.orders[0]["tp"]), z + 3 * a, places=4)   # OANDA TP far out: the rest can ride
            api.px["EUR_USD"] = (z, z + 0.0001)                                       # at the zone: 25% out, stop to break-even
            r.manage(gid, 58000.0, "")
            self.assertEqual(g["sold"], int(round(total * 0.25)))
            self.assertEqual(api.stops[-1][1], f"{g['avg']:.5f}")
            api.px["EUR_USD"] = (z + 0.3 * a, z + 0.3 * a + 0.0001)                   # a little beyond: 50% out, stop locked at the zone
            r.manage(gid, 58000.0, "")
            self.assertEqual(g["sold"], int(round(total * 0.5)))
            self.assertEqual(api.stops[-1][1], f"{z:.5f}")
            api.px["EUR_USD"] = (z + 2 * a, z + 2 * a + 0.0001)                       # far beyond: the last half rides
            r.manage(gid, 58000.0, "")
            self.assertIn(gid, r.groups)
            self.assertEqual(len(api.orders), 1)                                       # never adds
            rows = [json.loads(l) for l in (Path(tmp) / "trades.jsonl").read_text().splitlines()]
            self.assertEqual([x["reason"] for x in rows], ["zone25", "zone50"])

    def test_one_stop_by_default_and_paused_strategies_only_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            self.assertEqual(r.cfg["ladder"]["cut_fracs"], [])
            self.assertEqual(r.cfg["trade"], ["lien_h1"])
            r.try_open("lien_h1", "EUR_USD", 1, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], "2026-10-14T13:00:00+00:00")
            gid, g = next(iter(r.groups.items()))
            api.px["EUR_USD"] = (g["fill"] - 0.0023, g["fill"] - 0.0022)              # three quarters to the stop: no cut, OANDA's stop decides
            r.manage(gid, 58000.0, "")
            self.assertEqual(g["sold"], 0)

    def test_against_the_trade_cuts_in_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            r.cfg["ladder"] = {**r.cfg["ladder"], "cut_fracs": [0.25, 0.5], "cut_depth_r": [0.5, 0.75]}     # optional setting still works
            r.try_open("lien_h1", "EUR_USD", 1, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], "2026-10-14T13:00:00+00:00")
            gid, g = next(iter(r.groups.items()))
            fill, total = g["fill"], g["peak_units"]
            api.px["EUR_USD"] = (fill - 0.0016, fill - 0.0015)                        # just past half way to the 30-pip stop
            r.manage(gid, 58000.0, "")
            self.assertEqual(g["sold"], int(round(total * 0.25)))
            api.px["EUR_USD"] = (fill - 0.0023, fill - 0.0022)                        # three quarters
            r.manage(gid, 58000.0, "")
            self.assertEqual(g["sold"], int(round(total * 0.5)))
            self.assertIn(gid, r.groups)                                               # OANDA's stop takes the rest

    def test_rider_trails_at_oanda(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            r.try_open("lien_h1", "EUR_USD", 1, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], "2026-10-14T13:00:00+00:00")
            gid, g = next(iter(r.groups.items()))
            a, z = g["atr"], g["zone_tp"]
            for px in (z, z + 0.3 * a, z + 2 * a):
                api.px["EUR_USD"] = (px, px + 0.0001)
                r.manage(gid, 58000.0, "")
            self.assertAlmostEqual(g["stop_px"], z + 1 * a, places=6)                  # 1 ATR behind the best price, at OANDA
            self.assertEqual(api.stops[-1][1], f"{z + a:.5f}")

    def test_range_box_strategy(self):
        from fxbot.strategies import RangeBox200
        n = 800
        idx = pd.date_range("2026-01-05", periods=n, freq="1h", tz="UTC")
        c = 1.1000 + 0.0040 * np.sin(np.arange(n) * 2 * np.pi / 50)             # sideways: up and down in a repeating pattern
        m = pd.DataFrame({"open": c, "high": c + 0.0004, "low": c - 0.0004, "close": c}, index=idx)
        f = RangeBox200().frame(m)
        self.assertTrue(f["long"].iloc[400:].any() and f["short"].iloc[400:].any())
        i = int(np.flatnonzero(f["long"].to_numpy())[-1])
        self.assertLess(f["zone_px"].iloc[i], m["high"].iloc[i - 100:i].max())    # first TP line just inside the box top
        trend = pd.DataFrame({"open": 1.1 + np.arange(n) * 0.0005}, index=idx)
        trend["high"], trend["low"], trend["close"] = trend["open"] + 0.0004, trend["open"] - 0.0004, trend["open"]
        f2 = RangeBox200().frame(trend)
        self.assertFalse(f2["long"].any() or f2["short"].any())                     # trending: MAs apart, no box trades

    def test_broker_closed_trade_goes_to_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            r.try_open("lien_h1", "EUR_USD", -1, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], "2026-10-14T13:00:00+00:00")
            gid, g = next(iter(r.groups.items()))
            t = api.trades.pop("1")
            api.closed["1"] = {**t, "state": "CLOSED", "realizedPL": "-170.5", "financing": "-0.5", "averageClosePrice": f"{g['stop_px']:.5f}"}
            r.sync(api.open_trades())
            self.assertEqual(r.groups, {})
            row = json.loads((Path(tmp) / "trades.jsonl").read_text())
            self.assertEqual((row["reason"], row["pl"]), ("broker_sl", -171.0))
            self.assertAlmostEqual(r.equity([]), 58000.0 - 171.0)

    def test_step_runs_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            r.step()
            self.assertTrue((Path(tmp) / "status.txt").exists())
            self.assertTrue((Path(tmp) / "groups.json").exists())
            r.step()                                                                   # second pass: no re-download before the next bar
            self.assertIn("MARV FX PRACTICE RUNNER", (Path(tmp) / "status.txt").read_text())


if __name__ == "__main__":
    unittest.main()
