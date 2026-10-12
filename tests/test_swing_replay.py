"""End-to-end proof that the daily swing runner trades what the Sector Top-Down backtest measures.

A simulated broker fills opening-auction (OPG) orders at the day's open and GTC stops the way the backtest does (a gap through the stop fills
at the open, a touch fills at the stop). The real SwingRunner runs scan (after each close) and open (before each open) day by day; the same
data goes through engine.simulate_swing + size_swing. Every closed trade must match: symbol, entry day, exit day, exit reason, prices.
"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from alpacabot.engine import Costs, Portfolio
from alpacabot.research import run_swing
from alpacabot.strategies import SectorTopDown
from alpacabot.swing_paper import SwingRunner

ET = ZoneInfo("America/New_York")
DAYS = pd.bdate_range("2025-01-02", periods=230, tz="America/New_York")


def series(seed, drift, vol=0.014, start=100.0):
    rng = np.random.default_rng(seed)
    c = start * np.exp(np.cumsum(drift + rng.normal(0, vol, len(DAYS))))
    o = np.r_[start, c[:-1]] * np.exp(rng.normal(0, 0.006, len(DAYS)))            # real overnight gaps
    hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.006, len(DAYS))))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.006, len(DAYS))))
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": 1e6}, index=DAYS)


class SimBroker:
    def __init__(self, data):
        self.data, self.k, self.orders, self.n = data, 0, {}, 0

    def equity(self):
        return 1_000_000.0

    def is_session(self, day):
        return True

    def submit_order(self, symbol, qty, side, type_, tif, coid, stop_price=None):
        self.n += 1
        o = {"id": f"o{self.n}", "symbol": symbol, "qty": qty, "side": side, "type": type_, "tif": tif, "stop_price": stop_price,
             "status": "new", "placed": self.k, "client_order_id": coid}
        if tif == "day" and type_ == "market":                                         # immediate sale after a gap below the stop
            o.update(status="filled", filled_qty=qty, filled_avg_price=float(self.data[symbol]["open"].iloc[self.k]))
        self.orders[o["id"]] = o
        return dict(o)

    def cancel_order(self, oid):
        if self.orders[oid]["status"] == "new":
            self.orders[oid]["status"] = "canceled"

    def order(self, oid):
        o = self.orders[oid]
        if o["status"] == "new" and o["tif"] == "opg":                                  # the opening auction has happened
            o.update(status="filled", filled_qty=o["qty"], filled_avg_price=float(self.data[o["symbol"]]["open"].iloc[self.k]))
        return dict(o)

    def run_stops(self):
        """Intraday: GTC stops placed BEFORE today gap-fill at the open; any active stop fills at its level when the low touches it."""
        for o in self.orders.values():
            if o["status"] != "new" or o["type"] != "stop":
                continue
            bar = self.data[o["symbol"]].iloc[self.k]
            if o["placed"] < self.k and bar["open"] <= o["stop_price"]:
                o.update(status="filled", filled_qty=o["qty"], filled_avg_price=float(bar["open"]))
            elif bar["low"] <= o["stop_price"]:
                o.update(status="filled", filled_qty=o["qty"], filled_avg_price=float(o["stop_price"]))


class SwingReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spy = series(1, 0.0003, 0.009, 400.0)
        cls.data = getattr(cls, "data_override", None) or {"AAA": series(2, 0.0020), "BBB": series(3, 0.0012), "CCC": series(4, -0.0002)}
        cls.tmp = tempfile.TemporaryDirectory()
        broker = SimBroker(cls.data)
        allbars = {**cls.data, "SPY": cls.spy}

        def fetch(sym, tf, start, end, feed):
            d = allbars[sym]
            return d[d.index.date <= cls.today]

        r = SwingRunner(broker, state_dir=cls.tmp.name, universe=list(cls.data), fetch=fetch, sleep=lambda *_: None, base_equity=1_000_000.0)
        cls.first = 0                       # both start flat on day 0 (indicators are not ready yet, so nothing can trade early)
        for k in range(cls.first, len(DAYS)):
            broker.k = k
            day = DAYS[k].date()
            cls.today = day
            r.open(datetime(day.year, day.month, day.day, 9, 20, tzinfo=ET), wait_until=datetime(day.year, day.month, day.day, 9, 20, tzinfo=ET))
            broker.run_stops()
            r.scan(datetime(day.year, day.month, day.day, 16, 20, tzinfo=ET))
        cls.runner = r
        p = Path(cls.tmp.name) / "trades.jsonl"
        cls.ledger = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        # the backtest over the same days, same rules, no costs (the simulated fills have none)
        res = run_swing(SectorTopDown(cls.spy["close"]), cls.data, Costs(0, 0, 0),
                        Portfolio(equity=1_000_000.0, risk_pct=0.0035, max_positions=3, max_pos_frac=0.333), DAYS[cls.first + 1].date(), DAYS[-1].date())
        cls.bt = res["fills"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_0_exercised(self):
        self.assertGreaterEqual(len(self.ledger), 8)
        self.assertTrue({t["reason"] for t in self.ledger} >= {"signal", "stop"})

    def test_1_every_closed_trade_matches_the_backtest(self):
        bt = sorted((r.symbol, r.entry.date().isoformat(), r.exit.date().isoformat(), r.reason, round(r.entry_px, 4), round(r.exit_px, 4))
                    for r in self.bt.itertuples() if r.reason in ("signal", "stop"))
        lv = sorted((t["symbol"], t["opened"], t["date"], t["reason"], round(t["entry"], 4), round(t["exit"], 4))
                    for t in self.ledger if t["reason"] in ("signal", "stop"))
        self.maxDiff = None
        # the live stop is sent at 2 decimals; compare prices to the cent
        norm = lambda rows: [(a, b, c, d, round(e, 2), round(f, 2)) for a, b, c, d, e, f in rows]      # noqa: E731
        self.assertEqual(norm(lv), norm(bt))
        print(f"\nswing parity: {len(lv)} closed trades identical; reasons {sorted({x[3] for x in lv})}")

    def test_2_never_more_than_three_positions_and_stops_rest_at_the_broker(self):
        self.assertLessEqual(len(self.runner.state["positions"]), 3)
        for sym, p in self.runner.state["positions"].items():
            o = self.runner.b.orders[p["stop_order_id"]]
            self.assertEqual((o["type"], o["tif"], o["status"], o["symbol"]), ("stop", "gtc", "new", sym))
        self.assertTrue(all(o["client_order_id"].startswith("sw-") for o in self.runner.b.orders.values()))


if __name__ == "__main__":
    unittest.main()


class SwingReplaySlotsTest(SwingReplayTest):
    """Five names competing for three slots: ranking by relative strength and slot accounting must match the backtest too."""
    data_override = {"AAA": series(2, 0.0020), "BBB": series(3, 0.0012), "CCC": series(4, -0.0002), "DDD": series(5, 0.0016), "EEE": series(6, 0.0018)}
