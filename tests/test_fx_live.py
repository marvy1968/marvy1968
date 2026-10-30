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

TREND_ON = False          # the trend-filter tests switch the filters on for their runner (TrendFilterTests)


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
    r.cfg = {**r.cfg, "trend_filters": {"long": True, "short": True, "reversal": True, "day_close": "16:00"}} if r.cfg.get("trend_filters") is None and TREND_ON else r.cfg
    r.daily_bias = lambda inst: (0, [])           # range day, no map levels unless a test says otherwise
    r.news_fetch = lambda url: []                 # no network: the news calendar falls back to the Fed + jobs-report dates
    r.cfg = {**r.cfg, "foreign_guard": live.FOREIGN_GUARD}   # the guard's own tests (it is OFF live: test_foreign_guard_off_live)
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
            r.cfg["take_profit_r"] = None                                             # (the old scale-out ladder; the +0.30R close-out would pre-empt it)
            r.cfg["loss_cut_r"] = None
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
            self.assertEqual(r.cfg["trade"], ["lien_h1", "retest_h1", "boxmr_h1"])   # trend experts log only (owner 2026-10-29)   # F0, the plain box and the other in-box experts only log
            self.assertEqual(r.cfg["risk_by_strategy"]["confirm_h1"], 0.005)
            r.try_open("lien_h1", "EUR_USD", 1, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], "2026-10-14T13:00:00+00:00")
            gid, g = next(iter(r.groups.items()))
            api.px["EUR_USD"] = (g["fill"] - 0.0023, g["fill"] - 0.0022)              # three quarters to the stop: no cut, OANDA's stop decides
            r.manage(gid, 58000.0, "")
            self.assertEqual(g["sold"], 0)

    def test_loss_cut_closes_a_trade_that_is_ten_percent_of_its_risk_against_us(self):
        with tempfile.TemporaryDirectory() as tmp:
            for side, sign in ((1, 1), (-1, -1)):
                api = FakeOanda()
                r = runner(tmp, api)
                r.cfg["loss_cut_r"], r.cfg["take_profit_r"] = 0.10, None                      # (the rule at 10%; the live default is 0.50, see CONFIG)
                r.try_open("lien_h1", "EUR_USD", side, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], f"2026-10-14T13:0{side + 1}:00+00:00")
                gid, g = next(iter(r.groups.items()))
                mid0 = g["mid0"]
                api.px["EUR_USD"] = (mid0 - sign * 0.0002 - 0.00005, mid0 - sign * 0.0002 + 0.00005)     # 2 pips = 0.067R against: still held
                r.manage(gid, 58000.0, "")
                self.assertIn(gid, r.groups)
                api.px["EUR_USD"] = (mid0 - sign * 0.00032 - 0.00005, mid0 - sign * 0.00032 + 0.00005)   # 3.2 pips = 0.107R against: closed
                r.manage(gid, 58000.0, "")
                self.assertNotIn(gid, r.groups)
                self.assertEqual(r.ledger()[-1]["reason"], "loss_cut")

    def test_loss_cut_off_and_spread_alone_never_triggers_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            r.try_open("lien_h1", "EUR_USD", 1, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], "2026-10-14T13:00:00+00:00")
            gid, g = next(iter(r.groups.items()))
            api.px["EUR_USD"] = (1.0996, 1.1006)                                       # a 10-pip spread around the same mid: no move, no cut
            api.px["EUR_USD"] = (g["mid0"] - 0.0005, g["mid0"] + 0.0005)
            r.manage(gid, 58000.0, "")
            self.assertIn(gid, r.groups)
            r.cfg["loss_cut_r"] = None
            api.px["EUR_USD"] = (g["mid0"] - 0.0020, g["mid0"] - 0.0019)               # 2/3 of the way to the stop: off means no cut
            r.manage(gid, 58000.0, "")
            self.assertIn(gid, r.groups)

    def test_take_profit_and_closeout_at_thirty_percent_of_the_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            for side, sign in ((1, 1), (-1, -1)):
                api = FakeOanda()
                r = runner(tmp, api)
                r.cfg["loss_cut_r"], r.cfg["take_profit_r"] = 0.10, 0.30                      # (the rule at 0.10 / 0.30; the live default is 0.50 / 1.25)
                r.try_open("lien_h1", "EUR_USD", side, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], f"2026-10-14T13:0{side + 1}:00+00:00")
                o = api.orders[-1]
                px = 1.1001 if side == 1 else 1.1000
                self.assertAlmostEqual(float(o["tp"]), px + sign * 0.30 * 0.0030, places=5)          # the OANDA take-profit sits at +0.30R
                gid, g = next(iter(r.groups.items()))
                mid0 = g["mid0"]
                api.px["EUR_USD"] = (mid0 + sign * 0.0008 - 0.00005, mid0 + sign * 0.0008 + 0.00005)   # 8 pips = 0.27R in profit: held
                r.manage(gid, 58000.0, "")
                self.assertIn(gid, r.groups)
                api.px["EUR_USD"] = (mid0 + sign * 0.00095 - 0.00005, mid0 + sign * 0.00095 + 0.00005)  # 9.5 pips = 0.32R: all out
                r.manage(gid, 58000.0, "")
                self.assertNotIn(gid, r.groups)
                self.assertEqual(r.ledger()[-1]["reason"], "take_profit_r")
                self.assertEqual(r.ledger()[-1]["side"], "long" if side == 1 else "short")

    def test_trade_lock_keeps_half_the_best_gain(self):
        with tempfile.TemporaryDirectory() as tmp:
            for side, sign in ((1, 1), (-1, -1)):
                api = FakeOanda()
                r = runner(tmp, api)
                r.cfg["trade_lock"] = {"trigger_r": 0.5, "keep": 0.5, "floor_r": 0.05}
                r.cfg["neg_exit_min"] = None
                r.try_open("lien_h1", "EUR_USD", side, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], f"2026-10-14T14:0{side + 1}:00+00:00")
                gid, g = next(iter(r.groups.items()))
                mid0 = g["mid0"]
                api.px["EUR_USD"] = (mid0 + sign * 0.0024 - 0.00005, mid0 + sign * 0.0024 + 0.00005)    # +0.8R: armed, held
                r.manage(gid, 58000.0, "")
                self.assertIn(gid, r.groups)
                api.px["EUR_USD"] = (mid0 + sign * 0.0013 - 0.00005, mid0 + sign * 0.0013 + 0.00005)    # +0.43R > 0.4R kept: held
                r.manage(gid, 58000.0, "")
                self.assertIn(gid, r.groups)
                api.px["EUR_USD"] = (mid0 + sign * 0.0011 - 0.00005, mid0 + sign * 0.0011 + 0.00005)    # +0.37R: gave back past half -> out
                r.manage(gid, 58000.0, "")
                self.assertNotIn(gid, r.groups)
                self.assertEqual(r.ledger()[-1]["reason"], "trade_lock")

    def test_trade_lock_live(self):
        from fxbot.live import CONFIG
        self.assertEqual(CONFIG["trade_lock"], {"trigger_r": 0.5, "keep": 0.5, "floor_r": 0.05})

    def test_live_default_is_the_backtested_best_pair(self):
        from fxbot.live import CONFIG
        self.assertEqual((CONFIG["loss_cut_r"], CONFIG["take_profit_r"]), (0.50, 1.25))

    def test_take_profit_off_restores_the_ladder_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            r.cfg["take_profit_r"] = None
            r.try_open("lien_h1", "EUR_USD", 1, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], "2026-10-14T13:00:00+00:00")
            self.assertGreater(float(api.orders[-1]["tp"]), 1.1001 + 0.0030)                 # the old zone-based target, well past +0.30R

    def test_against_the_trade_cuts_in_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            r.cfg["ladder"] = {**r.cfg["ladder"], "cut_fracs": [0.25, 0.5], "cut_depth_r": [0.5, 0.75]}     # optional setting still works
            r.cfg["loss_cut_r"] = None                                                  # (the 10% loss cut would close the trade first)
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
            r.cfg["take_profit_r"] = None                                             # (the old scale-out ladder; the +0.30R close-out would pre-empt it)
            r.cfg["loss_cut_r"] = None
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
        fz = RangeBox200(levels="zscore").frame(m)                                  # statistical envelope levels also fire in a sideways market
        self.assertTrue(fz["long"].iloc[400:].any() or fz["short"].iloc[400:].any())
        from alpacabot.indicators import atr as _atr
        j = int(np.flatnonzero(fz["long"].to_numpy())[-1]) if fz["long"].any() else None
        if j is not None:                                                          # a long: first take-profit line = envelope top - 0.25 ATR
            mu, sd = m["close"].shift(1).rolling(200).mean().iloc[j], m["close"].shift(1).rolling(200).std().iloc[j]
            self.assertAlmostEqual(float(fz["zone_px"].iloc[j]), float(mu + 2 * sd - 0.25 * _atr(m, 14).iloc[j]), places=9)
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

    def test_flatten_now_file_closes_the_runners_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            r.try_open("lien_h1", "EUR_USD", 1, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], "2026-10-14T13:00:00+00:00")
            self.assertTrue(r.groups)
            (Path(tmp) / "FLATTEN_NOW").write_text("owner\n")
            r.step()
            self.assertEqual(r.groups, {})
            self.assertFalse((Path(tmp) / "FLATTEN_NOW").exists())
            self.assertEqual(r.ledger()[-1]["reason"], "owner flatten")

    def test_negative_for_ten_minutes_is_closed(self):
        from datetime import timedelta
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            t = [datetime(2026, 10, 14, 14, 0, 30, tzinfo=timezone.utc)]
            r = live.FxPaper(api=api, state_dir=tmp, clock=lambda: t[0], sleep=lambda *_: None)
            r.daily_bias = lambda inst: (0, [])
            r.cfg["loss_cut_r"], r.cfg["take_profit_r"], r.cfg["neg_exit_min"] = None, None, 10
            r.try_open("lien_h1", "EUR_USD", 1, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], "2026-10-14T13:00:00+00:00")
            gid, g = next(iter(r.groups.items()))
            api.px["EUR_USD"] = (g["mid0"] - 0.00015, g["mid0"] - 0.00005)        # 1 pip under water
            r.manage(gid, 58000.0, "")
            self.assertIn(gid, r.groups)
            t[0] += timedelta(minutes=9)
            r.manage(gid, 58000.0, "")
            self.assertIn(gid, r.groups)                                          # 9 minutes: still held
            t[0] += timedelta(minutes=2)
            r.manage(gid, 58000.0, "")
            self.assertNotIn(gid, r.groups)                                       # 11 minutes negative: closed
            self.assertEqual(r.ledger()[-1]["reason"], "neg_exit")

    def test_back_above_entry_resets_the_negative_clock(self):
        from datetime import timedelta
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            t = [datetime(2026, 10, 14, 14, 0, 30, tzinfo=timezone.utc)]
            r = live.FxPaper(api=api, state_dir=tmp, clock=lambda: t[0], sleep=lambda *_: None)
            r.daily_bias = lambda inst: (0, [])
            r.cfg["loss_cut_r"], r.cfg["take_profit_r"], r.cfg["neg_exit_min"] = None, None, 10
            r.try_open("lien_h1", "EUR_USD", 1, 0.0030, 0.0060, frame(None, 1.1000), 58000.0, "", [], "2026-10-14T13:00:00+00:00")
            gid, g = next(iter(r.groups.items()))
            api.px["EUR_USD"] = (g["mid0"] - 0.00015, g["mid0"] - 0.00005)
            r.manage(gid, 58000.0, "")
            t[0] += timedelta(minutes=6)
            api.px["EUR_USD"] = (g["mid0"] + 0.00005, g["mid0"] + 0.00015)        # back in profit: the clock resets
            r.manage(gid, 58000.0, "")
            api.px["EUR_USD"] = (g["mid0"] - 0.00015, g["mid0"] - 0.00005)
            t[0] += timedelta(minutes=6)
            r.manage(gid, 58000.0, "")
            self.assertIn(gid, r.groups)

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


class ProfitLockTests(unittest.TestCase):
    def test_giving_back_35pct_of_the_days_gain_stops_new_entries_until_the_next_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = runner(tmp, FakeOanda())
            self.assertEqual(r.entries_allowed(50000.0), "")                       # day starts at 50,000
            self.assertEqual(r.entries_allowed(50150.0), "")                       # +0.30%: below the 0.4% start, no lock
            self.assertEqual(r.entries_allowed(50300.0), "")                       # +0.60% peak
            self.assertEqual(r.entries_allowed(50200.0), "")                       # +0.40%: gave back 33%, still open
            self.assertTrue(r.entries_allowed(50190.0).startswith("profit lock"))  # +0.38%: gave back 37%
            self.assertTrue(r.entries_allowed(50400.0).startswith("profit lock"))  # stays locked for the rest of the trading day
            r.save()
            r2 = runner(tmp, FakeOanda())                                           # a restart keeps the lock
            self.assertTrue(r2.entries_allowed(50400.0).startswith("profit lock"))
            nxt = runner(tmp, FakeOanda(), now=datetime(2026, 10, 15, 14, 0, 30, tzinfo=timezone.utc))
            self.assertEqual(nxt.entries_allowed(50400.0), "")                     # next trading day: open again

    def test_off_when_not_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = runner(tmp, FakeOanda())
            r.cfg = {**r.cfg, "profit_lock": None}
            for eq in (50000.0, 50300.0, 50000.0):
                self.assertEqual(r.entries_allowed(eq), "")

    def test_live_config(self):
        self.assertEqual(live.CONFIG["profit_lock"], {"start_pct": 0.004, "giveback_frac": 0.35})


class ForeignGuardTests(unittest.TestCase):
    def test_other_bots_trades_get_a_stop_and_a_loss_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            api.trades["9"] = {"id": "9", "instrument": "EUR_USD", "currentUnits": "-100000", "price": "1.1000", "unrealizedPL": "-20",
                               "clientExtensions": {"tag": "fxexp"}}
            api.trades["8"] = {"id": "8", "instrument": "EUR_USD", "currentUnits": "50000", "price": "1.1000", "unrealizedPL": "-200",
                               "clientExtensions": {"tag": "fxexp"}, "stopLossOrder": {"price": "1.0"}}
            api.trades["7"] = {"id": "7", "instrument": "EUR_USD", "currentUnits": "50000", "price": "1.1000", "unrealizedPL": "-30",
                               "clientExtensions": {"tag": "fxexp"}, "stopLossOrder": {"price": "1.0"}}
            r = runner(tmp, api)
            r.guard_foreign(api.open_trades())
            self.assertEqual(api.stops, [("9", "1.10232")])       # short: stop above entry, 0.4% of $58,000 = $232 / 100k units
            self.assertNotIn("8", api.trades)                      # -$200 <= -0.25% of NAV: closed
            self.assertIn("7", api.trades)                         # small loss with a stop: left alone
            r.cfg["foreign_guard"] = None
            api.stops.clear()
            r.guard_foreign(api.open_trades())
            self.assertEqual(api.stops, [])


class ForeignProfitLockTests(unittest.TestCase):
    def test_winning_fx_experts_trade_stop_moves_to_keep_half_its_best_gain_and_never_loosens(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            api.px["EUR_USD"] = (1.1030, 1.1031)
            api.other = [{"id": "5", "instrument": "EUR_USD", "currentUnits": "100000", "price": "1.1000", "unrealizedPL": "300",
                          "clientExtensions": {"tag": "fxexp"}, "stopLossOrder": {"price": "1.0900"}}]
            r = runner(tmp, api)
            r.guard_foreign(api.open_trades())
            self.assertEqual(api.stops, [("5", "1.10150")])          # +$300 >= 0.2% of $58,000: keep $150 = 15 pips above entry
            api.other[0]["stopLossOrder"] = {"price": "1.10150"}
            api.px["EUR_USD"] = (1.1020, 1.1021)
            api.other[0]["unrealizedPL"] = "200"                     # pulled back: best stays $300, stop not loosened or re-sent
            r.guard_foreign(api.open_trades())
            self.assertEqual(len(api.stops), 1)
            r.save()
            self.assertEqual(runner(tmp, api).foreign_best, {"5": 300.0})

    def test_small_or_losing_trades_and_short_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            api.px["EUR_USD"] = (1.0969, 1.0970)
            api.other = [{"id": "6", "instrument": "EUR_USD", "currentUnits": "-100000", "price": "1.1000", "unrealizedPL": "300",
                          "clientExtensions": {"tag": "fxexp"}, "stopLossOrder": {"price": "1.1100"}},
                         {"id": "7", "instrument": "EUR_USD", "currentUnits": "10000", "price": "1.1000", "unrealizedPL": "50",
                          "clientExtensions": {"tag": "fxexp"}, "stopLossOrder": {"price": "1.0900"}}]
            r = runner(tmp, api)
            r.guard_foreign(api.open_trades())
            self.assertEqual(api.stops, [("6", "1.09850")])          # short: keep $150 = 15 pips below entry; trade 7 (+$50) untouched


class PlaybookLiveTests(unittest.TestCase):
    """Owner 2026-10-25 Break & Retest Playbook live (retest_h1): a virtual buy-stop fills only at its trigger, the order carries the
    structure stop and the swing target at OANDA, the trade is exempt from the 0.5R cut / 1.25R take-profit / 10-minute exit, and a
    completed close back into the zone closes it."""

    def test_trigger_fill_own_exits_and_close_back_exit(self):
        with tempfile.TemporaryDirectory() as d:
            api = FakeOanda()
            r = runner(d, api)
            r.pending["retest_h1:EUR_USD"] = {"side": 1, "zu": 1.0990, "rlow": 1.0975, "trig": 1.1010, "stop": 1.0970, "tgt": 1.1130,
                                              "strategy": "retest_h1", "instrument": "EUR_USD", "bar": "x", "bars": 0}
            api.px["EUR_USD"] = (1.1000, 1.1001)
            r.check_pending(58000.0, "", [])
            self.assertEqual(api.orders, [])                                   # below the trigger: still waiting
            api.px["EUR_USD"] = (1.1011, 1.1012)
            r.check_pending(58000.0, "", [])
            self.assertEqual(len(api.orders), 1)
            o = api.orders[0]
            self.assertEqual((o["sl"], o["tp"]), ("1.09700", "1.11300"))      # the structure stop and the swing target at OANDA
            self.assertEqual(r.pending, {})
            gid = next(iter(r.groups))
            self.assertTrue(r.groups[gid]["own_exits"])
            api.px["EUR_USD"] = (1.0985, 1.0986)                               # 0.65R against, under water: no loss cut, no neg exit
            r.manage(gid, 58000.0, "")
            self.assertIn(gid, r.groups)
            bars = live._to_frame(candles(300, start=1.0980))                   # last completed close below the zone edge 1.0990
            bars.iloc[-1, bars.columns.get_loc("bc")] = 1.0980
            bars.iloc[-1, bars.columns.get_loc("ac")] = 1.0981
            from unittest import mock
            with mock.patch.object(live, "live_setup", lambda df, rules: None):
                r.scan_playbook("retest_h1", live.STRATS["retest_h1"], "EUR_USD", bars, live.mid(bars), "x")
            self.assertNotIn(gid, r.groups)                                    # out on the close back into the zone


class NewsPauseTests(unittest.TestCase):
    FOMC = datetime(2026, 10, 28, 17, 30, tzinfo=timezone.utc)          # 13:30 New York, 30 minutes before the Fed decision

    def test_paused_window_and_pairs(self):
        from fxbot.newsfilter import paused
        ev = [{"t": "2026-10-28T18:00:00+00:00", "ccy": "USD", "title": "FOMC decision"},
              {"t": "2026-10-29T12:00:00+00:00", "ccy": "EUR", "title": "ECB"}]
        at = pd.Timestamp
        self.assertTrue(paused("EUR_USD", at("2026-10-28T17:00:00Z"), ev).startswith("news pause: USD FOMC"))
        self.assertTrue(paused("USD_JPY", at("2026-10-28T19:00:00Z"), ev))
        self.assertEqual(paused("USD_JPY", at("2026-10-28T19:01:00Z"), ev), "")
        self.assertEqual(paused("USD_JPY", at("2026-10-28T16:59:00Z"), ev), "")
        self.assertEqual(paused("USD_JPY", at("2026-10-29T12:00:00Z"), ev), "")   # ECB: not a JPY or USD release
        self.assertTrue(paused("EUR_USD", at("2026-10-29T12:00:00Z"), ev))

    def test_parse_forexfactory(self):
        from fxbot.newsfilter import parse_ff
        rows = [{"title": "Federal Funds Rate", "country": "USD", "date": "2026-10-28T14:00:00-04:00", "impact": "High"},
                {"title": "Crude Oil Inventories", "country": "USD", "date": "2026-10-28T10:30:00-04:00", "impact": "Medium"}]
        ev = parse_ff(rows)
        self.assertEqual(len(ev), 1)
        self.assertEqual(pd.Timestamp(ev[0]["t"]), pd.Timestamp("2026-10-28T18:00:00Z"))

    def test_runner_flattens_and_blocks_in_the_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api, now=self.FOMC)
            r.groups["g1"] = {"id": "g1", "strategy": "lien_h1", "instrument": "EUR_USD", "side": 1, "trades": ["1"], "units": {"1": 1000},
                              "avg": 1.1, "opened": "2026-10-28T16:00", "tier": 0}
            api.trades["1"] = {"id": "1", "instrument": "EUR_USD", "currentUnits": "1000", "unrealizedPL": "0", "clientExtensions": {"id": "x", "tag": TAG}}
            r.pending["k"] = {"strategy": "retest_h1", "instrument": "USD_JPY", "side": 1, "trig": 150.0}
            news = r.news_pause(self.FOMC)
            self.assertIn("EUR_USD", news)                         # fallback calendar holds the Oct 28 Fed decision
            r.sync = lambda trades: None
            r.scan = lambda *a, **k: None
            r.manage = lambda *a, **k: None
            r.step()
            self.assertNotIn("g1", r.groups)
            self.assertNotIn("1", api.trades)
            self.assertEqual(r.pending, {})
            seen = []
            r.scan = lambda name, spec, inst, eq, why, trades: seen.append(why)
            r.step()
            self.assertTrue(seen and all(w.startswith("news pause") or w for w in seen))
            self.assertTrue(any(w.startswith("news pause") for w in seen))

    def test_outside_the_window_nothing_happens(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = runner(tmp, FakeOanda(), now=datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc))
            self.assertEqual(r.news_pause(r.clock()), {})

    def test_off_and_live_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = runner(tmp, FakeOanda(), now=self.FOMC)
            r.cfg = {**r.cfg, "news_pause": None}
            self.assertEqual(r.news_pause(self.FOMC), {})
        self.assertEqual(live.CONFIG["news_pause"], {"before_min": 60, "after_min": 60})


class TrendFilterTests(unittest.TestCase):
    def setUp(self):
        global TREND_ON
        TREND_ON = True

    def tearDown(self):
        global TREND_ON
        TREND_ON = False

    def _m(self, drift):
        from fxbot.data import mid, _to_frame
        return mid(_to_frame(candles(400, drift=drift)))

    def test_with_the_long_trend_is_a_swing_trade(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = runner(tmp, FakeOanda())
            r.daily_trend = lambda inst: 1
            self.assertEqual(r.trend_check("EUR_USD", 1, self._m(0.0)), ("swing", ""))
            mode, block = r.trend_check("EUR_USD", -1, self._m(0.0))
            self.assertEqual(mode, "")
            self.assertTrue(block.startswith("trend filter"))

    def test_hard_move_today_is_a_day_trade(self):
        import fxbot.strongtrend as stg
        with tempfile.TemporaryDirectory() as tmp:
            r = runner(tmp, FakeOanda())
            r.daily_trend = lambda inst: 0
            orig = stg.today_state
            try:
                stg.today_state = lambda m, d, fx: np.r_[np.zeros(len(m) - 1, int), -1]
                self.assertEqual(r.trend_check("EUR_USD", -1, self._m(0.0)), ("day", ""))
                self.assertTrue(r.trend_check("EUR_USD", 1, self._m(0.0))[1])
            finally:
                stg.today_state = orig

    def test_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = runner(tmp, FakeOanda())
            r.cfg = {**r.cfg, "trend_filters": None}
            self.assertEqual(r.trend_check("EUR_USD", 1, self._m(0.0)), ("", ""))

    def _group(self, r, api, side, trend=""):
        r.groups["g1"] = {"id": "g1", "strategy": "holygrail_h1", "instrument": "EUR_USD", "side": side, "trades": ["1"], "units": {"1": 1000},
                          "avg": 1.1, "opened": "2026-10-14T10:00", "tier": 0, "trend": trend}
        api.trades["1"] = {"id": "1", "instrument": "EUR_USD", "currentUnits": str(1000 * side), "unrealizedPL": "0",
                           "clientExtensions": {"id": "x", "tag": TAG}}

    def test_reversal_closes_a_trade_against_the_trend(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            self._group(r, api, 1)
            r.reversal_exit("holygrail_h1", "EUR_USD", self._m(+0.0015))      # still trending up: keep the long
            self.assertIn("g1", r.groups)
            r.reversal_exit("holygrail_h1", "EUR_USD", self._m(-0.0015))      # turned down: out
            self.assertNotIn("g1", r.groups)
            self.assertEqual(r.ledger()[-1]["reason"], "trend reversed")

    def test_day_trades_close_at_16_new_york(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            self._group(r, api, 1, "day")
            r.day_close(datetime(2026, 10, 14, 19, 59, tzinfo=timezone.utc))   # 15:59 New York
            self.assertIn("g1", r.groups)
            r.day_close(datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc))    # 16:00 New York
            self.assertNotIn("g1", r.groups)

    def test_swing_trades_stay_overnight(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            self._group(r, api, 1, "swing")
            r.day_close(datetime(2026, 10, 14, 20, 30, tzinfo=timezone.utc))
            self.assertIn("g1", r.groups)

    def test_live_config(self):
        self.assertEqual(live.CONFIG["trend_filters"], {"long": True, "short": True, "reversal": True, "day_close": "16:00"})
        for n in live.TREND_EXPERTS:
            self.assertNotIn(n, live.CONFIG["trade"])                         # log only (owner 2026-10-29)
            self.assertIn(n, live.STRATS)


class RangeExpertTests(unittest.TestCase):
    def _m(self, drift=0.0):
        from fxbot.data import mid, _to_frame
        return mid(_to_frame(candles(400, drift=drift)))

    def setUp(self):
        global TREND_ON
        TREND_ON = True

    def tearDown(self):
        global TREND_ON
        TREND_ON = False

    def test_range_expert_only_without_a_trend(self):
        import fxbot.strongtrend as stg
        with tempfile.TemporaryDirectory() as tmp:
            r = runner(tmp, FakeOanda())
            orig = stg.today_state
            try:
                stg.today_state = lambda m, d, fx: np.zeros(len(m), int)
                r.daily_trend = lambda inst: 0
                self.assertEqual(r.trend_check("EUR_USD", 1, self._m(), "boxmr_h1"), ("range", ""))
                self.assertEqual(r.trend_check("EUR_USD", -1, self._m(), "retest_h1"), ("range", ""))
                r.daily_trend = lambda inst: 1
                self.assertTrue(r.trend_check("EUR_USD", 1, self._m(), "lien_h1")[1].startswith("range expert: the pair is in a long trend"))
                self.assertEqual(r.trend_check("EUR_USD", 1, self._m(), "holygrail_h1"), ("swing", ""))      # trend experts unchanged
                r.daily_trend = lambda inst: 0
                stg.today_state = lambda m, d, fx: np.r_[np.zeros(len(m) - 1, int), -1]
                self.assertTrue(r.trend_check("EUR_USD", 1, self._m(), "boxmr_h1")[1].startswith("range expert: hard move"))
            finally:
                stg.today_state = orig

    def test_no_reversal_exit_for_range_experts(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            r = runner(tmp, api)
            r.groups["g1"] = {"id": "g1", "strategy": "boxmr_h1", "instrument": "EUR_USD", "side": 1, "trades": ["1"], "units": {"1": 1000},
                              "avg": 1.1, "opened": "2026-10-14T10:00", "tier": 0, "trend": "range"}
            api.trades["1"] = {"id": "1", "instrument": "EUR_USD", "currentUnits": "1000", "unrealizedPL": "0", "clientExtensions": {"id": "x", "tag": TAG}}
            r.reversal_exit("boxmr_h1", "EUR_USD", self._m(-0.0015))
            self.assertIn("g1", r.groups)

    def test_lists(self):
        self.assertEqual(live.RANGE_EXPERTS, ["lien_h1", "retest_h1", "boxmr_h1"])
        self.assertIn("boxmr_h1", live.CONFIG["trade"])


class ForeignGuardOffTests(unittest.TestCase):
    def test_foreign_guard_off_live(self):
        self.assertIsNone(live.CONFIG["foreign_guard"])                     # owner 2026-10-28: "turn off the guard on fx-experts"
        self.assertEqual(live.FOREIGN_GUARD["cut_pct"], 0.0025)

    def test_off_touches_no_foreign_trade(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeOanda()
            api.trades["9"] = {"id": "9", "instrument": "EUR_USD", "currentUnits": "-100000", "price": "1.1000", "unrealizedPL": "-500",
                               "clientExtensions": {"tag": "fxexp"}}
            r = runner(tmp, api)
            r.cfg = {**r.cfg, "foreign_guard": live.CONFIG["foreign_guard"]}
            r.guard_foreign(api.open_trades())
            self.assertIn("9", api.trades)
            self.assertEqual(api.stops, [])
