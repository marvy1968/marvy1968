import tempfile
import unittest
from datetime import date

import numpy as np
import pandas as pd

from alpacabot import stairlive as sl
from alpacabot import swing_paper as sp


def daily(seed, n=700, drift=0.0006, end="2026-10-30"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=n)
    c = 50 * np.exp(np.cumsum(drift + rng.normal(0, 0.015, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.003, n))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.01, "low": np.minimum(o, c) * 0.99, "close": c,
                         "volume": rng.uniform(1e6, 3e6, n) * 10}, index=idx)


class FakeBroker:
    def __init__(self, fill_px=110.0):
        self.orders, self.n, self.fill_px = {}, 0, fill_px

    def submit_order(self, sym, qty, side, typ, tif, cid, stop_price=None):
        self.n += 1
        oid = f"o{self.n}"
        self.orders[oid] = {"id": oid, "symbol": sym, "qty": qty, "side": side, "type": typ, "stop_price": stop_price,
                            "status": "filled" if typ == "market" else "new", "filled_avg_price": self.fill_px if typ == "market" else None}
        return self.orders[oid]

    def cancel_order(self, oid):
        self.orders[oid]["status"] = "canceled"

    def order(self, oid):
        return self.orders[oid]


class StairLiveTests(unittest.TestCase):
    def test_week_end_index(self):
        cal = pd.bdate_range("2026-10-19", "2026-11-03")
        t = len(cal) - 1                                  # Tue 11-03 -> last week end is Fri 10-30
        self.assertEqual(cal[sl.week_end_index(cal, t)].date(), date(2026, 10, 30))
        t = list(cal.date).index(date(2026, 10, 30))     # a Friday is its own week end
        self.assertEqual(sl.week_end_index(cal, t), t)

    def test_plan_runs_and_respects_limits(self):
        sector_of = {f"S{i}": ("XLK" if i % 2 else "XLF") for i in range(8)}
        bars = {s: daily(i, drift=0.0009) for i, s in enumerate(sector_of)}
        for k, e in enumerate(("XLK", "XLF", "XLV", "SPY")):
            bars[e] = daily(50 + k, drift=0.0004)
        out = sl.plan(bars, date(2026, 10, 30), {}, sector_of, ["XLK", "XLF", "XLV"], {"max_open": 1})
        self.assertNotIn("skipped", out)
        self.assertLessEqual(len(out["entries"]), 1)
        for e in out["entries"]:
            self.assertLess(e["stop"], e["signal_close"])
        self.assertIn("skipped", sl.plan(bars, date(2026, 11, 2), {}, sector_of, ["XLK", "XLF", "XLV"], {}))

    def test_partials_then_breakeven_stop(self):
        with tempfile.TemporaryDirectory() as d:
            b = FakeBroker(fill_px=110.0)
            r = sp.SwingRunner(b, state_dir=d, base_equity=50000.0, sleep=lambda *_: None)
            stop = b.submit_order("AAA", 9, "sell", "stop", "gtc", "x", stop_price=95.0)
            r.state["positions"]["AAA"] = {"qty": 9, "entry": 100.0, "stop": 95.0, "entry_date": "2026-11-02", "side": 1, "strategy": "stairstep",
                                           "r_stop": 95.0, "qty0": 9, "parts": 0, "tp1": 105.0, "tp2": 107.5, "stop_order_id": stop["id"]}
            now = pd.Timestamp("2026-11-03 11:00", tz=sp.ET).to_pydatetime()
            self.assertEqual(r.stair_partials(now, lambda s: (104.0, 104.1)), {})          # below tp1: nothing
            out = r.stair_partials(now, lambda s: (106.0, 106.1))
            pos = r.state["positions"]["AAA"]
            self.assertIn("tp1", out["AAA"])
            self.assertEqual((pos["qty"], pos["parts"]), (6, 1))
            self.assertAlmostEqual(pos["stop"], 100.24)
            self.assertEqual(b.orders[stop["id"]]["status"], "canceled")
            new_stop = b.orders[pos["stop_order_id"]]
            self.assertEqual((new_stop["qty"], new_stop["stop_price"]), (6, 100.24))
            r.stair_partials(now, lambda s: (108.0, 108.1))
            self.assertEqual((r.state["positions"]["AAA"]["qty"], r.state["positions"]["AAA"]["parts"]), (3, 2))
            self.assertEqual(r.stair_partials(now, lambda s: (120.0, 120.1)), {})          # runner: no more partials
            trades = [l for l in open(f"{d}/trades.jsonl").read().splitlines() if l.strip()]
            self.assertEqual(len(trades), 2)
            self.assertIn('"r": 2.0', trades[0])                                             # (110-100)/(100-95) on the initial risk

    def test_profit_lock_skips_stairstep(self):
        with tempfile.TemporaryDirectory() as d:
            r = sp.SwingRunner(FakeBroker(), state_dir=d, base_equity=50000.0, sleep=lambda *_: None)
            r.state["positions"]["AAA"] = {"qty": 9, "entry": 100.0, "stop": 95.0, "entry_date": "2026-11-02", "side": 1, "strategy": "stairstep"}
            now = pd.Timestamp("2026-11-03 11:00", tz=sp.ET).to_pydatetime()
            self.assertEqual(r.profit_lock(now, lambda s: (120.0, 120.1), {"trigger_r": 1.0, "keep": 0.5, "floor": 0.002}), {})


if __name__ == "__main__":
    unittest.main()
