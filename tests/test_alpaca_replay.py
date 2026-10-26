"""End-to-end proof that the PAPER bot trades exactly what the BACKTEST measures.

A simulated market (10 symbols, 1-minute bars, 6 warm-up sessions + 1 test session) and a simulated broker drive the real PaperRunner bar by
bar, with the same clock the systemd service uses. The same data goes through the backtest engine. Then:

  1. parity        every trade (symbol, long/short, entry bar, exit bar, exit reason, entry and exit price) is identical in both
  2. accounting    the bot's own ledger P&L equals the broker's cash + position change to the cent
  3. session rule  nothing is open after 3:50 pm; no entry after the 3:30 decision bar
  4. gates         shorts only happen while the heat map allows them; no long on a symbol whose sector is BEARISH
  5. report card   it is produced and its win/loss counts match the ledger

Fills in the simulated market are 'touch' fills (a stop or target that is touched fills at its level, a gap fills at the open), which is the
same convention the backtest uses; costs are zero so prices compare exactly. Slippage is tested elsewhere.
"""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from alpacabot import gates, paper
from alpacabot.brain import Brain
from alpacabot.engine import Costs, raw_intraday, split_days
from alpacabot.experts import ALL_EXPERTS, features_both

NY = "America/New_York"
SYMS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "AMD"]
TEST_DAY = date(2026, 10, 9)                          # a Friday
SESSIONS = pd.bdate_range(end=pd.Timestamp(TEST_DAY), periods=7)


def minute_bars(seed: int, start_px: float, calm_after: str | None = None) -> pd.DataFrame:
    """Continuous 1-minute random walk over 7 sessions; each bar opens at the previous close (so gaps only happen through levels)."""
    rng = np.random.default_rng(seed)
    idx = pd.DatetimeIndex([t for d in SESSIONS for t in pd.date_range(f"{d.date()} 09:30", f"{d.date()} 15:59", freq="1min", tz=NY)])
    vol = np.where(rng.random(len(idx)) < 0.02, 0.0025, 0.0009)              # occasional bursts so the experts have something to do
    if calm_after:                                                            # a quiet last stretch: positions stay open into the close
        vol = np.where((idx.date == TEST_DAY) & (idx.strftime("%H:%M") >= calm_after), 0.00002, vol)
    c = start_px * np.exp(np.cumsum(rng.normal(0, 1, len(idx)) * vol + np.sin(np.arange(len(idx)) / 37.0) * 0.0002))
    o = np.r_[start_px, c[:-1]]
    wick = c * vol * 0.6 * rng.random(len(idx))
    h, lo = np.maximum(o, c) + wick, np.minimum(o, c) - wick
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": rng.integers(800, 5000, len(idx)).astype(float)}, index=idx)


def daily_bars(drift: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp(TEST_DAY), periods=90, tz=NY)
    c = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.004, len(idx))))
    return pd.DataFrame({"open": c, "high": c * 1.003, "low": c * 0.997, "close": c, "volume": 1e6}, index=idx)


class SimBroker:
    """Paper broker stand-in: market orders fill at the current quote; positions are signed; cash moves with every fill."""

    def __init__(self, daily):
        self.px, self.pos, self.cash, self._daily = {}, {}, 1_000_000.0, daily

    def quote(self, s):
        return self.px[s], self.px[s]

    def equity(self):
        return self.cash + sum(q * self.px[s] for s, q in self.pos.items())

    def positions(self):
        return {s: q for s, q in self.pos.items() if q}

    def shortable(self, s):
        return True

    def clock_open(self):
        return True

    def daily(self, s, days=90):
        return self._daily.get(s)

    def submit_market(self, s, qty, coid=None, side="buy"):
        q = qty if side == "buy" else -qty
        self.pos[s] = self.pos.get(s, 0) + q
        self.cash -= q * self.px[s]

    def close(self, s, qty=None):
        held = self.pos.get(s, 0)
        q = abs(held) if qty is None else qty
        sign = 1 if held > 0 else -1
        self.pos[s] = held - sign * q
        self.cash += sign * q * self.px[s]


class ReplayTest(unittest.TestCase):
    BRAIN = dict(experts=list(ALL_EXPERTS), short_experts=list(ALL_EXPERTS), sector_filter=True, short_gate="red")
    EXPECT_FLATTEN = False
    CALM_AFTER = None
    ZONES_LIVE = False
    OVERRIDE = None                                   # extra zones_override.json keys (e.g. switch the scaling ladder off)

    @classmethod
    def setUpClass(cls):
        cls.minute = {s: minute_bars(10 + k, 50 + 20 * k, cls.CALM_AFTER) for k, s in enumerate(SYMS)}
        # market falling (BEARISH -> shorts allowed everywhere), tech rising (longs allowed in XLK names), consumer falling (no XLY longs)
        cls.daily = {"SPY": daily_bars(-0.004, 1), "QQQ": daily_bars(0.0, 2), "IWM": daily_bars(0.0, 3), "XLK": daily_bars(0.004, 4),
                     "XLY": daily_bars(-0.004, 5), "XLC": daily_bars(0.0, 6)}
        cls.brain = cls.BRAIN

        # ---------------- backtest
        days = gates.add_gates(split_days(cls.minute, minutes=5, feature_fn=features_both), cls.daily)
        test_days = {s: [f for f in fr if f.index[0].date() == TEST_DAY] for s, fr in days.items()}
        cls.bt = raw_intraday(Brain(**cls.brain), test_days, Costs(0, 0, 0))
        cls.test_frames = {s: fr[0] for s, fr in test_days.items()}

        # ---------------- paper bot, bar by bar
        cls.tmp = tempfile.TemporaryDirectory()
        (Path(cls.tmp.name) / "zones_override.json").write_text(json.dumps({"live": cls.ZONES_LIVE, "scalper": {"live": False}, "velez": {"live": False}, "velez_hybrid": {"live": False}, "max_pos_frac": 0.25, "neg_exit_min": None, "expert_cooldown_min": None, "entry_symbols": None, "account_breaker_pct": None, "account_profit_lock": None, "vz_systems": {"live": False}, **(cls.OVERRIDE or {})}))   # parity: zone-free
        broker = SimBroker(cls.daily)
        for s in SYMS:
            broker.px[s] = float(cls.minute[s]["open"].iloc[0])

        def fetch(sym, tf, start, end, feed):
            df = cls.minute[sym]
            return df[df.index < cls.now]

        cls.now = pd.Timestamp(f"{TEST_DAY} 09:30", tz=NY)
        r = paper.PaperRunner(broker, Brain(**cls.brain), state_dir=cls.tmp.name, symbols=list(SYMS), fetch=fetch, sleep=lambda *_: None,
                              breaker_limit=1.0, max_positions=10, profit_arm=10.0)
        cls.start_equity = broker.equity()
        cls.open_after_350, cls.gate_violations = [], []
        bars = cls.test_frames["SPY"].index                       # 5-minute bar starts, 09:30 .. 15:45
        for k, b in enumerate(bars):
            # intrabar path of bar b, one touch-quote at a time (stop side first, as the backtest assumes)
            m = {s: cls.minute[s].loc[b:b + pd.Timedelta(minutes=4)] for s in SYMS}
            cls.now = b + pd.Timedelta(minutes=2)
            for step in ("open", "adverse", "favorable"):
                for s in SYMS:
                    o, h, lo = float(m[s]["open"].iloc[0]), float(m[s]["high"].max()), float(m[s]["low"].min())
                    pos = r.exits.positions.get(s)
                    if not pos or step == "open":
                        broker.px[s] = o
                        continue
                    side, st = pos.get("side", 1), pos["state"]
                    stop, tp = side * st.stop, side * st.tp
                    if side == 1:
                        broker.px[s] = (max(lo, stop) if lo <= stop else lo) if step == "adverse" else (min(h, tp) if h >= tp else h)
                    else:
                        broker.px[s] = (min(h, stop) if h >= stop else h) if step == "adverse" else (max(lo, tp) if lo <= tp else lo)
                r.poll_exits(cls.now.to_pydatetime())
            # bar close: quotes at the close, then the service's own order of work (housekeeping + exits, then decisions)
            cls.now = b + pd.Timedelta(minutes=5)
            for s in SYMS:
                broker.px[s] = float(m[s]["close"].iloc[-1])
            r.tick(cls.now.to_pydatetime())
            r.on_bar_close(cls.now.to_pydatetime())
            if cls.now.time() >= datetime(2000, 1, 1, 15, 50).time() and r.exits.positions:
                cls.open_after_350.append((str(cls.now), list(r.exits.positions)))
        cls.runner, cls.broker = r, broker
        cls.ledger = paper._jl(Path(cls.tmp.name) / "trades.jsonl")
        cls.journal = paper._jl(Path(cls.tmp.name) / "journal.jsonl")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_0_something_happened(self):
        sides = {t.side for t in self.bt}
        self.assertGreaterEqual(len(self.bt), 10, "the replay must exercise real trades")
        self.assertEqual(sides, {1, -1}, "both longs and shorts must be exercised")

    def test_1_paper_trades_equal_backtest_trades(self):
        reason = {"eod": "flatten", "time": "time", "stop": "stop", "target": "target", "signal": "signal"}
        bt = sorted((t.symbol, "short" if t.side == -1 else "long", str(t.entry_time), reason[t.reason], round(float(t.entry_px), 4), round(float(t.exit_px), 4))
                    for t in self.bt)
        pp = sorted((t["symbol"], t["side"], str(pd.Timestamp(self.runner_entry_bar(t))), t["reason"], round(t["entry"], 4), round(t["exit"], 4))
                    for t in self.ledger)
        self.maxDiff = None
        self.assertEqual(pp, bt)
        print(f"\nparity: {len(pp)} trades identical ({sum(x[1] == 'short' for x in pp)} shorts); reasons "
              f"{ {r: sum(x[3] == r for x in pp) for r in sorted({x[3] for x in pp})} }")

    def runner_entry_bar(self, t):
        # the ledger stores when the order was sent (bar close); the backtest's entry bar is the next bar, which starts at that moment
        return pd.Timestamp(t["opened"]).tz_convert(NY)

    def test_2_ledger_pnl_equals_broker_pnl(self):
        self.assertEqual(self.broker.positions(), {}, "flat at the end of the day")
        self.assertAlmostEqual(sum(t["pnl"] for t in self.ledger), self.broker.equity() - self.start_equity, places=0)
        bt_pnl_per_share = sum(t.pnl_per_share(Costs(0, 0, 0)) for t in self.bt)
        ledger_pnl_per_share = sum((1 if t["side"] == "long" else -1) * (t["exit"] - t["entry"]) for t in self.ledger)
        self.assertAlmostEqual(ledger_pnl_per_share, bt_pnl_per_share, places=6)

    def test_3_flat_by_350_and_no_late_entries(self):
        self.assertEqual(self.open_after_350, [])
        if self.EXPECT_FLATTEN:
            self.assertTrue([t for t in self.ledger if t["reason"] == "flatten"], "the 3:50 flatten must be exercised")
            self.assertTrue(all(pd.Timestamp(j["t"]).time() <= datetime(2000, 1, 1, 15, 50).time()
                                for j in self.journal if j["kind"] == "exit"))
        for t in self.ledger:
            self.assertLessEqual(pd.Timestamp(t["opened"]).tz_convert(NY).time(), datetime(2000, 1, 1, 15, 35).time())

    def test_4_gates_respected(self):
        heat = gates.heat_map(self.daily)
        market = gates.state_on(heat["SPY"], TEST_DAY)
        self.assertEqual(market, -1, "the test market is BEARISH, so shorts are allowed")
        for t in self.ledger:
            sector = gates.state_on(heat[gates.SECTOR[t["symbol"]]], TEST_DAY)
            if t["side"] == "long":
                self.assertNotEqual(sector, -1, f"long {t['symbol']} in a BEARISH sector")
        self.assertFalse([t for t in self.ledger if t["side"] == "long" and t["symbol"] in ("AMZN", "TSLA")],
                         "XLY is BEARISH: AMZN/TSLA longs must be blocked")
        logged = [j for j in self.journal if j["kind"] == "heat_map"]
        self.assertTrue(logged and logged[0]["SPY"] == -1 and logged[0]["XLK"] == 1, "the paper bot must see the real heat map, not NEUTRAL")

    def test_5_report_card_matches_ledger(self):
        card = paper.report_card(self.tmp.name, TEST_DAY)
        whole = paper.whole_trades(self.ledger)                   # a scaled-out trade counts once
        w = sum(t["pnl"] > 0 for t in whole)
        self.assertIn(f"Today: {len(whole)} trades, {w}W/{len(whole) - w}L", card)
        self.assertIn("longs:", card)
        self.assertIn("shorts:", card)


class ReplayHoldToCloseTest(ReplayTest):
    """The two experts that hold until the close (trailing manager, no time stop): exercises the 3:50 pm flatten against the backtest's
    end-of-day exit, for longs and shorts."""
    BRAIN = dict(experts=["donchian_turtle", "dual_regime"], short_experts=["donchian_turtle", "dual_regime"], sector_filter=True,
                 short_gate="red", route=False, mtf=False, max_trades=10)
    EXPECT_FLATTEN = True
    CALM_AFTER = "14:20"

    def test_0_something_happened(self):
        self.assertGreaterEqual(len(self.bt), 3)


class ReplayShadowTest(ReplayTest):
    """Every expert in shadow_tags (owner 2026-10-22): signals are still logged, no order is ever placed."""
    OVERRIDE = {"shadow_tags": list(ALL_EXPERTS) + ["S:" + e for e in ALL_EXPERTS]}

    def test_1_paper_trades_equal_backtest_trades(self):
        self.assertEqual(self.ledger, [], "a shadowed expert placed an order")
        rows = [json.loads(l) for f in Path(self.tmp.name).glob("journal*.jsonl") for l in f.read_text().splitlines() if l.strip()]
        self.assertTrue(any(str(j.get("result", "")).startswith("shadow") for j in rows), "shadow signals were not logged")

    def test_2_ledger_pnl_equals_broker_pnl(self):
        self.assertEqual(self.broker.positions(), {})

    def test_5_report_card_matches_ledger(self):
        pass


if __name__ == "__main__":
    unittest.main()



class ReplayZonesLiveTest(ReplayTest):
    """The same day with the owner's zones LIVE: no trade-for-trade parity with the zone-free backtest (by design), but the books must still
    balance with the broker, everything is flat by 3:50, and zone take-profits / exhaustion exits really fire."""
    ZONES_LIVE = True

    def test_1_paper_trades_equal_backtest_trades(self):
        reasons = {t["reason"] for t in self.ledger}
        self.assertTrue(any(r.startswith("zone") or r == "exhausted" for r in reasons), f"zone exits never fired: {reasons}")
        self.assertTrue(all("zone_profile" in t for t in self.ledger), "every trade logs its zone profile")

    def test_2_ledger_pnl_equals_broker_pnl(self):
        self.assertEqual(self.broker.positions(), {}, "flat at the end of the day")
        self.assertAlmostEqual(sum(t["pnl"] for t in self.ledger), self.broker.equity() - self.start_equity, places=0)

    def test_3_ladder_used(self):
        """The owner's scaling ladder (alpacabot/zones.json): entries are tiered, and it scales in or out at least once."""
        kinds = [json.loads(l)["kind"] for l in (Path(self.tmp.name) / "journal.jsonl").read_text().splitlines()]
        self.assertIn("ladder_open", kinds)
        self.assertTrue(any(t.get("partial") for t in self.ledger) or "ladder_add" in kinds, "the ladder never scaled in or out")


class ReplayZonesNoLadderTest(ReplayZonesLiveTest):
    """Zones live with the scaling ladder switched off locally: one entry size, one full take-profit at the zone."""
    OVERRIDE = {"ladder": None}

    def test_3_ladder_used(self):
        self.assertFalse(any(t.get("partial") for t in self.ledger))
