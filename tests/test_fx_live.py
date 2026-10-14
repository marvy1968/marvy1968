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
            self.assertAlmostEqual(risk / 58000.0, 0.0025, places=4)               # tier 0 = 0.25% of the book
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

    def test_ladder_in_then_out_with_breakeven(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            r.try_open("lien_h1", "EUR_USD", 1, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], "2026-10-14T13:00:00+00:00")
            gid, g = next(iter(r.groups.items()))
            a = g["atr"]
            api.px["EUR_USD"] = (1.1000 - 0.6 * a - 0.0001, 1.1000 - 0.6 * a)       # 0.6 ATR deeper: next tier only (one step per check)
            r.manage(gid, 58000.0, "")
            self.assertEqual(g["tier"], 1)
            self.assertEqual(len(api.orders), 2)
            self.assertEqual(api.orders[1]["sl"], api.orders[0]["sl"])               # same stop and target at OANDA
            self.assertEqual(api.orders[1]["tp"], api.orders[0]["tp"])
            total = sum(g["units"].values())
            risk = sum(abs(int(o["units"])) * (r.groups[gid]["avg"] - float(o["sl"])) for o in api.orders)
            self.assertAlmostEqual(risk / 58000.0, 0.005, places=3)                # total risk = 0.5% at tier 1
            z = g["zone_tp"]
            api.px["EUR_USD"] = (z, z + 0.0001)                                       # at the take-profit zone: 25% out, stop to BE
            r.manage(gid, 58000.0, "")
            self.assertEqual(g["sold"], int(round(total * 0.25)))
            self.assertTrue(g["be"])
            self.assertEqual({t for t, _ in api.stops}, set(g["trades"]))
            rows = [json.loads(l) for l in (Path(tmp) / "trades.jsonl").read_text().splitlines()]
            self.assertTrue(all(x["partial"] for x in rows))
            api.px["EUR_USD"] = (z + 1.1 * a, z + 1.1 * a + 0.0001)                   # 1 ATR beyond: everything out
            r.manage(gid, 58000.0, "")
            self.assertEqual(r.groups, {})
            self.assertEqual(api.trades, {})

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
