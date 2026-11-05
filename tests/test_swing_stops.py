import json
import tempfile
import unittest
import unittest.mock

import numpy as np
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

    def test_oco_stop_leg_filled_is_booked_not_flagged(self):
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.object(sp, "_alert") as al:
            b = Broker({})
            so = b.submit_oco("CVX", 23, 241.81, 220.41, "x")
            b.orders[so["id"]].update(status="canceled", legs=[{"status": "filled", "filled_avg_price": 220.30}])
            r = runner(d, b, stop_order_id=so["id"])
            self.assertEqual(r.ensure_stops(NOW, lambda s: (219.0, 219.1)), {})
            self.assertNotIn("CVX", r.state["positions"])
            self.assertEqual(al.call_count, 0)
            t = json.loads(open(f"{d}/trades.jsonl").read().splitlines()[-1])
            self.assertEqual((t["symbol"], t["exit"] if "exit" in t else t.get("px")), ("CVX", 220.30))

    def test_not_held_alert_persists_across_runners(self):
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.object(sp, "_alert") as al:
            b = Broker({})
            so = b.submit_oco("CVX", 23, 241.81, 220.41, "x")
            b.cancel_order(so["id"])
            runner(d, b, stop_order_id=so["id"]).ensure_stops(NOW, lambda s: (219.0, 219.1))
            r2 = sp.SwingRunner(b, state_dir=d, base_equity=50000.0, sleep=lambda *_: None)     # a new minute = a new runner
            r2.ensure_stops(NOW, lambda s: (219.0, 219.1))
            self.assertEqual(al.call_count, 1)

    def test_topdown_2r_tp_from_fill(self):
        with tempfile.TemporaryDirectory() as d:
            b = Broker({})
            r = sp.SwingRunner(b, state_dir=d, base_equity=50000.0, sleep=lambda *_: None)
            r.state["plan"] = {"entries": [{"symbol": "AAA", "qty": 10, "stop": 95.0, "zone_tp": 130.0, "tp_r": 2.0}]}
            with unittest.mock.patch.object(r, "zone_config", return_value={"live": True}):
                r._settle_open("AAA", "entry", {"status": "filled", "filled_avg_price": 100.5, "filled_qty": 10}, NOW)
            pos = r.state["positions"]["AAA"]
            self.assertEqual(pos["zone_tp"], 111.5)                       # 100.5 + 2 x (100.5 - 95), not the zone map's 130
            oco = b.orders[pos["stop_order_id"]]
            self.assertEqual((oco["limit"], oco["stop_price"]), (111.5, 95.0))

    def test_zone_tp_unchanged_without_tp_r(self):
        with tempfile.TemporaryDirectory() as d:
            b = Broker({})
            r = sp.SwingRunner(b, state_dir=d, base_equity=50000.0, sleep=lambda *_: None)
            r.state["plan"] = {"entries": [{"symbol": "AAA", "qty": 10, "stop": 95.0, "zone_tp": 130.0}]}
            with unittest.mock.patch.object(r, "zone_config", return_value={"live": True}):
                r._settle_open("AAA", "entry", {"status": "filled", "filled_avg_price": 100.5, "filled_qty": 10}, NOW)
            self.assertEqual(r.state["positions"]["AAA"]["zone_tp"], 130.0)

    def test_spy_regime_filter(self):
        idx = pd.bdate_range(end="2026-11-04", periods=60)
        up = pd.DataFrame({"close": np.linspace(400, 480, 60)}, index=idx)
        dn = pd.DataFrame({"close": np.linspace(480, 400, 60)}, index=idx)
        ents = [{"symbol": "AAA", "side": 1}, {"symbol": "BBB", "side": -1}]
        with tempfile.TemporaryDirectory() as d:
            r = sp.SwingRunner(Broker({}), state_dir=d, base_equity=50000.0, sleep=lambda *_: None)
            self.assertEqual([e["symbol"] for e in r.spy_regime_filter(up, ents, NOW)], ["AAA"])
            self.assertEqual([e["symbol"] for e in r.spy_regime_filter(dn, ents, NOW)], ["BBB"])

    def test_stock_news_filter(self):
        ents = [{"symbol": "AAA", "side": 1}, {"symbol": "BBB", "side": 1}]
        now = pd.Timestamp("2026-11-05 16:20", tz=sp.ET).to_pydatetime()
        arts = [{"t": "2026-11-05T12:00:00Z", "symbols": ["AAA"], "s": -2}, {"t": "2026-11-05T13:00:00Z", "symbols": ["BBB"], "s": 1},
                {"t": "2026-10-01T13:00:00Z", "symbols": ["BBB"], "s": -9}]                      # too old: ignored
        with tempfile.TemporaryDirectory() as d:
            r = sp.SwingRunner(Broker({}), state_dir=d, base_equity=50000.0, sleep=lambda *_: None)
            with unittest.mock.patch.object(r, "zone_config", return_value={"stock_news": {"live": True, "hours": 48}}):
                kept = r.stock_news_filter(ents, now, fetch_news=lambda syms, a, b, c: arts)
                self.assertEqual([e["symbol"] for e in kept], ["BBB"])
                def boom(*a):
                    raise RuntimeError("feed down")
                self.assertEqual(r.stock_news_filter(ents, now, fetch_news=boom), ents)          # feed failure skips nothing

    def test_seeking_alpha_log(self):
        rss = (b"<rss><channel><item><title>XYZ downgraded on weak guidance</title><pubDate>Thu, 05 Nov 2026 14:00:00 +0000</pubDate>"
               b"</item><item><title>XYZ old news</title><pubDate>Mon, 26 Oct 2026 14:00:00 +0000</pubDate></item></channel></rss>")

        class R:
            def __init__(self, code, content=b""):
                self.status_code, self.content = code, content
        now = pd.Timestamp("2026-11-05 16:20", tz=sp.ET).to_pydatetime()
        with tempfile.TemporaryDirectory() as d:
            r = sp.SwingRunner(Broker({}), state_dir=d, base_equity=50000.0, sleep=lambda *_: None)
            r.seeking_alpha_log(["XYZ", "ERR"], now, get=lambda url: R(200, rss) if "XYZ" in url else R(403))
            row = json.loads(open(f"{d}/seekingalpha.jsonl").read().splitlines()[-1])["rows"]
            self.assertEqual((row[0]["items"], row[0]["items_48h"]), (2, 1))
            self.assertLess(row[0]["score_48h"], 0)
            self.assertEqual(row[1]["error"], "http 403")

    def test_market_news_filter(self):
        ents = [{"symbol": "AAA", "side": 1}, {"symbol": "BBB", "side": -1}]
        now = pd.Timestamp("2026-11-05 16:20", tz=sp.ET).to_pydatetime()
        bad = [{"t": "2026-11-05T1%d:00:00Z" % i, "symbols": ["QQQ"], "s": -1} for i in range(4)]       # "Nasdaq falls ..." x4
        good = [{"t": "2026-11-05T1%d:00:00Z" % i, "symbols": ["SPY"], "s": 1} for i in range(4)]
        with tempfile.TemporaryDirectory() as d:
            r = sp.SwingRunner(Broker({}), state_dir=d, base_equity=50000.0, sleep=lambda *_: None)
            with unittest.mock.patch.object(r, "zone_config", return_value={"market_news": {"live": True}}):
                self.assertEqual([e["symbol"] for e in r.market_news_filter(ents, now, fetch_news=lambda *a: bad)], ["BBB"])   # longs out
                self.assertEqual(r.market_news_filter(ents, now, fetch_news=lambda *a: good), ents)
                self.assertEqual(r.market_news_filter(ents, now, fetch_news=lambda *a: bad[:2]), ents)                         # < 3 articles


if __name__ == "__main__":
    import unittest.mock  # noqa: F401
    unittest.main()
