import json
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
import pandas as pd
import pytest
from fxexp import live
from fxexp.live import Runner, entry_levels, is_armed, order_body, market_open, in_rollover, trading_day, day_bounds
from fxexp.strategies.base import Order
from fxexp.oanda import Oanda, OandaError
NY = ZoneInfo("America/New_York")

def ny(y, m, d, h, mi=0): return datetime(y, m, d, h, mi, tzinfo=NY).astimezone(timezone.utc)

def test_market_hours_and_rollover():
    assert not market_open(ny(2026, 10, 3, 12))           # Saturday
    assert not market_open(ny(2026, 10, 2, 17, 5))        # Friday after close
    assert market_open(ny(2026, 10, 4, 17, 5))            # Sunday open
    assert in_rollover(ny(2026, 10, 5, 17, 5)) and not in_rollover(ny(2026, 10, 5, 17, 20))
    assert trading_day(ny(2026, 10, 4, 18)) == date(2026, 10, 5)

def test_entry_levels_variants():
    o = Order(1, price=1.1, sl_dist=0.003, tp_dist=0.006)
    assert entry_levels(o, "EUR_USD", 1.09, 1.08, 1.12, 0.00014) == (1.1, pytest.approx(1.097), 0.003, pytest.approx(1.106))
    o2 = Order(-1, from_open=-0.002, sl_from_open=0.002)
    lv, sl, dist, tp = entry_levels(o2, "EUR_USD", 1.10, 1.09, 1.11, 0.00014)
    assert lv == pytest.approx(1.098) and sl == pytest.approx(1.102) and tp is None
    o3 = Order(1, price=1.1, sl_daylow=True)
    assert entry_levels(o3, "EUR_USD", 1.1, 1.0950, 1.11, 0.0002)[1] == pytest.approx(1.0950 - 0.0001 - 0.0001)

def test_is_armed():
    o = Order(1, price=1.1, arm=1.09)
    assert not is_armed(o, 1.0905, 1.1) and is_armed(o, 1.0899, 1.1)
    assert is_armed(Order(1, price=1.1), 1.2, 1.2)

def test_order_body_has_broker_side_stop_and_target():
    o = Order(1, price=1.1, sl_dist=0.003, tp_dist=0.006)
    b = order_body("EUR_USD", o, 1000, 1.1, 1.097, 0.003, 1.106, ny(2026, 10, 5, 16, 59), "cid1", "lien-fader", 0.00014)
    assert b["type"] == "STOP" and b["units"] == "1000" and b["timeInForce"] == "GTD" and b["gtdTime"].endswith("Z")
    assert b["stopLossOnFill"]["distance"] == "0.00300" and b["takeProfitOnFill"]["price"] == "1.10600"
    assert b["positionFill"] == "OPEN_ONLY" and b["clientExtensions"]["comment"] == "lien-fader"
    b2 = order_body("USD_JPY", Order(-1, kind="market", sl=151.0), 500, 150.0, 151.0, None, None, ny(2026, 10, 5, 16, 59), "c", "s", 0.014)
    assert b2["type"] == "MARKET" and b2["units"] == "-500" and b2["stopLossOnFill"]["price"] == "151.000" and "takeProfitOnFill" not in b2

def test_live_env_refused(monkeypatch):
    monkeypatch.delenv("FX_ALLOW_LIVE", raising=False)
    with pytest.raises(OandaError): Oanda("x", "y", "live")

def test_dry_run_records_but_does_not_send():
    c = Oanda("tok", "101-001-1-001", "practice", dry_run=True)
    assert c.create_order({"type": "STOP"}) == {"dryRun": True} and len(c.writes) == 1

class FakeCli:
    def __init__(self, daily_df, intraday=(1.1, 1.09, 1.11), prices=None):
        self.d, self.ix = daily_df, intraday; self.trades, self.orders, self.writes = [], [], []
        self.prices = prices or {"EUR_USD": (1.09993, 1.10007, True)}
    def candles(self, pair, gran, count=None, frm=None, price="M"):
        if gran == "D":
            df = self.d.copy(); df["time"] = [pd.Timestamp(i) - pd.Timedelta(hours=7) for i in df.index]
            df["time"] = df["time"].dt.tz_localize(NY).dt.tz_convert("UTC"); df["complete"] = True
            return df.reset_index(drop=True)
        o, l, h = self.ix
        return pd.DataFrame([{"time": pd.Timestamp.now(tz="UTC"), "open": o, "high": h, "low": l, "close": o, "complete": True}])
    def pricing(self, names): return self.prices
    def summary(self): return {"NAV": "100000"}
    def open_trades(self): return self.trades
    def pending_orders(self): return self.orders
    def trade(self, tid): return {"price": "1.1", "stopLossOrder": {"state": "FILLED"}}
    def create_order(self, b): self.writes.append(("create", b)); self.orders.append({"id": str(len(self.writes)), "instrument": b["instrument"], "units": b["units"], "clientExtensions": b["clientExtensions"]}); return {}
    def replace_order(self, oid, b): self.writes.append(("replace", oid, b)); return {}
    def cancel_order(self, oid): self.writes.append(("cancel", oid)); self.orders = [o for o in self.orders if o["id"] != oid]; return {}
    def set_stop(self, tid, px): self.writes.append(("stop", tid, px)); return {}
    def close_trade(self, tid): self.writes.append(("close", tid)); return {}

def flat(n=60, end="2026-10-02"):
    idx = pd.bdate_range(end=end, periods=n)
    off = [0.001 * (1 if i % 2 else -1) for i in range(n)]; off[-1] = 0.0
    return pd.DataFrame({"open": 1.1, "high": [1.1025 + o for o in off], "low": [1.0975 + o for o in off], "close": 1.1}, index=idx)

def cfg(**kw):
    c = {"systems": ["lien-fader"], "pairs": ["EUR_USD"], "risk_pct": 0.5}; c.update(kw); return c

def test_runner_plans_then_places_only_armed(tmp_path):
    cli = FakeCli(flat(), intraday=(1.1, 1.0990, 1.1010))
    r = Runner(cfg(), {"lien-fader": cli}, str(tmp_path / "s.json"), now=ny(2026, 10, 4, 18), log=lambda m: None)
    r.run()
    st = json.load(open(tmp_path / "s.json"))["lien-fader"]["EUR_USD"]
    assert len(st["orders"]) == 2 and all(o["status"] == "planned" for o in st["orders"])
    assert not [w for w in cli.writes if w[0] == "create"]
    cli2 = FakeCli(flat(), intraday=(1.1, 1.0950, 1.1010))     # broke below PDL-15 -> long armed
    r2 = Runner(cfg(), {"lien-fader": cli2}, str(tmp_path / "s.json"), now=ny(2026, 10, 4, 23), log=lambda m: None)
    r2.run()
    creates = [w[1] for w in cli2.writes if w[0] == "create"]
    assert len(creates) == 1 and creates[0]["units"].lstrip("-").isdigit() and float(creates[0]["price"]) == pytest.approx(1.1040)
    assert creates[0]["stopLossOnFill"]["distance"] == "0.00300" and float(creates[0]["takeProfitOnFill"]["price"]) == pytest.approx(1.1100)

def test_runner_defers_on_wide_spread(tmp_path):
    cli = FakeCli(flat(), intraday=(1.1, 1.0950, 1.1010), prices={"EUR_USD": (1.0990, 1.1010, True)})
    Runner(cfg(), {"lien-fader": cli}, str(tmp_path / "s.json"), now=ny(2026, 10, 4, 18), log=lambda m: None).run()
    assert not [w for w in cli.writes if w[0] == "create"]

def test_runner_oco_cancel_after_fill(tmp_path):
    cli = FakeCli(flat(), intraday=(1.1, 1.0950, 1.1060))
    Runner(cfg(), {"lien-fader": cli}, str(tmp_path / "s.json"), now=ny(2026, 10, 4, 18), log=lambda m: None).run()
    placed = [w[1] for w in cli.writes if w[0] == "create"]
    assert len(placed) == 2
    cid = placed[0]["clientExtensions"]["id"]
    cli.trades = [{"id": "77", "instrument": "EUR_USD", "currentUnits": placed[0]["units"], "price": "1.104",
                   "openTime": "2026-10-05T01:00:00.000000000Z", "clientExtensions": {"id": "t-" + cid, "comment": "lien-fader"},
                   "stopLossOrder": {"price": "1.101"}}]
    cli.writes.clear()
    Runner(cfg(), {"lien-fader": cli}, str(tmp_path / "s.json"), now=ny(2026, 10, 4, 19), log=lambda m: None).run()
    assert [w for w in cli.writes if w[0] == "cancel"] and not [w for w in cli.writes if w[0] == "create"]

def test_runner_crabel_closes_day_trade(tmp_path):
    cli = FakeCli(flat())
    path = str(tmp_path / "s.json")
    json.dump({"crabel-idnr4": {"EUR_USD": {"day": "2026-10-05", "orders": [], "trades": {"9": {"side": 1, "entry_day": "2026-10-05", "close_eod": True, "meta": {}}}, "sstate": {}}}}, open(path, "w"))
    cli.trades = [{"id": "9", "instrument": "EUR_USD", "currentUnits": "100", "price": "1.1", "openTime": "2026-10-05T01:00:00Z",
                   "clientExtensions": {"id": "t-x", "comment": "crabel-idnr4"}, "stopLossOrder": {"price": "1.09"}}]
    Runner(cfg(systems=["crabel-idnr4"]), {"crabel-idnr4": cli}, path, now=ny(2026, 10, 5, 16, 52), log=lambda m: None).run()
    assert ("close", "9") in cli.writes

def test_runner_turtle_trails_stop_at_new_day(tmp_path):
    d = flat(80)
    cli = FakeCli(d)
    cli.trades = [{"id": "5", "instrument": "EUR_USD", "currentUnits": "100", "price": "1.1", "openTime": "2026-09-20T01:00:00Z",
                   "clientExtensions": {"id": "t-x", "comment": "faith-turtle"}, "stopLossOrder": {"price": "1.0800"}}]
    Runner(cfg(systems=["faith-turtle"]), {"faith-turtle": cli}, str(tmp_path / "s.json"), now=ny(2026, 10, 4, 18), log=lambda m: None).run()
    stops = [w for w in cli.writes if w[0] == "stop"]
    assert stops and float(stops[0][2]) == pytest.approx(1.0965 - 0.0001)  # lowest low of last 10 days - 1 tick

def test_runner_does_nothing_when_market_closed(tmp_path):
    cli = FakeCli(flat())
    Runner(cfg(), {"lien-fader": cli}, str(tmp_path / "s.json"), now=ny(2026, 10, 3, 12), log=lambda m: None).run()
    assert cli.writes == []

def test_runner_skips_pair_held_by_other_system(tmp_path):
    cli = FakeCli(flat(), intraday=(1.1, 1.0950, 1.1010))
    cli.trades = [{"id": "1", "instrument": "EUR_USD", "currentUnits": "100", "price": "1.1", "openTime": "2026-10-04T22:00:00Z",
                   "clientExtensions": {"id": "t-z", "comment": "raschke-soup"}, "stopLossOrder": {"price": "1.09"}}]
    Runner(cfg(), {"lien-fader": cli}, str(tmp_path / "s.json"), now=ny(2026, 10, 4, 18), log=lambda m: None).run()
    assert not [w for w in cli.writes if w[0] == "create"]

def test_switch_on_off(tmp_path):
    from fxexp import switch
    p = tmp_path / "c.json"; json.dump({"systems": ["lien-fader"]}, open(p, "w"))
    switch.main(["on", "faith-turtle"], str(p)); assert json.load(open(p))["systems"] == ["lien-fader", "faith-turtle"]
    switch.main(["off", "lien-fader"], str(p)); assert json.load(open(p))["systems"] == ["faith-turtle"]
    with pytest.raises(SystemExit): switch.main(["on", "nope"], str(p))
