import json
import tempfile
import unittest
import unittest.mock

import pandas as pd

from alpacabot import swing_paper as sp


class Broker:
    def __init__(self, held):
        self.orders, self.n, self.held = {}, 0, held

    def _new(self, **kw):
        self.n += 1
        oid = f"o{self.n}"
        self.orders[oid] = {"id": oid, **kw}
        return self.orders[oid]

    def submit_order(self, sym, qty, side, typ, tif, cid, stop_price=None):
        return self._new(symbol=sym, qty=qty, side=side, type=typ, tif=tif, stop_price=stop_price, cid=cid,
                         status="filled" if typ == "market" else "new", filled_avg_price=99.0 if typ == "market" else None)

    def submit_oco(self, sym, qty, limit_price, stop_price, cid, side="sell"):
        return self._new(symbol=sym, qty=qty, side=side, type="oco", limit=limit_price, stop_price=stop_price, cid=cid, status="new")

    def cancel_order(self, oid):
        self.orders[oid]["status"] = "canceled"

    def order(self, oid):
        return self.orders[oid]

    def positions(self):
        return dict(self.held)


NOW = pd.Timestamp("2026-11-03 10:10", tz=sp.ET).to_pydatetime()


def runner(d, b, **pos):
    r = sp.SwingRunner(b, state_dir=d, base_equity=50000.0, sleep=lambda *_: None)
    p = {"qty": 23, "entry": 234.68, "stop": 220.41, "entry_date": "2026-10-30", "side": 1, "strategy": "weekpicks", "oco": True, "zone_tp": 241.81}
    p.update(pos)
    r.state["positions"]["CVX"] = p
    return r


class EnsureStopsTests(unittest.TestCase):
    def test_live_stop_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            b = Broker({"CVX": 23})
            so = b.submit_oco("CVX", 23, 241.81, 220.41, "x")
            r = runner(d, b, stop_order_id=so["id"])
            self.assertEqual(r.ensure_stops(NOW, lambda s: (225.0, 225.1)), {})
            self.assertEqual(b.n, 1)

    def test_cancelled_oco_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            b = Broker({"CVX": 23})
            so = b.submit_oco("CVX", 23, 241.81, 220.41, "x")
            b.cancel_order(so["id"])
            r = runner(d, b, stop_order_id=so["id"])
            out = r.ensure_stops(NOW, lambda s: (225.0, 225.1))
            self.assertIn("re-placed OCO", out["CVX"])
            new = b.orders[r.state["positions"]["CVX"]["stop_order_id"]]
            self.assertEqual((new["type"], new["qty"], new["stop_price"], new["limit"]), ("oco", 23, 220.41, 241.81))
            self.assertEqual(r.ensure_stops(NOW, lambda s: (225.0, 225.1)), {})              # now live: nothing more

    def test_missing_stop_plain(self):
        with tempfile.TemporaryDirectory() as d:
            b = Broker({"CVX": 23})
            r = runner(d, b, oco=False, zone_tp=None)
            out = r.ensure_stops(NOW, lambda s: (225.0, 225.1))
            new = b.orders[r.state["positions"]["CVX"]["stop_order_id"]]
            self.assertEqual((new["type"], new["tif"], new["stop_price"], new["side"]), ("stop", "gtc", 220.41, "sell"))
            self.assertIn("re-placed stop", out["CVX"])

    def test_through_stop_exits_at_market(self):
        with tempfile.TemporaryDirectory() as d:
            b = Broker({"CVX": 23})
            so = b.submit_oco("CVX", 23, 241.81, 220.41, "x")
            b.orders[so["id"]]["status"] = "expired"
            r = runner(d, b, stop_order_id=so["id"])
            out = r.ensure_stops(NOW, lambda s: (219.0, 219.1))
            self.assertIn("market exit filled", out["CVX"])
            self.assertNotIn("CVX", r.state["positions"])
            t = json.loads(open(f"{d}/trades.jsonl").read().splitlines()[-1])
            self.assertEqual(t["symbol"], "CVX")

    def test_not_held_never_trades(self):
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.object(sp, "_alert") as al:
            b = Broker({})
            so = b.submit_oco("CVX", 23, 241.81, 220.41, "x")
            b.cancel_order(so["id"])
            r = runner(d, b, stop_order_id=so["id"])
            self.assertEqual(r.ensure_stops(NOW, lambda s: (219.0, 219.1)), {})
            self.assertEqual(b.n, 1)
            r.ensure_stops(NOW, lambda s: (219.0, 219.1))
            self.assertEqual(al.call_count, 1)                                                 # alerted once


if __name__ == "__main__":
    import unittest.mock  # noqa: F401
    unittest.main()
