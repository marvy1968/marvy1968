import unittest
from datetime import date

import numpy as np
import pandas as pd

from alpacabot.engine import Costs, Portfolio, Trade, metrics, raw_intraday, simulate_day, simulate_swing, size_intraday, size_swing, split_days
from alpacabot.indicators import atr, rsi, session_vwap
from alpacabot.research import verdict, walk_forward
from alpacabot.strategies import OpeningRangeBreak, RsiPullback, Signal, VwapReversion

NY = "America/New_York"


def make_day(day="2024-03-04", n=390, seed=0, drift=0.0, vol=0.0004, start=100.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(f"{day} 09:30", periods=n, freq="min", tz=NY)
    c = start * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    o = np.r_[start, c[:-1]]
    spread = np.abs(rng.normal(0, vol / 2, n)) * c
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + spread, "low": np.minimum(o, c) - spread, "close": c,
                         "volume": rng.integers(1000, 5000, n).astype(float)}, index=idx)


def flat_day(price=100.0, n=390, day="2024-03-04"):
    idx = pd.date_range(f"{day} 09:30", periods=n, freq="min", tz=NY)
    return pd.DataFrame({"open": price, "high": price + 0.05, "low": price - 0.05, "close": price, "volume": 1000.0}, index=idx)


class IndicatorTests(unittest.TestCase):
    def test_rsi_extremes(self):
        up = pd.Series(np.arange(1.0, 30.0))
        self.assertGreater(rsi(up, 2).iloc[-1], 99)
        self.assertLess(rsi(up[::-1].reset_index(drop=True), 2).iloc[-1], 1)

    def test_vwap_matches_manual(self):
        df = flat_day(n=4)
        df.loc[df.index[1], ["high", "low", "close", "volume"]] = [102, 98, 101, 3000]
        v = session_vwap(df)
        tp = (df.high + df.low + df.close) / 3
        self.assertAlmostEqual(v.iloc[1], (tp.iloc[0] * 1000 + tp.iloc[1] * 3000) / 4000)


class CausalityTests(unittest.TestCase):
    def test_signals_do_not_use_future_bars(self):
        for seed in range(6):
            df = make_day(seed=seed, vol=0.0012)
            for strat in (VwapReversion(z_entry=1.0, max_trades=50), OpeningRangeBreak()):
                full = [s.i for s in strat.signals(df)]
                for cut in (120, 200, 300):
                    part = [s.i for s in strat.signals(df.iloc[:cut])]
                    self.assertEqual(part, [i for i in full if i < cut][: len(part)], (seed, cut, type(strat).__name__))
                    self.assertTrue(all(i < cut for i in part))


class FillTests(unittest.TestCase):
    costs = Costs(slip_bps=5.0, fee_bps_sell=0.0)

    def test_entry_is_next_bar_open_with_slippage(self):
        df = flat_day()
        df.iloc[101, df.columns.get_loc("open")] = 101.0
        t = simulate_day("X", df, [Signal(100, 1, 1.0, 2.0)], self.costs)[0]
        self.assertEqual(t.entry_time, df.index[101])
        self.assertAlmostEqual(t.entry_px, 101.0 * (1 + 5e-4))

    def test_stop_wins_when_stop_and_target_hit_in_same_bar(self):
        df = flat_day()
        df.iloc[102, df.columns.get_loc("high")] = 110.0
        df.iloc[102, df.columns.get_loc("low")] = 90.0
        t = simulate_day("X", df, [Signal(100, 1, 1.0, 2.0)], self.costs)[0]
        self.assertEqual(t.reason, "stop")
        self.assertLess(t.exit_px, t.entry_px)

    def test_target_and_limit_fill(self):
        df = flat_day()
        df.iloc[105, df.columns.get_loc("high")] = 103.0
        t = simulate_day("X", df, [Signal(100, 1, 1.0, 2.0)], self.costs)[0]
        self.assertEqual(t.reason, "target")
        self.assertAlmostEqual(t.exit_px, t.entry_px + 2.0)

    def test_everything_is_flat_at_the_end_of_the_session(self):
        df = flat_day()
        t = simulate_day("X", df, [Signal(380, 1, 5.0, 0.0)], self.costs)[0]
        self.assertEqual(t.reason, "eod")
        self.assertEqual(t.exit_time, df.index[-1])
        self.assertEqual(simulate_day("X", df, [Signal(389, 1, 1.0, 1.0)], self.costs), [])   # no bar left to fill on

    def test_gap_down_through_the_stop_fills_at_the_open(self):
        df = flat_day()
        df.iloc[103, df.columns.get_loc("open")] = 95.0
        df.iloc[103, df.columns.get_loc("low")] = 94.0
        t = simulate_day("X", df, [Signal(100, 1, 1.0, 0.0)], Costs(0, 0))[0]
        self.assertAlmostEqual(t.exit_px, 95.0)

    def test_costs_reduce_pnl(self):
        df = flat_day()
        df.iloc[105, df.columns.get_loc("high")] = 103.0
        t0 = simulate_day("X", df, [Signal(100, 1, 1.0, 2.0)], Costs(0, 0))[0]
        t1 = simulate_day("X", df, [Signal(100, 1, 1.0, 2.0)], Costs(5, 1))[0]
        self.assertLess(t1.pnl_per_share(Costs(5, 1)), t0.pnl_per_share(Costs(0, 0)))

    def test_short_target_and_stop_mirror_the_long(self):
        df = flat_day()                                        # flat at 100
        df.iloc[105, df.columns.get_loc("low")] = 97.0         # price falls: the short's 2R target (98) is hit
        t = simulate_day("X", df, [Signal(100, -1, 1.0, 2.0)], Costs(0, 0))[0]
        self.assertEqual((t.side, t.reason), (-1, "target"))
        self.assertAlmostEqual(t.entry_px, 100.0)
        self.assertAlmostEqual(t.exit_px, 98.0)
        self.assertAlmostEqual(t.pnl_per_share(Costs(0, 0)), 2.0)
        df = flat_day()
        df.iloc[103, df.columns.get_loc("open")] = 105.0       # gap UP through the short's stop (101): filled at the open, a loss
        df.iloc[103, df.columns.get_loc("high")] = 106.0
        t = simulate_day("X", df, [Signal(100, -1, 1.0, 0.0)], Costs(0, 0))[0]
        self.assertEqual(t.reason, "stop")
        self.assertAlmostEqual(t.exit_px, 105.0)
        self.assertAlmostEqual(t.pnl_per_share(Costs(0, 0)), -5.0)

    def test_short_slippage_and_fees_are_adverse(self):
        df = flat_day()
        t = simulate_day("X", df, [Signal(100, -1, 1.0, 0.0, 10)], Costs(10, 0))[0]     # 10 bps, time exit on a flat tape
        self.assertAlmostEqual(t.entry_px, 100.0 * (1 - 0.001))    # sold lower
        self.assertAlmostEqual(t.exit_px, 100.0 * (1 + 0.001))     # bought back higher
        self.assertLess(t.pnl_per_share(Costs(10, 0.4)), t.pnl_per_share(Costs(10, 0)))

    def test_short_with_managed_exit_trails_from_the_low(self):
        df = flat_day()
        for k in range(102, 110):                               # steady fall, then a bounce through the trail
            for col in ("open", "high", "low", "close"):
                df.iloc[k, df.columns.get_loc(col)] = 100.0 - (k - 101) * 0.5
        for col, v in (("open", 96.5), ("high", 99.0), ("low", 96.5), ("close", 98.5)):
            df.iloc[110, df.columns.get_loc(col)] = v
        t = simulate_day("X", df, [Signal(100, -1, 1.5, 0.0, 0, "s", True, 1.0, 20.0, 1.5, 9.0)], Costs(0, 0))[0]
        self.assertEqual(t.side, -1)
        self.assertGreater(t.pnl_per_share(Costs(0, 0)), 0)     # trailed down, closed in profit on the bounce


def trade(day, sym="X", sd=1.0, pnl_ps=0.5, minute=0):
    t0 = pd.Timestamp(f"{day} 10:{minute:02d}", tz=NY)
    return Trade(sym, t0, t0 + pd.Timedelta(minutes=30), 100.0, 100.0 + pnl_ps, sd, "time", 30)


class PortfolioTests(unittest.TestCase):
    costs = Costs(0, 0)

    def test_risk_sizing_and_notional_cap(self):
        pf = Portfolio(equity=100_000, risk_pct=0.005, max_pos_frac=0.25)
        fills = size_intraday([trade("2024-03-04", sd=5.0)], self.costs, pf)
        self.assertEqual(int(fills.qty.iloc[0]), 100)                      # risk: 0.5% of 100k = $500 / $5 stop
        fills = size_intraday([trade("2024-03-04", sd=0.01)], self.costs, pf)
        self.assertEqual(int(fills.qty.iloc[0]), 250)                      # capped at 25% notional (25,000 / $100)

    def test_daily_loss_limit_stops_new_trades(self):
        pf = Portfolio(equity=100_000, risk_pct=0.01, daily_loss_limit=0.01, max_positions=1, max_pos_frac=1.0)
        ts = [trade("2024-03-04", pnl_ps=-1.0, minute=m) for m in (0, 40, 50)]
        ts = [Trade(t.symbol, t.entry_time, t.exit_time, 100, 99, 1, "stop", 1) for t in ts]
        fills = size_intraday(ts, self.costs, pf)
        self.assertEqual(len(fills), 1)                                    # the first loss hits 1% of equity; no more trades that day

    def test_pdt_blocks_the_fourth_day_trade_under_25k(self):
        pf = Portfolio(equity=20_000, pdt=True)
        ts = [trade(f"2024-03-0{d}") for d in (4, 5, 6, 7)]
        self.assertEqual(len(size_intraday(ts, self.costs, pf)), 3)
        self.assertEqual(len(size_intraday(ts, self.costs, Portfolio(equity=30_000, pdt=True))), 4)


def swing_frame():
    idx = pd.date_range("2022-01-03", periods=320, freq="B", tz=NY)
    c = 100 + np.arange(320) * 0.2
    c[300] -= 6.0
    c[301] -= 6.5
    return pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1e6}, index=idx)


class SwingTests(unittest.TestCase):
    def test_entry_is_next_open_after_signal_and_exit_after_bounce(self):
        d = swing_frame()
        s = RsiPullback(rsi_entry=15, exit_sma=3)
        trades = simulate_swing("X", d, s, Costs(0, 0))
        self.assertTrue(trades)
        f = s.frame(d)
        sig_days = f.index[f["entry"]]
        t = trades[0]
        self.assertEqual(t.entry_time, d.index[d.index.get_loc(sig_days[sig_days < t.entry_time][-1]) + 1])
        self.assertAlmostEqual(t.entry_px, float(d.loc[t.entry_time, "open"]))
        self.assertGreater(t.exit_time, t.entry_time)

    def test_swing_portfolio_marks_to_market(self):
        d = swing_frame()
        trades = simulate_swing("X", d, RsiPullback(rsi_entry=15, exit_sma=3), Costs(0, 0))
        fills, curve = size_swing(trades, {"X": d["close"]}, Costs(0, 0), Portfolio(), d.index[0].date(), d.index[-1].date())
        self.assertEqual(len(curve), len(d))
        self.assertGreater(len(fills), 0)
        self.assertAlmostEqual(curve.iloc[0], 100_000)


class EndToEndTests(unittest.TestCase):
    def test_walk_forward_runs_and_reports_placebo(self):
        days = {"A": [make_day(f"2024-03-{d:02d}", seed=d, vol=0.0008) for d in range(4, 30) if date(2024, 3, d).weekday() < 5]}
        res = walk_forward("day", days, (date(2024, 3, 1), date(2024, 3, 15)), (date(2024, 3, 16), date(2024, 3, 31)), min_trades=1, placebo_runs=3)
        self.assertIn("grid", res)
        if res["chosen"]:
            self.assertIn("placebo", res)
            self.assertIn(verdict(res), ("NO EDGE FOUND", "NO RESULT", "PROMISING (paper trade next; not proof)"))

    def test_metrics_shape(self):
        pf = Portfolio()
        fills = size_intraday([trade("2024-03-04", pnl_ps=1.0), trade("2024-03-05", pnl_ps=-0.5)], Costs(0, 0), pf)
        m = metrics(fills, start_equity=pf.equity)
        self.assertEqual(m["trades"], 2)
        self.assertAlmostEqual(m["win_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- execution layer
from datetime import datetime, timedelta  # noqa: E402

from alpacabot import execution as X  # noqa: E402


class FakeBroker:
    def __init__(self, equity=100_000.0, bid=99.98, ask=100.00):
        self._equity, self.bid, self.ask, self.calls = equity, bid, ask, []

    def equity(self):
        return self._equity

    def quote(self, sym):
        return self.bid, self.ask

    def submit_bracket(self, sym, qty, stop, target, coid=None):
        self.calls.append(("bracket", sym, qty, stop, target))
        self.coids = getattr(self, "coids", []) + [coid]

    def close(self, sym):
        self.calls.append(("close", sym))

    def flatten_all(self):
        self.calls.append(("flatten",))


def et(h, m, day=(2026, 10, 12)):
    return datetime(*day, h, m)


class ExecutionTests(unittest.TestCase):
    def test_spread_check_stocks_and_crypto(self):
        self.assertTrue(X.spread_ok(99.98, 100.00, max_abs=0.05))
        self.assertFalse(X.spread_ok(99.90, 100.00, max_abs=0.05))
        self.assertFalse(X.spread_ok(0, 100.0))
        self.assertTrue(X.spread_ok(60000, 60020, max_pct=0.0005))          # 0.033%
        self.assertFalse(X.spread_ok(60000, 60060, max_pct=0.0005))         # 0.1%

    def test_volatility_normalized_size(self):
        calm, wild = X.position_size(100_000, 0.005, 100, 5.0), X.position_size(100_000, 0.005, 100, 20.0)
        self.assertEqual((calm, wild), (100, 25))                            # a 4x wider stop gets 4x fewer shares: $500 at risk either way
        self.assertEqual(X.position_size(100_000, 0.005, 100, 0.5), 250)     # ...until the 25% notional cap binds
        self.assertEqual(X.position_size(100_000, 0.005, 100, 0.0), 0)

    def test_entry_window(self):
        w = X.EntryWindow()
        self.assertFalse(w.entries_allowed(et(9, 40)))                       # opening minutes
        self.assertTrue(w.entries_allowed(et(9, 45)))
        self.assertFalse(w.entries_allowed(et(15, 45)))
        self.assertFalse(w.entries_allowed(et(10, 0, (2026, 10, 10))))       # Saturday
        self.assertTrue(w.must_flatten(et(15, 55)))

    def test_circuit_breaker_trips_locks_24h_and_resets_next_day_only_after_lock(self):
        b = X.CircuitBreaker(limit=0.02)
        self.assertFalse(b.update(100_000, et(10, 0)))
        self.assertFalse(b.update(98_500, et(11, 0)))
        self.assertTrue(b.update(97_900, et(12, 0)))                         # -2.1% on the day
        self.assertFalse(b.entries_allowed(et(13, 0)))
        self.assertFalse(b.entries_allowed(et(9, 0, (2026, 10, 13))))        # next morning, still inside 24h
        self.assertTrue(b.entries_allowed(et(12, 1, (2026, 10, 13))))

    def test_stale_positions(self):
        held = {"A": et(10, 0), "B": et(10, 40)}
        self.assertEqual(X.stale_positions(held, et(10, 50), 45), ["A"])

    def test_retry_backs_off_then_succeeds_and_gives_up_cleanly(self):
        class E429(Exception):
            status_code = 429
        calls, waits = {"n": 0}, []

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise E429()
            return "ok"
        self.assertEqual(X.retry_call(flaky, sleep=waits.append), "ok")
        self.assertEqual(len(waits), 2)
        self.assertGreater(waits[1], waits[0] * 1.2)                          # exponential
        with self.assertRaises(E429):
            X.retry_call(lambda: (_ for _ in ()).throw(E429()), retries=2, sleep=lambda s: None)

        class E400(Exception):
            status_code = 400
        n = {"c": 0}

        def bad():
            n["c"] += 1
            raise E400()
        with self.assertRaises(E400):
            X.retry_call(bad, sleep=lambda s: None)
        self.assertEqual(n["c"], 1)                                           # a client error is not retried

    def test_rotating_log_is_size_capped(self):
        import logging.handlers
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            lg = X.setup_logging(Path(d) / "bot.log", max_bytes=2000, backups=2)
            for i in range(300):
                lg.warning("line %d %s", i, "x" * 50)
            files = list(Path(d).glob("bot.log*"))
            self.assertLessEqual(len(files), 3)
            self.assertLessEqual(sum(f.stat().st_size for f in files), 3 * 2200)
            for h in list(lg.handlers):
                if isinstance(h, logging.handlers.RotatingFileHandler):
                    h.close()
                    lg.removeHandler(h)

    def test_live_decision_matches_backtest_signal(self):
        df = make_day(seed=3, vol=0.0012)
        strat = VwapReversion(z_entry=1.0, max_trades=50)
        sigs = strat.signals(df)
        self.assertTrue(sigs)
        k = sigs[0].i
        got = X.live_decision(strat, df.iloc[: k + 1])
        self.assertIsNotNone(got)
        self.assertEqual((got.i, round(got.stop_dist, 6)), (k, round(sigs[0].stop_dist, 6)))
        self.assertIsNone(X.live_decision(strat, df.iloc[:k]))                # one bar earlier: nothing yet

    def test_executor_orders_with_checks_and_halts_on_wide_spread(self):
        b = FakeBroker()
        ex = X.Executor(b, sleep=lambda s: None)
        sig = Signal(100, 1, 0.5, 2.0)
        self.assertEqual(ex.try_enter("AAPL", sig, 100.0, et(9, 40)), "outside entry window")
        self.assertEqual(ex.try_enter("AAPL", sig, 100.0, et(10, 0)), "ordered")
        self.assertEqual(b.calls[-1], ("bracket", "AAPL", 250, 99.5, 101.0))   # 25% notional cap binds: 250 shares at $100
        self.assertEqual(ex.try_enter("AAPL", sig, 100.0, et(10, 0)), "duplicate order id")        # same signal replayed after a restart
        self.assertEqual(b.coids, ["ab-AAPL-20261012-100"])
        self.assertEqual(ex.try_enter("AAPL", sig, 100.0, et(10, 1)), "duplicate order id")       # same decision bar, same id
        self.assertEqual(ex.try_enter("AAPL", Signal(101, 1, 0.5, 2.0), 100.0, et(10, 1)), "position limit")
        b.bid, b.ask = 99.80, 100.00
        self.assertTrue(ex.try_enter("MSFT", sig, 100.0, et(10, 2)).startswith("spread too wide"))
        ex.kill_switch = True
        self.assertEqual(ex.try_enter("NVDA", sig, 100.0, et(10, 3)), "kill switch")

    def test_executor_housekeeping_flattens_on_breaker_close_and_stale(self):
        b = FakeBroker()
        ex = X.Executor(b, sleep=lambda s: None, stale_minutes=45)
        ex.on_tick(et(10, 0))
        ex.try_enter("AAPL", Signal(1, 1, 0.5, 2.0), 100.0, et(10, 0))
        self.assertEqual(ex.on_tick(et(10, 50)), ["stale exit AAPL"])
        self.assertIn(("close", "AAPL"), b.calls)
        ex.try_enter("AAPL", Signal(1, 1, 0.5, 2.0), 100.0, et(11, 0))
        b._equity = 97_000.0
        self.assertEqual(ex.on_tick(et(11, 5)), ["breaker: flattened"])
        self.assertEqual(b.calls[-1], ("flatten",))
        self.assertEqual(ex.try_enter("MSFT", Signal(1, 1, 0.5, 2.0), 100.0, et(11, 6)), "breaker lock")
        b2 = FakeBroker(); e2 = X.Executor(b2, sleep=lambda s: None)
        e2.try_enter("AAPL", Signal(1, 1, 0.5, 2.0), 100.0, et(10, 0))
        self.assertEqual(e2.on_tick(et(15, 55)), ["flattened for the close"])


# ---------------------------------------------------------------- dual regime + virtual exits
import json  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from alpacabot.exits import ExitState, StateStore, tp_multiple  # noqa: E402
from alpacabot.strategies import DualRegime  # noqa: E402


class ExitStateTests(unittest.TestCase):
    def test_regime_scaled_targets(self):
        self.assertEqual([tp_multiple(a) for a in (10, 24.9, 25, 34.9, 35, 60)], [1.8, 1.8, 3.0, 3.0, 4.5, 4.5])

    def test_break_even_lock_then_trailing_stop(self):
        st = ExitState.open(100.0, 1.0, adx=30)                 # stop 98.5, target 103.0
        self.assertAlmostEqual((st.stop, st.tp), (98.5, 103.0))
        st.ratchet(100.9)
        self.assertAlmostEqual(st.stop, 99.4)                   # trailing: 100.9 - 1.5 is above the old stop but break-even not yet reached
        self.assertFalse(st.be_done)
        st.ratchet(101.0)                                       # +1.0 ATR: stop to entry + 0.1 ATR
        self.assertTrue(st.be_done)
        self.assertAlmostEqual(st.stop, 100.1)
        st.ratchet(102.5)                                       # high-water mark 102.5: trail 1.5 behind = 101.0
        self.assertAlmostEqual(st.stop, 101.0)
        st.ratchet(101.5)                                       # a lower high never loosens the stop
        self.assertAlmostEqual(st.stop, 101.0)

    def test_stop_beats_target_and_gaps_fill_at_the_open(self):
        st = ExitState.open(100.0, 1.0, adx=30)
        self.assertEqual(st.check(100.0, 104.0, 98.0)[0], "stop")
        self.assertEqual(st.check(95.0, 96.0, 94.0), ("stop", 95.0))        # gap down through the stop
        self.assertEqual(st.check(104.0, 105.0, 103.5), ("target", 104.0))   # gap up through the target
        self.assertIsNone(st.check(100.0, 101.0, 99.5))

    def test_levels_set_before_a_bar_are_used_for_that_bar(self):
        st = ExitState.open(100.0, 1.0, adx=30)
        # one bar that spikes to 103 (target) then collapses: the target level existed before the bar, so it exits at the target
        self.assertEqual(st.check(100.0, 103.2, 99.9), ("target", 103.0))

    def test_json_round_trip_and_atomic_corruption_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d) / "state.json")
            pos = {"AAPL": {"state": ExitState.open(100, 1, 40), "qty": 25, "opened": "2026-10-12T10:00:00"}}
            store.save(pos)
            back = store.load()
            self.assertEqual(back["AAPL"]["state"], pos["AAPL"]["state"])
            self.assertEqual(back["AAPL"]["qty"], 25)
            (Path(d) / "state.json").write_text("{not json")
            self.assertEqual(store.load(), {})                                       # corrupt file: start clean, keep the evidence
            self.assertTrue((Path(d) / "state.corrupt").exists())


class VirtualManagerTests(unittest.TestCase):
    def test_polling_ratchets_exits_and_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            broker = FakeBroker()
            store = StateStore(Path(d) / "s.json")
            m = X.VirtualExitManager(store, broker, sleep=lambda s: None)
            m.open("AAPL", 10, 100.0, 1.0, 30, et(10, 0))
            self.assertIsNone(m.on_quote("AAPL", 101.2, 101.3))                      # +1.2 ATR: break-even lock engages
            self.assertAlmostEqual(m.positions["AAPL"]["state"].stop, 100.1)
            m2 = X.VirtualExitManager(StateStore(Path(d) / "s.json"), broker, sleep=lambda s: None)   # "reboot"
            self.assertAlmostEqual(m2.positions["AAPL"]["state"].stop, 100.1)
            self.assertEqual(m2.on_quote("AAPL", 100.0, 100.05), "stop")             # price falls back through the locked stop
            self.assertIn(("close", "AAPL"), broker.calls)
            self.assertNotIn("AAPL", m2.positions)
            self.assertEqual(json.loads((Path(d) / "s.json").read_text()), {})


class ManagedBacktestTests(unittest.TestCase):
    def test_managed_exit_locks_break_even_in_the_engine(self):
        df = flat_day()
        # bars 102.. rise to +1.3 ATR then fall back below entry: the break-even lock should exit near entry, not at the original stop
        o = df["open"].to_numpy().copy(); h = df["high"].to_numpy().copy(); l = df["low"].to_numpy().copy(); c = df["close"].to_numpy().copy()
        for j in (103, 104):                       # price jumps to +1.3 ATR and holds there: the lock engages
            o[j], h[j], l[j], c[j] = 101.2, 101.4, 101.1, 101.3
        o[105], h[105], l[105], c[105] = 101.0, 101.1, 99.0, 99.2      # then it collapses through the locked stop
        df = df.assign(open=o, high=h, low=l, close=c)
        sig = Signal(100, 1, 1.5, 0.0, 0, "t", True, 1.0, 30.0, 1.5, 1.0)
        t = simulate_day("X", df, [sig], Costs(0, 0))[0]
        self.assertEqual(t.reason, "stop")
        self.assertAlmostEqual(t.exit_px, 100.1)                     # break-even + 0.1 ATR, not the original -1.5 ATR stop at 98.5

    def test_dual_regime_signals_are_causal_and_fire_on_transitions_only(self):
        for seed in range(4):
            raw = make_day(seed=seed, vol=0.0015, n=390)
            full = raw.join(DualRegime.features(raw))
            strat = DualRegime(max_trades=50)
            idx = [s.i for s in strat.signals(full)]
            for cut in (150, 250, 330):
                part = [s.i for s in strat.signals(full.iloc[:cut])]
                self.assertEqual(part, [i for i in idx if i < cut][: len(part)])
            self.assertEqual(len(idx), len(set(idx)))
            for s in strat.signals(full):
                self.assertTrue(s.managed and s.atr > 0 and s.stop_dist > 0)


# ---------------------------------------------------------------- experts + brain
from unittest import mock  # noqa: E402

from alpacabot.brain import Brain  # noqa: E402
from alpacabot.experts import ALL_EXPERTS, Experts, features_all  # noqa: E402


class ExpertTests(unittest.TestCase):
    def test_every_expert_is_causal(self):
        for seed in range(3):
            raw = make_day(seed=seed, vol=0.0012, n=390)
            full = raw.join(features_all(raw))
            ex = Experts(("09:35", "15:55"))
            for name in ALL_EXPERTS:
                idx = [s.i for _, s in ex.signals(full, [name])]
                for cut in (150, 260, 340):
                    part = [s.i for _, s in ex.signals(full.iloc[:cut], [name])]
                    self.assertEqual(part, [i for i in idx if i < cut], (name, seed, cut))

    def test_each_expert_ships_a_stop_and_an_exit(self):
        raw = make_day(seed=5, vol=0.002, n=390)
        full = raw.join(features_all(raw))
        for name, s in Experts(("09:35", "15:55")).signals(full):
            self.assertGreater(s.stop_dist, 0, name)
            self.assertTrue(s.target_r > 0 or s.exit_col or s.managed, name)
            if s.exit_col:
                self.assertIn(s.exit_col, full.columns)


class BrainRuleTests(unittest.TestCase):
    @staticmethod
    def row(ratio=1.0, adx=30.0, mtf=True):
        return pd.DataFrame({"f_atr_ratio": [ratio], "f_adx": [adx], "f_mtf_up": [mtf]})

    def test_volatility_circuit_breaker(self):
        b = Brain(route=False, mtf=False)
        self.assertTrue(b._allowed(self.row(1.0), "connors_rsi2", 0))
        self.assertFalse(b._allowed(self.row(3.0), "connors_rsi2", 0))      # spike
        self.assertFalse(b._allowed(self.row(0.2), "connors_rsi2", 0))      # flat / illiquid
        self.assertFalse(b._allowed(self.row(float("nan")), "connors_rsi2", 0))

    def test_regime_router_and_higher_timeframe_filter(self):
        b = Brain(route=True, mtf=True)
        self.assertTrue(b._allowed(self.row(adx=30), "donchian_turtle", 0))
        self.assertFalse(b._allowed(self.row(adx=18), "donchian_turtle", 0))   # trend expert in chop
        self.assertFalse(b._allowed(self.row(adx=30), "connors_rsi2", 0))      # mean reversion in a strong trend
        self.assertTrue(b._allowed(self.row(adx=18), "connors_rsi2", 0))
        self.assertFalse(b._allowed(self.row(adx=30, mtf=False), "donchian_turtle", 0))
        self.assertTrue(Brain(route=False, mtf=False)._allowed(self.row(adx=18), "donchian_turtle", 0))

    def test_consensus_needs_two_different_experts_and_priority_picks_one(self):
        d = flat_day().join(pd.DataFrame({"f_atr_ratio": 1.0, "f_adx": 30.0, "f_mtf_up": True, "f_atr": 1.0}, index=flat_day().index))
        fake = [("williams_r", Signal(100, 1, 1.0, 2.0, 0, "williams_r")), ("connors_rsi2", Signal(101, 1, 1.0, 2.0, 0, "connors_rsi2")),
                ("williams_r", Signal(200, 1, 1.0, 2.0, 0, "williams_r"))]
        with mock.patch.object(Experts, "signals", return_value=fake):
            one = Brain(route=False, mtf=False, consensus=1, priority=["connors_rsi2", "williams_r"]).signals(d)
            two = Brain(route=False, mtf=False, consensus=2, window=3).signals(d)
        self.assertEqual([s.i for s in one], [100, 101, 200])
        self.assertEqual([s.i for s in two], [101])          # only bar 101 has two distinct experts inside the 3-bar window


class IndicatorExitTests(unittest.TestCase):
    def test_exit_on_indicator_fills_at_next_open(self):
        df = flat_day()
        df["x"] = False
        df.iloc[110, df.columns.get_loc("x")] = True
        df.iloc[111, df.columns.get_loc("open")] = 100.4
        t = simulate_day("X", df, [Signal(100, 1, 5.0, 0.0, 0, "t", exit_col="x")], Costs(0, 0))[0]
        self.assertEqual((t.reason, t.exit_time), ("signal", df.index[111]))
        self.assertAlmostEqual(t.exit_px, 100.4)

    def test_cooldown_blocks_an_immediate_reentry(self):
        df = flat_day()
        df.iloc[105, df.columns.get_loc("high")] = 103.0               # target hit at bar 105 (exit)
        sigs = [Signal(100, 1, 1.0, 2.0), Signal(106, 1, 1.0, 2.0)]
        self.assertEqual(len(simulate_day("X", df, sigs, Costs(0, 0), cooldown=0)), 2)
        self.assertEqual(len(simulate_day("X", df, sigs, Costs(0, 0), cooldown=3)), 1)


class CredentialTests(unittest.TestCase):
    def test_any_common_variable_name_works_and_nothing_is_printed(self):
        import os
        from alpacabot.data import credentials
        names = ["ALPACA_DATA_KEY_ID", "ALPACA_API_KEY", "ALPACA_KEY_ID", "APCA_API_KEY_ID", "ALPACA_DATA_SECRET_KEY", "ALPACA_SECRET_KEY",
                 "ALPACA_API_SECRET", "ALPACA_SECRET", "APCA_API_SECRET_KEY"]
        saved = {n: os.environ.pop(n, None) for n in names}
        try:
            self.assertEqual(credentials(), ("", ""))
            os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"] = " k1 ", "s1"
            self.assertEqual(credentials(), ("k1", "s1"))
            os.environ["ALPACA_DATA_KEY_ID"] = "k0"
            self.assertEqual(credentials()[0], "k0")                       # the dedicated name wins
        finally:
            for n in names:
                os.environ.pop(n, None)
                if saved[n] is not None:
                    os.environ[n] = saved[n]


class PaperRunnerTests(unittest.TestCase):
    def test_runner_paper_only_and_reconciles(self):
        import tempfile
        from alpacabot import paper

        class B:
            def positions(self): return {}
            def equity(self): return 100000.0
            def quote(self, s): return (100.0, 100.02)
            def submit_market(self, s, q, c=None): return {}
            def close(self, s): pass
            def flatten_all(self): pass
            def clock_open(self): return True

        with tempfile.TemporaryDirectory() as d:
            r = paper.PaperRunner(B(), state_dir=d, sleep=lambda *_: None, fetch=lambda *a, **k: pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
            r.exits.positions["SPY"] = {"state": ExitState.open(100, 1, 20), "qty": 10, "opened": "2026-10-09T10:00:00-04:00"}
            r.reconcile()
            self.assertEqual(r.exits.positions, {})                       # broker holds nothing -> dropped
            r.on_bar_close(datetime(2026, 10, 12, 10, 30, tzinfo=paper.ET))  # no bars -> no orders, no crash
            self.assertIn("paper-api.alpaca.markets", paper.TRADE_URL)
            self.assertNotIn("api.alpaca.markets/v2/orders", paper.TRADE_URL.replace("paper-api", ""))

    def test_report_card_and_gate(self):
        import tempfile
        from alpacabot import paper
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "trades.jsonl").write_text("\n".join(json.dumps({"date": "2026-10-12", "symbol": "SPY", "qty": 10, "entry": 100, "exit": 101, "pnl": 10, "r": 0.5, "reason": "target"}) if i % 2 == 0
                                                         else json.dumps({"date": "2026-10-12", "symbol": "AMD", "qty": 10, "entry": 100, "exit": 99, "pnl": -10, "r": -0.5, "reason": "stop"}) for i in range(4)))
            (d / "equity.jsonl").write_text(json.dumps({"date": "2026-10-11", "equity": 100000, "spy": 500}) + "\n" + json.dumps({"date": "2026-10-12", "equity": 100500, "spy": 501}) + "\n")
            t = paper.report_card(d, date(2026, 10, 12))
            self.assertIn("2W/2L", t)
            self.assertIn("+0.50%", t)
            self.assertIn("GATE NOT MET", t)                     # 2 days and 4 trades cannot pass
            self.assertIn("PAPER account only", t)
        self.assertIn("No equity snapshots", paper.report_card(tempfile.mkdtemp(), date(2026, 10, 12)))

    def test_shared_account_only_touches_own_shares(self):
        import tempfile
        from alpacabot import paper

        class B:
            def __init__(self): self.closed, self.cancelled = [], False
            def positions(self): return {"SPY": 110, "XOM": 50}          # 100 SPY + all XOM belong to the other bots
            def equity(self): return 54000.0
            def quote(self, s): return (101.0, 101.02)
            def submit_market(self, s, q, c=None): return {}
            def close(self, s, qty=None): self.closed.append((s, qty))
            def flatten_all(self): self.cancelled = True                  # account-wide: must never be called
            def clock_open(self): return True

        with tempfile.TemporaryDirectory() as d:
            b = B()
            r = paper.PaperRunner(b, state_dir=d, sleep=lambda *_: None)
            r.exits.positions["SPY"] = {"state": ExitState.open(100, 1, 20), "qty": 10, "opened": "2026-10-12T10:00:00-04:00"}
            r.exec.opened["SPY"] = datetime(2026, 10, 12, 10, 0)
            r.tick(datetime(2026, 10, 12, 15, 56, tzinfo=paper.ET))          # flatten time
            self.assertEqual(b.closed, [("SPY", 10)])                          # only our 10 shares
            self.assertFalse(b.cancelled)
            self.assertEqual(r.exits.positions, {})
            t = paper._jl(Path(d) / "trades.jsonl")
            self.assertEqual((t[0]["reason"], t[0]["pnl"]), ("flatten", 10.0))
            self.assertAlmostEqual(r.own_equity(), 54010.0)                   # own book, not the shared account


class FlatBy350Tests(unittest.TestCase):
    def test_backtest_days_end_350_and_live_flattens_350(self):
        idx = pd.date_range("2026-10-12 09:30", "2026-10-12 15:59", freq="1min", tz="America/New_York")
        df = pd.DataFrame({"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 1000.0}, index=idx)
        one = split_days({"SPY": df})["SPY"][0]
        five = split_days({"SPY": df}, minutes=5)["SPY"][0]
        self.assertEqual(one.index[-1].strftime("%H:%M"), "15:49")      # 1-min bar 15:49-15:50 is the last
        self.assertEqual(five.index[-1].strftime("%H:%M"), "15:45")     # 5-min bar 15:45-15:50 is the last
        w = X.EntryWindow()
        self.assertFalse(w.must_flatten(datetime(2026, 10, 12, 15, 49)))
        self.assertTrue(w.must_flatten(datetime(2026, 10, 12, 15, 50)))


class HeatMapAndShortTests(unittest.TestCase):
    def _daily(self, drift):
        idx = pd.date_range("2024-01-02", periods=80, freq="B", tz="America/New_York")
        rng = np.random.default_rng(1)
        c = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.003, len(idx))))
        return pd.DataFrame({"open": c, "high": c * 1.002, "low": c * 0.998, "close": c, "volume": 1e6}, index=idx)

    def test_heat_state_direction_and_no_lookahead(self):
        from alpacabot.gates import heat_state
        from alpacabot.gates import state_on
        down, up = heat_state(self._daily(-0.004)), heat_state(self._daily(0.004))
        self.assertEqual(down.iloc[-1], -1)
        self.assertEqual(up.iloc[-1], 1)
        d = self._daily(-0.004)
        last = d.index[-1].date()
        with_today, without_today = heat_state(d), heat_state(d.iloc[:-1])
        # the state used ON the last day is the same whether or not that day's (partial) bar exists, and it is not NEUTRAL by accident
        self.assertEqual(state_on(with_today, last), state_on(without_today, last))
        self.assertEqual(state_on(without_today, last), -1)
        self.assertEqual(state_on(with_today, d.index[0].date()), 0)       # nothing before the first close

    def test_gates_block_longs_and_allow_shorts_only_when_bearish(self):
        from alpacabot import gates
        day = pd.date_range("2024-04-23 09:30", periods=10, freq="5min", tz="America/New_York")
        d = pd.DataFrame({"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 1.0}, index=day)
        prev = (day[0] - pd.Timedelta(days=1)).date()          # states are keyed by the close they were computed at
        bear = pd.Series({prev: -1.0})
        neutral = pd.Series({prev: 0.0})
        g = gates.apply(d.copy(), "NVDA", {"XLK": bear, "SPY": neutral})
        self.assertFalse(g["g_long_ok"].all())
        self.assertTrue(g["g_short_ok"].all())
        g = gates.apply(d.copy(), "NVDA", {"XLK": neutral, "SPY": neutral})
        self.assertTrue(g["g_long_ok"].all())
        self.assertFalse(g["g_short_ok"].any())
        g = gates.apply(d.copy(), "AMZN", {"XLY": neutral, "SPY": bear})       # market bearish: shorts allowed everywhere
        self.assertTrue(g["g_short_ok"].all())

    def test_brain_shorts_only_through_the_gate(self):
        from alpacabot.experts import features_both, mirror
        idx = pd.date_range("2024-03-04 09:30", periods=78 * 6, freq="5min", tz="America/New_York")
        idx = idx[(idx.time >= pd.Timestamp("09:30").time()) & (idx.time <= pd.Timestamp("15:45").time())]
        rng = np.random.default_rng(3)
        c = 100 + np.cumsum(rng.normal(0, 0.15, len(idx)))
        df = pd.DataFrame({"open": np.r_[c[0], c[:-1]], "high": c + 0.1, "low": c - 0.1, "close": c, "volume": 1000.0}, index=idx)
        df["high"] = df[["open", "high", "close"]].max(axis=1)
        df["low"] = df[["open", "low", "close"]].min(axis=1)
        full = df.join(features_both(df))
        day = full[full.index.date == full.index[-1].date()].copy()
        b = Brain(experts=[], short_experts=list(ALL_EXPERTS), route=False, mtf=False)
        day["g_long_ok"], day["g_short_ok"], day["g_spy_red"] = True, False, False
        self.assertEqual(b.signals(day), [])                              # gate closed: no shorts at all
        day["g_short_ok"] = True
        sigs = b.signals(day)
        self.assertTrue(sigs and all(s.side == -1 and s.tag.startswith("S:") for s in sigs))
        # a short on df is exactly a long on the mirrored series
        m = mirror(df)
        mday = m.join(features_both(m)).loc[day.index]
        long_on_mirror = Brain(experts=list(ALL_EXPERTS), route=False, mtf=False).signals(mday)
        self.assertEqual([s.i for s in sigs], [s.i for s in long_on_mirror])
        day["g_short_ok"], day["g_spy_red"] = False, True
        self.assertEqual(Brain(experts=[], short_experts=list(ALL_EXPERTS), route=False, mtf=False, short_gate="red").signals(day) != [], True)

    def test_paper_short_round_trip(self):
        import tempfile
        from alpacabot import paper

        class B:
            def __init__(self): self.closed, self.px = [], (100.0, 100.02)
            def positions(self): return {"TSLA": -10}
            def equity(self): return 50000.0
            def quote(self, s): return self.px
            def submit_market(self, s, q, c=None, side="buy"): return {}
            def close(self, s, qty=None): self.closed.append((s, qty))
            def clock_open(self): return True

        with tempfile.TemporaryDirectory() as d:
            b = B()
            r = paper.PaperRunner(b, state_dir=d, sleep=lambda *_: None)
            r.exits.open("TSLA", 10, 100.0, 1.0, 20.0, datetime(2026, 10, 12, 10, 0, tzinfo=paper.ET), 1.5, 1.0, side=-1)   # short at 100, stop 101.5
            b.px = (99.0, 99.02)                       # falls: the short is winning, trail ratchets down
            r.poll_exits(datetime(2026, 10, 12, 10, 5, tzinfo=paper.ET))
            self.assertIn("TSLA", r.exits.positions)
            self.assertAlmostEqual(r.own_equity(), 50000.0 + (100.0 - 99.02) * 10)
            b.px = (101.9, 102.0)                      # rips through the stop: buy back at the ask
            r.poll_exits(datetime(2026, 10, 12, 10, 10, tzinfo=paper.ET))
            self.assertEqual(b.closed, [("TSLA", 10)])
            t = paper._jl(Path(d) / "trades.jsonl")[0]
            self.assertEqual((t["side"], t["reason"]), ("short", "stop"))
            self.assertAlmostEqual(t["pnl"], -20.0)    # (100 - 102) * 10
            self.assertIn("shorts: 1, 0W/1L", paper.report_card(d, date(2026, 10, 12)))


class EngagedConfigTests(unittest.TestCase):
    def test_engaged_config_is_applied_and_unknown_keys_ignored(self):
        import tempfile
        from unittest import mock
        from alpacabot import paper
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "brain_config.json"
            cfg.write_text(json.dumps({"validated": "2021-2022", "brain": {"experts": ["connors_rsi2"], "short_experts": [], "route": False,
                                                                           "consensus": 2, "rm -rf": 1}}))
            with mock.patch.object(paper, "ENGAGED", cfg):
                b = paper.default_brain(d)
            self.assertEqual((b.experts, b.short_experts, b.route, b.consensus), (["connors_rsi2"], [], False, 2))
            with mock.patch.object(paper, "ENGAGED", Path(d) / "missing.json"):
                b = paper.default_brain(d)                  # no engaged file: the default (all experts both ways, gated)
            self.assertEqual(len(b.experts), 11)


class StaircaseModeTests(unittest.TestCase):
    def test_staircase_mode_puts_every_expert_on_the_trailing_manager(self):
        idx = pd.DatetimeIndex([t for d in pd.bdate_range("2024-03-04", periods=5)
                                for t in pd.date_range(f"{d.date()} 09:30", f"{d.date()} 15:45", freq="5min", tz="America/New_York")])
        rng = np.random.default_rng(5)
        c = 100 + np.cumsum(rng.normal(0, 0.15, len(idx)))
        df = pd.DataFrame({"open": np.r_[c[0], c[:-1]], "high": c + 0.1, "low": c - 0.1, "close": c, "volume": 1000.0}, index=idx)
        df["high"], df["low"] = df[["open", "high", "close"]].max(axis=1), df[["open", "low", "close"]].min(axis=1)
        d = df.join(features_all(df))
        day = d[d.index.date == d.index[-1].date()]
        fixed = [n for n in ALL_EXPERTS if n not in ("donchian_turtle", "dual_regime")]      # the 8 experts with their own exits
        own = Brain(experts=fixed, route=False, mtf=False).signals(day)
        st = Brain(experts=fixed, route=False, mtf=False, exit_mode="staircase").signals(day)
        self.assertEqual([s.i for s in own], [s.i for s in st])              # same entries
        self.assertTrue(st and all(s.managed and s.atr > 0 and s.max_bars == 0 and not s.exit_col and
                                   abs(s.stop_dist - 1.5 * s.atr) < 1e-9 for s in st))
        self.assertTrue(own and not any(s.managed for s in own))             # the default keeps each expert's own exits


class SessionSafetyTests(unittest.TestCase):
    def test_early_close_day_flattens_ten_minutes_before_the_real_close(self):
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        w = X.EntryWindow(session_close=datetime(2026, 11, 27, 13, 0, tzinfo=et))          # day after Thanksgiving
        self.assertTrue(w.entries_allowed(datetime(2026, 11, 27, 12, 30, tzinfo=et)))
        self.assertFalse(w.entries_allowed(datetime(2026, 11, 27, 12, 35, tzinfo=et)))
        self.assertFalse(w.must_flatten(datetime(2026, 11, 27, 12, 49, tzinfo=et)))
        self.assertTrue(w.must_flatten(datetime(2026, 11, 27, 12, 50, tzinfo=et)))
        normal = X.EntryWindow(session_close=datetime(2026, 11, 30, 16, 0, tzinfo=et))     # a normal day keeps 3:35 / 3:50
        self.assertTrue(normal.entries_allowed(datetime(2026, 11, 30, 15, 35, tzinfo=et)))
        self.assertFalse(normal.must_flatten(datetime(2026, 11, 30, 15, 49, tzinfo=et)))
        # the backtest cuts an early-close day at the same time
        idx = pd.date_range("2026-11-27 09:30", "2026-11-27 12:59", freq="1min", tz="America/New_York")
        df = pd.DataFrame({"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 1.0}, index=idx)
        self.assertEqual(split_days({"S": df}, minutes=5)["S"][0].index[-1].strftime("%H:%M"), "12:45")

    def test_kill_switch_file_stops_new_entries(self):
        import tempfile
        from alpacabot import paper

        class B:
            def positions(self): return {}
            def equity(self): return 50000.0
            def quote(self, s): return (100.0, 100.0)
            def clock(self): return {"is_open": True, "next_close": "2026-10-12T20:00:00Z"}
            def clock_open(self): return True

        with tempfile.TemporaryDirectory() as d:
            r = paper.PaperRunner(B(), state_dir=d, sleep=lambda *_: None)
            now = datetime(2026, 10, 12, 10, 0, tzinfo=paper.ET)
            r.tick(now)
            self.assertFalse(r.exec.kill_switch)
            (Path(d) / "KILL_SWITCH").write_text("stop")
            r.tick(now)
            self.assertTrue(r.exec.kill_switch)
            self.assertEqual(r.exec.try_enter("SPY", Signal(5, 1, 1.0, 2.0), 100.0, now), "kill switch")
            (Path(d) / "KILL_SWITCH").unlink()
            r.tick(now)
            self.assertFalse(r.exec.kill_switch)


class HistoryReportTests(unittest.TestCase):
    def test_bot_labels_round_trips_styles_and_pnl(self):
        from alpacabot.history import bot_label, round_trips, summarize
        self.assertEqual(bot_label("sx-AAPL-1"), "sx")
        self.assertEqual(bot_label("ab-SPY-20261012-40"), "ab")
        self.assertEqual(bot_label("3f2b8c1e-1234-4abc-9def-0123456789ab"), "untagged")
        t = lambda s: pd.Timestamp(s, tz="America/New_York").tz_convert("UTC")      # noqa: E731
        fills = pd.DataFrame([
            {"bot": "sx", "symbol": "AAPL", "time": t("2026-10-09 10:00"), "side": "buy", "qty": 10, "price": 100.0},
            {"bot": "sx", "symbol": "AAPL", "time": t("2026-10-09 10:10"), "side": "sell", "qty": 10, "price": 101.0},   # scalp +10
            {"bot": "xp", "symbol": "NVDA", "time": t("2026-10-09 10:00"), "side": "sell", "qty": 5, "price": 200.0},    # short
            {"bot": "xp", "symbol": "NVDA", "time": t("2026-10-09 14:00"), "side": "buy", "qty": 5, "price": 204.0},     # day -20
            {"bot": "sw", "symbol": "KO", "time": t("2026-10-08 15:00"), "side": "buy", "qty": 20, "price": 60.0},
            {"bot": "sw", "symbol": "KO", "time": t("2026-10-09 10:00"), "side": "sell", "qty": 12, "price": 61.0},      # swing +12, 8 still open
            {"bot": "sx", "symbol": "TSLA", "time": t("2026-10-09 11:00"), "side": "buy", "qty": 4, "price": 50.0},
            {"bot": "sx", "symbol": "TSLA", "time": t("2026-10-09 11:20"), "side": "sell", "qty": 6, "price": 49.0},     # closes 4 (-4), opens short 2
        ])
        rt = round_trips(fills)
        got = {(r.bot, r.symbol, r.side, r.style): round(r.pnl, 6) for r in rt.itertuples()}
        self.assertEqual(got, {("sx", "AAPL", "long", "scalp"): 10.0, ("xp", "NVDA", "short", "day"): -20.0,
                               ("sw", "KO", "long", "swing"): 12.0, ("sx", "TSLA", "long", "scalp"): -4.0})
        txt = summarize(rt, fills)
        self.assertIn("BY BOT", txt)
        self.assertIn("sw:KO +8", txt)
        self.assertIn("sx:TSLA -2", txt)


class UniverseTests(unittest.TestCase):
    def test_added_stock_joins_universe_and_heat_map(self):
        import tempfile
        from unittest import mock
        from alpacabot import gates, paper
        sector0, heat0 = dict(gates.SECTOR), list(gates.HEAT_ETFS)
        try:
            with tempfile.TemporaryDirectory() as d:
                cfg = Path(d) / "brain_config.json"
                cfg.write_text(json.dumps({"symbols": ["MU"], "sectors": {"MU": "XLK"}}))
                with mock.patch.object(paper, "ENGAGED", cfg):
                    syms = paper.engaged_symbols()
            self.assertIn("MU", syms)
            self.assertEqual(gates.SECTOR["MU"], "XLK")
            with self.assertRaises(ValueError):
                gates.add_sector("ABC", "NOTANETF")
        finally:
            gates.SECTOR.clear(); gates.SECTOR.update(sector0)
            gates.HEAT_ETFS[:] = heat0

    def test_universe_is_top5_per_sector_and_every_stock_rates_its_sector_etf(self):
        from unittest import mock
        from alpacabot import gates, paper, universe
        self.assertEqual(len(universe.SECTOR_TOP5), 11)
        self.assertTrue(all(len(v) == 5 for v in universe.SECTOR_TOP5.values()))
        self.assertEqual(len(set(universe.STOCKS)), 55)
        sector0, heat0 = dict(gates.SECTOR), list(gates.HEAT_ETFS)
        try:
            with mock.patch.object(paper, "ENGAGED", Path("/nonexistent/brain_config.json")):
                syms = paper.engaged_symbols()
            self.assertEqual(set(syms), set(universe.STOCKS) | {"SPY", "QQQ", "IWM"})
            self.assertEqual(gates.SECTOR["XOM"], "XLE")
            self.assertTrue(set(universe.SECTOR_ETFS) <= set(gates.HEAT_ETFS))
        finally:
            gates.SECTOR.clear(); gates.SECTOR.update(sector0)
            gates.HEAT_ETFS[:] = heat0


class LimitEntryTests(unittest.TestCase):
    def test_limit_fills_only_when_traded_through_at_the_limit_without_slippage(self):
        df = flat_day()                                            # flat at 100, lows 99.95
        c = Costs(10, 0, entry="limit", limit_offset=0.1, limit_wait=3)    # limit = 100 - 0.1 x 1.0 = 99.9: never traded through
        self.assertEqual(simulate_day("X", df, [Signal(100, 1, 1.0, 2.0, 10)], c), [])
        df.iloc[102, df.columns.get_loc("low")] = 99.8             # traded through on the 2nd bar after the signal
        t = simulate_day("X", df, [Signal(100, 1, 1.0, 2.0, 10)], c)[0]
        self.assertAlmostEqual(t.entry_px, 99.9)                    # at the limit, no slippage
        self.assertEqual(t.entry_time, df.index[102])
        df2 = flat_day()
        df2.iloc[104, df2.columns.get_loc("low")] = 99.0           # too late: outside the 3-bar wait
        self.assertEqual(simulate_day("X", df2, [Signal(100, 1, 1.0, 2.0, 10)], c), [])

    def test_short_limit_sells_above(self):
        df = flat_day()
        df.iloc[101, df.columns.get_loc("high")] = 100.2
        t = simulate_day("X", df, [Signal(100, -1, 1.0, 2.0, 10)], Costs(10, 0, entry="limit", limit_offset=0.1))[0]
        self.assertAlmostEqual(t.entry_px, 100.1)                   # a sell limit at 100 + 0.1 filled when price went through it
        self.assertEqual(t.side, -1)


class TaggedExitTests(unittest.TestCase):
    def test_exit_orders_carry_the_ab_prefix_and_the_right_side(self):
        from alpacabot import paper
        sent = []

        class R(paper.RestBroker):
            def __init__(self): pass
            def submit_market(self, symbol, qty, client_order_id=None, side="buy"): sent.append((symbol, qty, side, client_order_id))

        class Runner:
            class exits:
                positions = {"NVDA": {"qty": 59, "side": -1}, "AAPL": {"qty": 10, "side": 1}}
        book = paper.OwnBook(R(), Runner())
        book.close("NVDA")
        book.close("AAPL")
        self.assertEqual([(s, q, side) for s, q, side, _ in sent], [("NVDA", 59, "buy"), ("AAPL", 10, "sell")])
        self.assertTrue(all(c.startswith("ab-x-") for *_, c in sent))

    def test_report_card_lists_each_symbol_once(self):
        import tempfile
        from alpacabot import paper
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "trades.jsonl").write_text(json.dumps({"date": "2026-10-12", "symbol": "NVDA", "side": "short", "qty": 59, "entry": 227.4,
                                                              "exit": 228.2, "pnl": -47.0, "r": -1.05, "reason": "stop"}) + "\n")
            card = paper.report_card(d, date(2026, 10, 12))
        self.assertIn("By symbol: NVDA $-47\n", card + "\n")


class ExecutionTruthTests(unittest.TestCase):
    def test_junk_iex_quote_falls_back_to_last_trade(self):
        from alpacabot import paper

        class R(paper.RestBroker):
            def __init__(self, q): self.q = q
            def _req(self, method, url, **kw):
                class Resp:
                    def __init__(s, j): s._j = j
                    def json(s): return s._j
                return Resp({"quote": self.q} if "quotes" in url else {"trade": {"p": 380.05}})
        self.assertEqual(R({"bp": 378.9, "ap": 381.71}).quote("TSLA"), (380.05, 380.05))      # $2.81 wide on IEX
        self.assertEqual(R({"bp": 380.04, "ap": 380.06}).quote("TSLA"), (380.04, 380.06))     # a real 2-cent quote is kept
        self.assertEqual(R({"bp": 0, "ap": 381.0}).quote("TSLA"), (380.05, 380.05))

    def test_relative_spread_guard(self):
        self.assertTrue(X.spread_ok(538.50, 538.89, 1e9, 0.001))      # MSFT 39 cents = 0.07%: fine
        self.assertFalse(X.spread_ok(587.0, 589.0, 1e9, 0.001))       # 0.34%: blocked

    def test_rejected_entry_never_becomes_a_position(self):
        import tempfile
        from alpacabot import paper

        class B:
            def positions(self): return {}
            def equity(self): return 50000.0
            def quote(self, s): return (100.0, 100.0)
            def order(self, oid): return {"id": oid, "status": "rejected", "filled_qty": "0"}
        with tempfile.TemporaryDirectory() as d:
            r = paper.PaperRunner(B(), state_dir=d, sleep=lambda *_: None)
            self.assertEqual(r.confirm_fill({"id": "x1", "status": "new"}), ("rejected", 0, 0.0))
            self.assertEqual(r.confirm_fill({"id": "x2", "status": "filled", "filled_qty": "59", "filled_avg_price": "227.31"}),
                             ("filled", 59, 227.31))
            self.assertIsNone(r.confirm_fill(None))                    # simulations without order records keep the quote fill


class DayGuardRestartTests(unittest.TestCase):
    """A deploy mid-session must not unlock a profit-locked / breaker-locked day or move the day's opening-equity baseline."""
    class B:
        def positions(self): return {}
        def equity(self): return 50000.0
        def quote(self, s): return (100.0, 100.0)

    def test_locks_survive_restart(self):
        import tempfile
        from alpacabot import paper
        with tempfile.TemporaryDirectory() as d:
            r = paper.PaperRunner(self.B(), state_dir=d, sleep=lambda *_: None)
            now = datetime.now(paper.ET)
            pl = r.exec.profit_lock
            pl.update(50000.0, now)
            pl.update(50400.0, now)
            self.assertTrue(pl.update(50150.0, now))                     # gave back half: locked
            r.exec.breaker.update(50000.0, now)
            r.save_day_guards()
            r2 = paper.PaperRunner(self.B(), state_dir=d, sleep=lambda *_: None)
            self.assertFalse(r2.exec.profit_lock.entries_allowed(now))
            self.assertEqual(r2.exec.breaker.day_start_equity, 50000.0)
            self.assertEqual(r2.exec.profit_lock.peak_gain, 400.0)

    def test_first_run_reads_todays_lock_from_the_journal(self):
        import tempfile
        from alpacabot import paper
        with tempfile.TemporaryDirectory() as d:
            now = datetime.now(paper.ET)
            (Path(d) / "journal.jsonl").write_text(json.dumps({"t": now.isoformat(timespec="seconds"), "kind": "housekeeping",
                                                               "action": "profit lock: flattened"}) + "\n")
            r = paper.PaperRunner(self.B(), state_dir=d, sleep=lambda *_: None)
            self.assertFalse(r.exec.profit_lock.entries_allowed(now))
            self.assertTrue(r.exec.breaker.entries_allowed(now))


class LadderWalkTests(unittest.TestCase):
    """The backtest's version of the owner's scaled exits (engine.ladder_walk)."""
    LAD = {"cut_fracs": [0.25, 0.5], "cut_depth_r": [0.5, 0.75], "tp_fracs": [0.25, 0.5], "tp_depth_atr": [0.0, 0.25],
           "lock": ["breakeven", "tp"], "trail_atr": 1.0}

    def walk(self, bars, lad=None, stop=98.0, ztgt=102.0):
        from alpacabot.engine import ladder_walk
        o, h, l, c = (np.array([b[k] for b in bars], float) for k in range(4))
        return ladder_walk(o, h, l, c, 0, len(bars), 100.0, stop, ztgt, 1.0, 2.0, lad or self.LAD, lambda px: px)

    def test_tp_steps_then_trail(self):
        bars = [(100, 100.5, 99.8, 100.2), (100.2, 102.4, 100.1, 102.3), (102.3, 105.0, 102.2, 104.8), (104.8, 104.9, 103.5, 103.6)]
        j, pnl, why = self.walk(bars)
        # 25% at 102, 25% at 102.25, then the rider trails 1 ATR behind 105 -> stopped at 104 on the last bar
        self.assertEqual(j, 3)
        self.assertAlmostEqual(pnl, 0.25 * 2 + 0.25 * 2.25 + 0.5 * 4.0)
        self.assertEqual(why, "lock")

    def test_cut_steps_then_stop(self):
        bars = [(100, 100.2, 99.0, 99.1), (99.1, 99.2, 98.5, 98.6), (98.6, 98.7, 97.5, 97.6)]
        j, pnl, why = self.walk(bars)
        self.assertAlmostEqual(pnl, 0.25 * -1.0 + 0.25 * -1.5 + 0.5 * -2.0)        # cuts at -0.5R, -0.75R, the stop takes the rest
        self.assertEqual((j, why), (2, "stop"))

    def test_trail_from_entry_option(self):
        lad = {**self.LAD, "cut_fracs": [], "cut_depth_r": [], "trail_from_entry_r": 0.5}
        bars = [(100, 101.5, 99.9, 101.4), (101.4, 101.45, 100.4, 100.5)]          # +1.5 then back: trail 1R (0.5 x 2) behind 101.5
        j, pnl, why = self.walk(bars, lad)
        self.assertAlmostEqual(pnl, 0.5)
        self.assertEqual((j, why), (1, "lock"))

    def test_each_expert_picks_its_own_exits(self):
        from alpacabot.engine import EXPERT_LADDERS, ladder_for
        from alpacabot.research import LADDER_VARIANTS
        lad = LADDER_VARIANTS["each expert's own exits"]
        self.assertEqual(ladder_for(lad, "connors_rsi2")["tp_fracs"], [1.0])
        self.assertEqual(ladder_for(lad, "S:donchian_turtle")["tp_fracs"], [])          # shorts use the same expert's plan
        self.assertEqual(ladder_for(lad, "unknown")["tp_fracs"], [0.25, 0.5])            # default plan
        from alpacabot.experts import ALL_EXPERTS, CANDIDATE_EXPERTS
        self.assertEqual(set(EXPERT_LADDERS), set(ALL_EXPERTS) | set(CANDIDATE_EXPERTS) | {"trend_stairs", "scalper", "velez_halt", "velez_vbs", "velez_rbi"})   # + the owner's own strategy
        bars = [(100, 102.1, 99.9, 102.0), (102.0, 103.0, 101.9, 102.9)]
        j, pnl, why = self.walk(bars, ladder_for(lad, "connors_rsi2"))
        self.assertEqual((j, pnl, why), (0, 2.0, "zone100"))                            # Connors: all out at the zone
        turtle = ladder_for(lad, "donchian_turtle")
        bars = [(100, 106, 99.9, 105.5), (105.5, 105.6, 101.8, 102.0)]                   # no target; 2 ATR trail behind 106 -> 104
        j, pnl, why = self.walk(bars, turtle)
        self.assertEqual((j, pnl, why), (1, 4.0, "lock"))

    def test_breakeven_lock_after_first_step(self):
        bars = [(100, 102.1, 100.0, 101.9), (101.9, 102.0, 99.5, 99.6)]
        j, pnl, why = self.walk(bars)
        self.assertAlmostEqual(pnl, 0.25 * 2.0 + 0.75 * 0.0)                       # 25% banked, the rest out at break-even
        self.assertEqual(why, "lock")

    def test_the_drawn_plan_on_a_short(self):
        """The mirror image of the drawing: sell short, cover 25%/50% if price rises toward the stop, cover 25%/50% in the take-profit zone BELOW
        (stop moves down to break-even, then to the zone), the rest rides with a stop 1 ATR above its lowest price."""
        import tempfile
        from alpacabot.exits import FixedState
        with tempfile.TemporaryDirectory() as tmp:
            lt = LadderTests("test_tier_from_depth")
            r = lt._runner(tmp)
            now = datetime(2026, 10, 14, 11, 0, tzinfo=paper_ET())
            r.exits.positions = {"X": {"state": FixedState(entry=-100.0, stop=-102.0, tp=-90.0, atr=1.0, trail=2.0), "qty": 100, "side": -1,
                                       "opened": "t0", "zone_tp": 98.0, "best_px": 100.0,
                                       "ladder": {"ref": 100.0, "atr": 1.0, "tier": 0, "stop_px": 102.0, "fill": 100.0, "peak_qty": 100, "sold": 0,
                                                  "tp_steps": 0, "cfg": {**LadderTests.CFG, "trail_atr": 1.0}}}}
            pos = r.exits.positions["X"]
            self.assertFalse(r.ladder_cut("X", pos, 100.9, now))                  # a quarter of the way to the stop: hold
            self.assertEqual(pos["qty"], 100)
            self.assertFalse(r.ladder_cut("X", pos, 101.0, now))                  # half way: cover 25%
            self.assertEqual(pos["qty"], 75)
            pos["ladder"]["sold"], pos["qty"] = 0, 100                            # (reset: the same trade, now in the profit zone instead)
            self.assertFalse(r.ladder_take("X", pos, 98.0, now))                  # at the zone: cover 25%, stop to break-even
            self.assertEqual(pos["qty"], 75)
            self.assertEqual(pos["state"].stop, -100.0)                           # (shorts are tracked on -price: stop = 100)
            self.assertFalse(r.ladder_take("X", pos, 97.7, now))                  # 0.3 ATR beyond: 50% covered, stop locked at the zone
            self.assertEqual(pos["qty"], 50)
            self.assertEqual(pos["state"].stop, -98.0)
            pos["best_px"] = 95.0                                                 # the rest rides down: stop 1 ATR above the lowest price
            r.lock_stop("X", pos, pos["best_px"] + 1.0, quiet=True)
            self.assertEqual(pos["state"].stop, -96.0)
            r.lock_stop("X", pos, 99.0, quiet=True)
            self.assertEqual(pos["state"].stop, -96.0)                            # never loosens
            sides = [t for t in r.sent if t[2] == "close"]
            self.assertTrue(sides)
            rows = [json.loads(l) for l in (Path(tmp) / "trades.jsonl").read_text().splitlines()]
            self.assertTrue(all(t["side"] == "short" for t in rows))
            self.assertAlmostEqual(rows[1]["pnl"], 25 * (100.0 - 98.0))           # covered 25 shares at 98 after selling at 100


class OpeningRangeBreakoutTests(unittest.TestCase):
    def _day(self, break_up=True):
        from alpacabot.experts import features_both
        idx = pd.DatetimeIndex([t for d in pd.bdate_range("2026-09-01", periods=8) for t in pd.date_range(f"{d.date()} 09:30", f"{d.date()} 15:55", freq="5min", tz="America/New_York")])
        rng = np.random.default_rng(3)
        c = 100 + np.cumsum(rng.normal(0, 0.15, len(idx)))
        o = np.r_[100, c[:-1]]
        df = pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.05, "low": np.minimum(o, c) - 0.05, "close": c,
                           "volume": rng.integers(1000, 5000, len(idx)).astype(float)}, index=idx)
        return df.join(features_both(df))

    def test_signals_only_after_the_opening_range_and_before_11_30(self):
        from alpacabot.experts import Experts, CANDIDATE_EXPERTS, ALL_EXPERTS
        self.assertIn("opening_range_breakout", ALL_EXPERTS)                       # live since the 2026-10-15 study verdict (ADD)
        f = self._day()
        sigs = Experts(("09:50", "15:30")).signals(f, ["opening_range_breakout"])
        self.assertTrue(sigs)
        for _, s in sigs:
            t = f.index[s.i].strftime("%H:%M")
            self.assertTrue("10:00" <= t <= "11:30")
            self.assertTrue(0 < s.stop_dist)
            self.assertEqual(s.tag, "opening_range_breakout")
            day = f.index[s.i].date()
            orng = f[(f.index.date == day) & (f.index.strftime("%H:%M") < "10:00")]
            self.assertGreater(f["close"].iloc[s.i], orng["high"].max())              # broke the opening range high

    def test_brain_accepts_it_on_both_sides(self):
        from alpacabot.brain import Brain
        f = self._day()
        f["g_long_ok"] = True
        f["g_short_ok"] = True
        br = Brain(experts=["opening_range_breakout"], short_experts=["opening_range_breakout"], sector_filter=True, short_gate="red")
        sig = br.signals(f)
        self.assertTrue(any(s.side == 1 for s in sig) or any(s.side == -1 for s in sig))


class SwingShortTests(unittest.TestCase):
    """The swing bot's short side (owner: follow the trend down): strategy, simulator, and portfolio accounting that balances to the cent."""
    def _down(self, n=260, seed=5, drift=-0.003):
        idx = pd.bdate_range("2023-01-02", periods=n, tz="America/New_York")
        rng = np.random.default_rng(seed)
        c = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.008, n)))
        o = np.r_[c[0], c[:-1]]
        return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.004, "low": np.minimum(o, c) * 0.996, "close": c, "volume": 1e6}, index=idx)

    def test_entry_is_the_mirror_of_the_long_rule_and_gates_only_remove(self):
        from alpacabot.strategies import SectorTopDownShort
        d = self._down()
        spy = self._down(seed=9, drift=-0.0005)["close"]
        f = SectorTopDownShort(spy).frame(d)
        c = d["close"]
        e = c.ewm(span=20, adjust=False).mean()
        self.assertTrue(f["entry"].any())
        ok = f["entry"]
        self.assertTrue((c[ok] < e[ok]).all())                                  # below its 20-day average
        self.assertTrue((f["stop"][ok] > c[ok]).all())                          # the stop sits above the entry for a short
        gated = SectorTopDownShort(spy, stage4=True, market_weak=True).frame(d)
        self.assertTrue((gated["entry"] <= f["entry"]).all())                   # gates never add entries

    def test_simulator_stop_gap_and_signal_cover(self):
        from alpacabot.engine import Costs, simulate_swing_short

        class S:
            max_days = 10 ** 6

            def __init__(self, entry, exit_, stop):
                self.f = pd.DataFrame({"entry": entry, "exit": exit_, "stop": stop, "rs": 1.0})

            def frame(self, d): return self.f.set_index(d.index)
        idx = pd.bdate_range("2023-01-02", periods=6, tz="America/New_York")
        d = pd.DataFrame({"open": [100, 100, 99, 98, 103, 99.0], "high": [101, 101, 100, 99, 104, 100.0], "low": [99, 98, 97, 96, 100, 98.0],
                          "close": [100, 99, 98, 97, 102, 99.0], "volume": 1.0}, index=idx)
        # short signalled on day 0, sold at day 1's open (100); stop 102: day 4 gaps to 103 -> covered at the open
        t = simulate_swing_short("X", d, S([True, 0, 0, 0, 0, 0], [False] * 6, 102.0), Costs(0, 0, 0))[0]
        self.assertEqual((t.side, t.entry_px, t.exit_px, t.reason), (-1, 100.0, 103.0, "stop"))
        self.assertAlmostEqual(t.pnl_per_share(Costs(0, 0, 0)), -3.0)
        # same entry, no stop hit, the exit flag fires on day 3 -> covered at day 4's open (103), slippage worsens both legs
        t2 = simulate_swing_short("X", d, S([True, 0, 0, 0, 0, 0], [False, False, False, True, False, False], 110.0), Costs(100, 0, 0))[0]
        self.assertAlmostEqual(t2.entry_px, 99.0)                                # sold 1% worse (lower)
        self.assertAlmostEqual(t2.exit_px, 104.03)                               # bought back 1% worse (higher)

    def test_portfolio_books_balance_with_shorts(self):
        from alpacabot.engine import Costs, Portfolio, Trade, size_swing
        idx = pd.bdate_range("2023-01-02", periods=8, tz="America/New_York")
        closes = {"S": pd.Series([100, 100, 98, 96, 95, 97, 99, 99.0], index=idx), "L": pd.Series([50, 50, 51, 52, 53, 53, 53, 53.0], index=idx)}
        trades = [Trade("S", idx[1], idx[5], 100.0, 97.0, 3.0, "signal", 5, side=-1), Trade("L", idx[1], idx[4], 50.0, 53.0, 2.0, "signal", 4, side=1)]
        pf = Portfolio(risk_pct=0.01, max_positions=3, max_pos_frac=0.5, equity=100_000.0)
        fills, curve = size_swing(trades, closes, Costs(0, 0, 0), pf, idx[0].date(), idx[-1].date())
        self.assertEqual(len(fills), 2)
        short = fills[fills.symbol == "S"].iloc[0]
        self.assertAlmostEqual(short.pnl, short.qty * 3.0)                       # sold 100, bought back 97
        self.assertAlmostEqual(curve.iloc[-1] - 100_000.0, fills.pnl.sum(), places=6)   # the equity curve equals the realized P&L when flat


class MultiTimeframeConfirmTests(unittest.TestCase):
    """Owner (2026-10-15): trend direction AND strength confirmed on several timeframes, and re-checked while the trade runs."""
    def _frame(self, n=150):
        idx = pd.date_range("2026-09-01 09:30", periods=n, freq="5min", tz="America/New_York")
        d = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1e6}, index=idx)
        # real uptrend: f_* read +1. The mirrored (m_*) columns are the trend of the UPSIDE-DOWN series, so a real uptrend reads -1 there;
        # a short is confirmed when m_* reads +1 (the real trend is down)
        for c, v in (("f_h1_dir", 1), ("f_m15_dir", 1), ("f_h1_adx", 30.0), ("m_f_h1_dir", -1), ("m_f_m15_dir", -1), ("m_f_h1_adx", 30.0)):
            d[c] = v
        return d

    def _downtrend(self, d):
        d = d.copy()
        for c, v in (("f_h1_dir", -1), ("f_m15_dir", -1), ("m_f_h1_dir", 1), ("m_f_m15_dir", 1)):
            d[c] = v
        return d

    def test_features_exist_and_are_causal(self):
        from alpacabot.experts import features_both
        idx = pd.DatetimeIndex([t for d in pd.bdate_range("2026-09-01", periods=8) for t in pd.date_range(f"{d.date()} 09:30", f"{d.date()} 15:55", freq="5min", tz="America/New_York")])
        rng = np.random.default_rng(2)
        c = 100 + np.cumsum(rng.normal(0.02, 0.15, len(idx)))
        o = np.r_[100, c[:-1]]
        df = pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.05, "low": np.minimum(o, c) - 0.05, "close": c, "volume": 1e6}, index=idx)
        f = features_both(df)
        for col in ("f_h1_dir", "f_h1_adx", "f_m15_dir", "f_m15_adx", "m_f_h1_dir", "m_f_m15_dir"):
            self.assertIn(col, f)
        self.assertTrue(set(f["f_h1_dir"].unique()) <= {-1, 0, 1})
        self.assertTrue((f["f_h1_dir"] == -f["m_f_h1_dir"]).all())                   # the short view is the exact mirror
        cut = df.iloc[:300].copy()                                                    # changing the FUTURE never changes the past
        self.assertTrue((features_both(cut)["f_h1_dir"] == f["f_h1_dir"].iloc[:300]).all())

    def test_reason_trend_expert_long_and_short(self):
        from alpacabot.brain import tf_confirm_reason
        from alpacabot.strategies import Signal
        d = self._frame()
        lg = Signal(i=100, side=1, stop_dist=1.0, tag="donchian_turtle")
        sh = Signal(i=100, side=-1, stop_dist=1.0, tag="S:donchian_turtle")
        self.assertEqual(tf_confirm_reason(d, lg), "")
        self.assertNotEqual(tf_confirm_reason(d, sh), "")                             # an uptrend never confirms a short
        dn = self._downtrend(d)
        self.assertEqual(tf_confirm_reason(dn, sh), "")                               # a downtrend confirms it (mirrored columns)
        self.assertNotEqual(tf_confirm_reason(dn, lg), "")
        d.loc[d.index[100], "f_m15_dir"] = 0
        self.assertEqual(tf_confirm_reason(d, lg), "15-minute trend not confirmed")
        d.loc[d.index[100], "f_h1_adx"] = 12.0
        d.loc[d.index[100], "f_m15_dir"] = 1
        self.assertIn("strength too weak", tf_confirm_reason(d, lg))
        d.loc[d.index[100], "f_h1_adx"] = 30.0
        self.assertEqual(tf_confirm_reason(d, lg, wbias=-1), "against the weekly trend")   # weekly down: no longs
        self.assertEqual(tf_confirm_reason(dn, sh, wbias=-1), "")                         # ... but shorts are fine
        self.assertEqual(tf_confirm_reason(dn, sh, wbias=1), "against the weekly trend")  # weekly up: no shorts
        d.loc[d.index[100], "f_h1_dir"] = -1
        self.assertEqual(tf_confirm_reason(d, lg), "1-hour trend not confirmed")

    def test_range_experts_need_a_non_trending_hour(self):
        from alpacabot.brain import tf_confirm_reason
        from alpacabot.strategies import Signal
        d = self._frame()
        rs = Signal(i=100, side=1, stop_dist=1.0, tag="connors_rsi2")
        self.assertIn("trending", tf_confirm_reason(d, rs))                          # 1-hour ADX 30: not a range
        d["f_h1_adx"] = 15.0
        self.assertEqual(tf_confirm_reason(d, rs), "")

    def test_weekly_bias_and_trend_lost_exit(self):
        from alpacabot.engine import Costs, ZONE_PROFILES, simulate_day
        from alpacabot.strategies import Signal
        from alpacabot.zonemap import weekly_bias
        idx = pd.bdate_range("2024-01-02", periods=400, tz="America/New_York")
        up = pd.DataFrame({"close": 100 * np.exp(np.arange(400) * 0.002)}, index=idx)
        self.assertEqual(int(weekly_bias(up).iloc[-1]), 1)
        self.assertEqual(int(weekly_bias(up.iloc[:100]).iloc[-1]), 0)                  # not enough weeks yet
        self.assertEqual(int(weekly_bias(up[::-1].set_axis(idx)).iloc[-1]), -1)
        d = self._frame(60)
        d["f_h1_dir"] = 1
        d.loc[d.index[20:], "f_h1_dir"] = -1                                           # the 1-hour trend turns against the long at bar 20
        sig = Signal(i=5, side=1, stop_dist=10.0, tag="donchian_turtle", max_bars=0)
        z = {"profiles": ZONE_PROFILES, "trend_exit": True}
        t = simulate_day("X", d, [sig], Costs(0, 0, 0), zones=z)[0]
        self.assertEqual(t.reason, "trend_lost")
        self.assertEqual(d.index.get_loc(t.exit_time), 21)                             # decided at bar 20's close, out at the next open
        z2 = {"profiles": ZONE_PROFILES}
        self.assertNotEqual(simulate_day("X", d, [sig], Costs(0, 0, 0), zones=z2)[0].reason, "trend_lost")


class ZoneParamStudyTests(unittest.TestCase):
    def test_variants_cover_the_frozen_grid_without_repeating_the_live_default(self):
        from alpacabot.engine import ZONE_PROFILES
        from alpacabot.research import ZONE_GRID, zone_param_variants
        v = zone_param_variants()
        self.assertEqual(len(v), len({x[0] for x in v}))                                # no duplicates
        for label, prof, par, val in v:
            self.assertIn(val, ZONE_GRID[par])
            self.assertNotEqual(val, ZONE_PROFILES[prof][par])                           # the live default is the baseline, not a variant
        trend_only = [x for x in v if x[1] == "trend"]
        self.assertEqual(len(trend_only), sum(len([g for g in vals if g != ZONE_PROFILES["trend"][par]]) for par, vals in ZONE_GRID.items()))
        for prof in ("trend", "range"):                                                  # every live setting is itself on the frozen grid
            for par, vals in ZONE_GRID.items():
                self.assertIn(ZONE_PROFILES[prof][par], vals)


class ProfileAssignStudyTests(unittest.TestCase):
    def test_moving_an_expert_to_the_range_profile_is_undone_after_the_run(self):
        from alpacabot import engine, research
        from alpacabot.engine import ZONE_PROFILES, zone_profile
        before = set(engine.RANGE_STYLE)
        self.assertIs(zone_profile({"profiles": ZONE_PROFILES}, "dual_regime"), ZONE_PROFILES["trend"])
        res = research.profile_assign_study({}, ("2021-07-01", "2021-12-31"), ("2022-01-01", "2022-06-30"), ckpt=None)
        self.assertEqual(set(engine.RANGE_STYLE), before)                                 # the study never leaves the live sets changed
        self.assertEqual(res["family"], "profile_assign")
        self.assertEqual(len(res["periods"]["train"]), 4)
        engine.RANGE_STYLE.add("dual_regime")
        try:
            self.assertIs(zone_profile({"profiles": ZONE_PROFILES}, "S:dual_regime"), ZONE_PROFILES["range"])
        finally:
            engine.RANGE_STYLE.discard("dual_regime")


class StockRangeBoxTests(unittest.TestCase):
    def _daily(self, sideways=True, n=260):
        idx = pd.bdate_range("2023-01-02", periods=n, tz="America/New_York")
        rng = np.random.default_rng(11)
        base = 100 + (2 * np.sin(np.arange(n) / 6.0) if sideways else 0.35 * np.arange(n))
        c = base + rng.normal(0, 0.3, n)
        o = np.r_[c[0], c[:-1]]
        return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.6, "low": np.minimum(o, c) - 0.6, "close": c, "volume": 1e6}, index=idx)

    def test_box_is_valid_for_a_sideways_stock_and_not_for_a_trending_one(self):
        from alpacabot.zonemap import range_box
        side, trend = range_box(self._daily(True)), range_box(self._daily(False))
        self.assertTrue(side["valid"].iloc[-1])
        self.assertLess(side["bot"].iloc[-1], side["top"].iloc[-1])
        self.assertFalse(trend["valid"].iloc[-30:].any())

    def test_attach_puts_the_box_on_each_session_from_earlier_days_only(self):
        from alpacabot.zonemap import attach
        d = self._daily(True)
        sess = d.index[200]
        f = pd.DataFrame({"close": [100.0, 100.5]}, index=pd.DatetimeIndex([sess + pd.Timedelta(hours=10), sess + pd.Timedelta(hours=11)]))
        out = attach({"AAA": [f]}, {"AAA": d}, with_box=True)["AAA"][0]
        valid, top, bot = out.attrs["box"]
        prior = d.iloc[:200]
        self.assertAlmostEqual(top, float(prior["high"].iloc[-40:].max()))        # the box is the prior 40 days, not including the session itself
        self.assertAlmostEqual(bot, float(prior["low"].iloc[-40:].min()))
        g = pd.DataFrame({"close": [100.0, 100.5]}, index=f.index)
        self.assertNotIn("box", attach({"AAA": [g]}, {"AAA": d})["AAA"][0].attrs)            # off unless asked for

    def test_every_expert_keeps_the_stage_rule_and_range_experts_also_need_the_box(self):
        from alpacabot.brain import BoxRangeBrain
        from alpacabot.strategies import Signal
        day = pd.DataFrame({"close": [95.0, 105.0]})
        day.attrs.update(stage=2, box=(True, 110.0, 90.0))                  # an advancing stock with a valid box: 90-110
        sigs = [Signal(i=0, side=1, tag="connors_rsi2"), Signal(i=1, side=1, tag="connors_rsi2"),    # long low in the box / long high in the box
                Signal(i=1, side=-1, tag="S:williams_r"), Signal(i=0, side=1, tag="velez_elephant")]  # short high (stage 2 forbids shorts) / a trend expert
        class Stub:
            def signals(self, d):
                return sigs
        got = [(s.tag, s.i) for s in BoxRangeBrain(Stub(), "half").signals(day)]
        self.assertEqual(got, [("connors_rsi2", 0), ("velez_elephant", 0)])         # the short is dropped by the stage rule, the high long by the box half
        self.assertEqual(len(BoxRangeBrain(Stub(), "valid").signals(day)), 3)
        day.attrs["box"] = (False, 110.0, 90.0)                                      # no valid box: no range-expert trades at all
        self.assertEqual([x.tag for x in BoxRangeBrain(Stub(), "valid").signals(day)], ["velez_elephant"])   # only the trend expert is left


class BoxStrategyTests(unittest.TestCase):
    def _day(self, bot_dip_at=60, top_poke_at=None):
        idx = pd.date_range("2024-05-01 09:30", periods=390, freq="5min", tz="America/New_York")[:78]
        c = np.full(len(idx), 100.0)                                   # a quiet stock mid-box (box 90-110, 100 is the middle)
        o, hi, lo = c.copy(), c + 0.1, c - 0.1
        if bot_dip_at is not None:                                     # one bar dips to the bottom of the box and closes up inside the bottom third
            o[bot_dip_at], lo[bot_dip_at], c[bot_dip_at], hi[bot_dip_at] = 91.0, 90.5, 93.0, 93.2
        if top_poke_at is not None:                                    # one bar pokes the top and closes down inside the top third
            o[top_poke_at], hi[top_poke_at], c[top_poke_at], lo[top_poke_at] = 109.0, 109.6, 107.0, 106.8
        d = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": 1e5, "f_atr": 0.3}, index=idx)
        d.attrs.update(box=(True, 110.0, 90.0), box_atr=2.0)
        return d

    def test_buys_the_bottom_of_a_valid_box_with_the_drawn_stop_and_the_far_edge_target(self):
        from alpacabot.brain import BoxSignals
        class Empty:
            def signals(self, d):
                return []
        got = BoxSignals(Empty()).signals(self._day())
        self.assertEqual(len(got), 1)
        s = got[0]
        self.assertEqual((s.i, s.side, s.tag), (60, 1, "range_box"))
        self.assertAlmostEqual(s.stop_dist, 93.0 - (90.0 - 0.5 * 2.0))             # Sell ALL = 0.5 daily ATR under the box low
        self.assertAlmostEqual(s.target_px, 110.0 - 0.25 * 2.0)                    # TP at the far edge less a quarter ATR

    def test_sells_the_top_and_nothing_without_a_valid_box_or_outside_the_window(self):
        from alpacabot.brain import BoxSignals
        class Empty:
            def signals(self, d):
                return []
        d = self._day(bot_dip_at=None, top_poke_at=60)
        got = BoxSignals(Empty()).signals(d)
        self.assertEqual([(x.side, x.tag) for x in got], [(-1, "S:range_box")])
        d.attrs["box"] = (False, 110.0, 90.0)
        self.assertEqual(BoxSignals(Empty()).signals(d), [])                       # no valid box, no trade
        early = self._day(bot_dip_at=3)                                            # 09:45 is before the 10:00 window
        self.assertEqual(BoxSignals(Empty()).signals(early), [])

    def test_engine_takes_profit_at_the_boxs_far_edge_when_zones_are_on(self):
        from alpacabot.engine import ZONE_PROFILES, Costs, simulate_day
        d = self._day()
        n = len(d)
        # price walks up from the dip to the box top region after the entry
        path = np.linspace(93.0, 109.5, n - 61)
        d.iloc[61:, d.columns.get_loc("close")] = path
        d.iloc[61:, d.columns.get_loc("open")] = path
        d.iloc[61:, d.columns.get_loc("high")] = path + 0.2
        d.iloc[61:, d.columns.get_loc("low")] = path - 0.2
        sig = Signal(i=60, side=1, stop_dist=5.5, tag="range_box", atr=0.3, target_px=109.5)
        tr = simulate_day("X", d, [sig], Costs(slip_bps=0.0, fee_bps_sell=0.0), zones={"profiles": ZONE_PROFILES, "levels": False})
        self.assertEqual(len(tr), 1)
        self.assertGreaterEqual(tr[0].exit_px, 109.4)                               # sold at the box target, not at a zone-map level


class FibBoxTests(unittest.TestCase):
    def _day(self, o_, lo_, c_, hi_, at=60, top=104.0, bot=96.0):
        idx = pd.date_range("2024-05-01 09:30", periods=78, freq="5min", tz="America/New_York")
        c = np.full(len(idx), 100.0)
        o, hi, lo = c.copy(), c + 0.1, c - 0.1
        o[at], lo[at], c[at], hi[at] = o_, lo_, c_, hi_
        d = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": 1e5, "f_atr": 0.3}, index=idx)
        d.attrs.update(box=(True, top, bot), box_atr=2.0)
        return d

    class Empty:
        def signals(self, d):
            return []

    def test_buys_the_618_786_zone_with_tp1_at_half_and_tp2_at_0236(self):
        from alpacabot.brain import FibBoxSignals
        got = FibBoxSignals(self.Empty()).signals(self._day(98.0, 97.6, 98.5, 98.6))   # box 96-104: buy zone 97.71 .. 99.06
        self.assertEqual(len(got), 1)
        s = got[0]
        self.assertEqual((s.tag, s.side), ("fib_box", 1))
        self.assertAlmostEqual(s.stop_dist, 98.5 - (96.0 - 0.25 * 2.0))
        self.assertAlmostEqual(s.target_px, 100.0)                                 # TP1: the 0.5 level
        self.assertAlmostEqual(s.target2_px, 96.0 + 0.764 * 8.0)                    # TP2: the 0.236 retracement

    def test_no_trade_above_the_zone_or_without_a_valid_box(self):
        from alpacabot.brain import FibBoxSignals
        self.assertEqual(FibBoxSignals(self.Empty()).signals(self._day(99.2, 99.1, 99.5, 99.6)), [])   # closes above the 0.618 level
        d = self._day(98.0, 97.6, 98.5, 98.6)
        d.attrs["box"] = (False, 104.0, 96.0)
        self.assertEqual(FibBoxSignals(self.Empty()).signals(d), [])

    def test_short_is_the_mirror(self):
        from alpacabot.brain import FibBoxSignals
        got = FibBoxSignals(self.Empty()).signals(self._day(102.0, 101.4, 101.5, 102.5))        # sell zone 100.94 .. 102.29
        self.assertEqual([(x.tag, x.side) for x in got], [("S:fib_box", -1)])
        self.assertAlmostEqual(got[0].target2_px, 104.0 - 0.764 * 8.0)

    def test_per_trade_ladder_puts_tp2_at_the_0236_level(self):
        from alpacabot.brain import fib_ladder_cfg
        cfg = {"tp_fracs": [0.25, 0.5], "tp_depth_atr": [0.0, 0.25], "cut_fracs": [0.25, 0.5]}
        out = fib_ladder_cfg(cfg, 100.0, 105.28, 0.5)
        self.assertAlmostEqual(out["tp_depth_atr"][1], 5.28 / 0.5)
        self.assertEqual(cfg["tp_depth_atr"], [0.0, 0.25])                           # the shared plan is not changed
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue((LadderTests("test_tier_from_depth")._runner(tmp).zone_config().get("fib") or {}).get("live"))


class HybridBoxTests(unittest.TestCase):
    def test_buys_the_confluence_level_with_two_take_profit_lines(self):
        from alpacabot.brain import HybridBoxSignals
        idx = pd.date_range("2024-05-01 09:30", periods=78, freq="5min", tz="America/New_York")
        c = np.full(len(idx), 100.0)
        o, hi, lo = c.copy(), c + 0.1, c - 0.1
        o[60], lo[60], c[60], hi[60] = 97.7, 97.4, 98.0, 98.1          # dips to the strong 97.5 swing level, closes up
        d = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": 1e5, "f_atr": 0.3}, index=idx)
        d.attrs.update(box=(True, 104.0, 94.0), box_atr=1.0,
                       levels=[{"price": 97.5, "touches": 3, "strong": True}, {"price": 101.5, "touches": 2, "strong": True}])
        class Empty:
            def signals(self, x):
                return []
        got = HybridBoxSignals(Empty()).signals(d)
        self.assertEqual(len(got), 1)
        s = got[0]
        self.assertEqual((s.tag, s.side, s.i), ("hybrid_box", 1, 60))
        self.assertGreater(s.target_px, 98.0 + s.stop_dist - 1e-9)                 # TP1 at least 1R away
        self.assertTrue(s.target2_px == 0.0 or s.target2_px > s.target_px)
        self.assertTrue((LadderTests("test_tier_from_depth")._runner(self.tmp).zone_config().get("hybrid") or {}).get("live"))

    def setUp(self):
        import tempfile
        self._t = tempfile.TemporaryDirectory()
        self.tmp = self._t.name

    def tearDown(self):
        self._t.cleanup()


class BoxConfirmStockTests(unittest.TestCase):
    def test_stage_rule_lets_box_strategies_trade_the_sideways_stages_only(self):
        from alpacabot.brain import stage_ok
        from alpacabot.strategies import Signal
        box_l, box_s = Signal(i=0, side=1, tag="box_confirm"), Signal(i=0, side=-1, tag="S:range_box")
        other = Signal(i=0, side=1, tag="velez_elephant")
        self.assertTrue(stage_ok(box_l, 1) and stage_ok(box_l, 2) and not stage_ok(box_l, 3))
        self.assertTrue(stage_ok(box_s, 3) and stage_ok(box_s, 4) and not stage_ok(box_s, 2))
        self.assertTrue(stage_ok(other, 2) and not stage_ok(other, 1))          # every other expert keeps the strict rule

    def test_strong_breakout_on_volume_is_bought(self):
        from alpacabot.brain import BoxConfirmSignals
        idx = pd.date_range("2024-05-01 09:30", periods=78, freq="5min", tz="America/New_York")
        n = len(idx)
        c = 100 + 0.2 * np.sin(np.arange(n) / 3.0)
        o = np.r_[c[0], c[:-1]]
        hi, lo, v = np.maximum(o, c) + 0.05, np.minimum(o, c) - 0.05, np.full(n, 1000.0)
        k = 40                                                                       # 12:50: a big up bar closing at its high above the box top
        o[k], c[k], lo[k], hi[k], v[k] = 100.9, 102.0, 100.85, 102.02, 5000.0
        d = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": v}, index=idx)
        d["f_atr"] = (d["high"] - d["low"]).rolling(14, min_periods=1).mean()
        d.attrs.update(box=(True, 101.0, 98.0), box_atr=1.0)
        class Empty:
            def signals(self, x):
                return []
        got = BoxConfirmSignals(Empty(), "both").signals(d)
        self.assertTrue(any(s.i == k and s.side == 1 and s.tag == "box_confirm" for s in got))
        s = next(s for s in got if s.i == k)
        self.assertAlmostEqual(s.target_px, 101.0 + 1.5)                             # TP1 half the box height above the edge
        self.assertTrue((LadderTests("test_tier_from_depth")._runner(self.tmp).zone_config().get("box_confirm") or {}).get("live"))

    def setUp(self):
        import tempfile
        self._t = tempfile.TemporaryDirectory()
        self.tmp = self._t.name

    def tearDown(self):
        self._t.cleanup()


class TrendContinuationTests(unittest.TestCase):
    def _day(self):
        idx = pd.date_range("2024-05-01 09:30", periods=40, freq="5min", tz="America/New_York")
        n = len(idx)
        c = np.linspace(100, 101, n)
        o = c - 0.02
        d = pd.DataFrame({"open": o, "high": c + 0.05, "low": o - 0.05, "close": c, "volume": 1000.0, "f_atr": 0.1}, index=idx)
        k = 30                                                     # a strong up bar: big body closing at its high, heavy volume, wider range
        d.iloc[k, [0, 1, 2, 3, 4, 5]] = [100.70, 101.21, 100.69, 101.20, 3000.0, 0.2]
        j = 31                                                     # a weak bar: small body, long upper wick, light volume
        d.iloc[j, [0, 1, 2, 3, 4, 5]] = [101.20, 101.60, 101.10, 101.22, 500.0, 0.05]
        return d, k, j

    def test_votes_tell_a_continuing_bar_from_a_failing_one(self):
        from alpacabot.confirm import trend_votes
        d, k, j = self._day()
        self.assertEqual(trend_votes(d, k, 1)[0], 5)
        n, failed = trend_votes(d, j, 1)
        self.assertLessEqual(n, 2)
        self.assertIn("volume", failed)

    def test_only_trend_experts_are_filtered(self):
        from alpacabot.brain import TrendContinuation
        from alpacabot.strategies import Signal
        d, k, j = self._day()
        sigs = [Signal(i=j, side=1, tag="velez_elephant"), Signal(i=k, side=1, tag="raschke_grail"), Signal(i=j, side=1, tag="connors_rsi2")]
        class Stub:
            def signals(self, x):
                return sigs
        got = [s.tag for s in TrendContinuation(Stub(), 0.6).signals(d)]
        self.assertEqual(got, ["raschke_grail", "connors_rsi2"])                  # the weak-bar trend entry is dropped, the range expert kept


class ExpertZoneTests(unittest.TestCase):
    def test_each_trend_expert_has_its_own_zones_and_exit_plan_live(self):
        import tempfile
        from alpacabot.engine import EXPERT_LADDERS, EXPERT_ZONES, ladder_for, zone_profile
        with tempfile.TemporaryDirectory() as tmp:
            r = LadderTests("test_tier_from_depth")._runner(tmp)
            (Path(tmp) / "zones_override.json").write_text(json.dumps({"velez_hybrid": {"live": False}}))   # the experts' own plans
            cfg = r.zone_config()
            (Path(tmp) / "zones_override.json").write_text(json.dumps({}))
            tuned = r.zone_config()                     # owner 2026-10-21 win-rate tune live: one take-profit, no cuts, for the live experts
        self.assertEqual(ladder_for(tuned["ladder"], "connors_rsi2")["cut_fracs"], [])
        self.assertEqual(ladder_for(tuned["ladder"], "connors_rsi2")["tp_fracs"], [1.0])
        for name in ("velez_elephant", "raschke_grail", "brooks_pullback", "donchian_turtle"):
            zp = zone_profile({"profiles": cfg["profiles"]}, "S:" + name)
            self.assertEqual(zp["chase_atr"], EXPERT_ZONES[name]["chase_atr"])               # its own buy zone
            lad = ladder_for(cfg["ladder"], name)
            self.assertEqual(lad["tp_fracs"], EXPERT_LADDERS[name]["tp_fracs"])             # its own take-profit zone
            self.assertEqual(lad["cut_fracs"], [])                                           # its own sell zone: one hard stop
        self.assertEqual(ladder_for(cfg["ladder"], "connors_rsi2")["cut_fracs"], [0.25, 0.5])   # range experts keep the drawn plan
        self.assertEqual(zone_profile({"profiles": cfg["profiles"]}, "connors_rsi2")["target"], "mean")


class TrendGateOrderTests(unittest.TestCase):
    def test_trend_experts_pass_every_trend_gate_in_order(self):
        """Owner 2026-10-16: a trend expert enters only after trend recognition (heat map, direction first, Weinstein stage, multi-timeframe
        direction + strength) AND the continuation check (candle / volume / volatility); then its own zones apply."""
        import inspect
        import tempfile
        from alpacabot import paper
        with tempfile.TemporaryDirectory() as tmp:
            cfg = LadderTests("test_tier_from_depth")._runner(tmp).zone_config()
        self.assertTrue(cfg["live"] and cfg["direction_first"] and cfg["stage_filter"])
        self.assertTrue(cfg["mtf"]["entry"] and cfg["mtf"]["trend_exit"] and cfg["mtf"]["adx_min"] >= 20)
        self.assertTrue(cfg["trend_confirm"]["live"])
        src = inspect.getsource(paper.PaperRunner.on_bar_close)
        order = [src.index(x) for x in ("gates.apply(day", "zone_entry_block(sym", "mtf_block(sym", "trend_votes(day", "try_enter(sym")]
        self.assertEqual(order, sorted(order))                       # heat map -> direction/stage/chase -> multi-timeframe -> continuation -> order


class RiskByExpertTests(unittest.TestCase):
    def test_clear_losers_trade_at_half_size_and_the_rest_at_full(self):
        import inspect
        import tempfile
        from alpacabot import paper
        with tempfile.TemporaryDirectory() as tmp:
            cfg = LadderTests("test_tier_from_depth")._runner(tmp).zone_config()
        rbe = cfg["risk_by_expert"]
        self.assertEqual(rbe["williams_r"], 0.005)
        self.assertNotIn("velez_elephant", rbe)
        self.assertNotIn("brooks_pullback", rbe)
        src = inspect.getsource(paper.PaperRunner.on_bar_close)
        self.assertLess(src.index("risk_by_expert"), src.index("try_enter(sym"))   # applied before the order is sized


class ReversalConfirmTests(unittest.TestCase):
    def _day(self):
        idx = pd.date_range("2024-05-01 09:30", periods=40, freq="5min", tz="America/New_York")
        n = len(idx)
        c = np.linspace(101, 100, n)                                 # a slow drift down
        o = c + 0.02
        d = pd.DataFrame({"open": o, "high": o + 0.05, "low": c - 0.05, "close": c, "volume": 1000.0, "f_atr": 0.1}, index=idx)
        k = 30                                                        # a bullish reversal bar: body 0.15 of a 0.2 range, closes near the high
        d.iloc[k, [0, 1, 2, 3]] = [100.20, 100.37, 100.17, 100.35]
        j = 31                                                        # a crash bar: big body down on heavy volume, ATR exploding
        d.iloc[j, [0, 1, 2, 3, 4, 5]] = [100.35, 100.36, 99.30, 99.32, 5000.0, 0.6]
        return d, k, j

    def test_a_turning_dip_passes_and_a_falling_knife_does_not(self):
        from alpacabot.confirm import reversal_votes
        d, k, j = self._day()
        self.assertGreaterEqual(reversal_votes(d, k, 1)[0], 3)
        n, failed = reversal_votes(d, j, 1)
        self.assertLessEqual(n, 1)
        self.assertIn("reversal candle", failed)

    def test_only_mean_reversion_experts_are_gated(self):
        from alpacabot.brain import ReversalConfirm
        from alpacabot.strategies import Signal
        d, k, j = self._day()
        sigs = [Signal(i=j, side=1, tag="connors_rsi2"), Signal(i=k, side=1, tag="williams_r"), Signal(i=j, side=1, tag="range_box"),
                Signal(i=j, side=1, tag="velez_elephant")]
        class Stub:
            def signals(self, x):
                return sigs
        self.assertEqual([s.tag for s in ReversalConfirm(Stub(), 2).signals(d)], ["williams_r", "range_box", "velez_elephant"])
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(LadderTests("test_tier_from_depth")._runner(tmp).zone_config()["reversal_confirm"]["live"])


class VelezReversalTests(unittest.TestCase):
    def test_bottoming_tail_and_180_count_as_reversal_candles(self):
        from alpacabot.confirm import reversal_votes
        idx = pd.date_range("2024-05-01 09:30", periods=30, freq="5min", tz="America/New_York")
        c = np.full(len(idx), 100.0)
        d = pd.DataFrame({"open": c + 0.02, "high": c + 0.05, "low": c - 0.05, "close": c, "volume": 1000.0, "f_atr": 0.1,
                          "f_ema20": c + 0.3}, index=idx)
        d.iloc[20, [0, 1, 2, 3]] = [99.80, 99.85, 99.40, 99.83]      # bottoming tail: long lower wick, closes near the high
        n, failed = reversal_votes(d, 20, 1)
        self.assertNotIn("reversal candle", failed)
        self.assertNotIn("stretched", failed)                           # 0.3+ below the 20 EMA with ATR 0.1: stretched
        d.iloc[24, [0, 1, 2, 3]] = [100.30, 100.32, 99.70, 99.72]      # a wide red bar ...
        d.iloc[25, [0, 1, 2, 3]] = [99.72, 100.10, 99.70, 100.08]      # ... then a green bar closing above its middle: a 180
        self.assertNotIn("reversal candle", reversal_votes(d, 25, 1)[1])

    def test_flatten_now_closes_the_bots_own_positions(self):
        import tempfile
        from datetime import datetime as _dt
        from alpacabot.paper import ET
        with tempfile.TemporaryDirectory() as tmp:
            r = LadderTests("test_tier_from_depth")._runner(tmp)
            closed = []
            r.close_now = lambda sym, why, bar: (closed.append((sym, why)), r.exits.positions.pop(sym))
            class Ex:
                positions = {"AAA": {"qty": 5}, "BBB": {"qty": 3}}
            r.exits, r.journal, r.weekly_guard = Ex(), (lambda *a, **k: None), (lambda now: None)
            r.exec = type("E", (), {"kill_switch": False})()
            (Path(tmp) / "FLATTEN_NOW").write_text("x")
            r.check_session(_dt(2026, 10, 16, 11, 0, tzinfo=ET))
            self.assertEqual(sorted(closed), [("AAA", "flatten_owner"), ("BBB", "flatten_owner")])
            self.assertFalse((Path(tmp) / "FLATTEN_NOW").exists())


class BoxStrategyStudyTests(unittest.TestCase):
    def test_study_runs_and_restores_the_live_style_sets(self):
        from alpacabot import engine, research
        from alpacabot import experts as ex
        a, b = set(engine.RANGE_STYLE), set(ex.RANGE_EXPERTS)
        res = research.box_strategy_study({}, ("2021-07-01", "2021-12-31"), ("2022-01-01", "2022-06-30"), ckpt=None)
        self.assertEqual((set(engine.RANGE_STYLE), set(ex.RANGE_EXPERTS)), (a, b))
        self.assertEqual(len(res["periods"]["train"]), 3)
        self.assertEqual(res["family"], "box_strategy")


class SectorTopDownTests(unittest.TestCase):
    def _data(self):
        idx = pd.bdate_range("2024-01-02", periods=160, tz="America/New_York")
        rng = np.random.default_rng(4)
        spy = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.006, len(idx))))
        stk = 100 * np.exp(np.cumsum(rng.normal(0.0035, 0.008, len(idx))))        # a strong, persistent leader
        def frame(c):
            o = np.r_[c[0], c[:-1]]
            return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.004, "low": np.minimum(o, c) * 0.996, "close": c, "volume": 1e6}, index=idx)
        return frame(stk), frame(spy)

    def test_rules_no_lookahead_and_fixed_price_stop(self):
        from alpacabot.engine import simulate_swing
        from alpacabot.strategies import SectorTopDown
        d, spy = self._data()
        st = SectorTopDown(spy["close"])
        f = st.frame(d)
        c = d["close"]
        from alpacabot.indicators import ema, sma
        want = (c > ema(c, 20)) & (ema(c, 20) > sma(c, 50)) & ((c.pct_change(20) - spy["close"].pct_change(20)) >= 0.03)
        self.assertTrue(f["entry"].equals(want & f["entry"]) and f["entry"].sum() > 5)       # only when every condition holds
        trades = simulate_swing("X", d, st, Costs(0, 0))
        self.assertTrue(trades)
        for t in trades:
            i = d.index.get_loc(t.entry_time) - 1                                          # the signal bar is the one BEFORE the entry bar
            self.assertTrue(f["entry"].iloc[i])
            self.assertAlmostEqual(t.entry_px, d["open"].iloc[i + 1])                      # filled at the next open, no slippage here
            self.assertAlmostEqual(t.stop_dist, t.entry_px - f["stop"].iloc[i])            # risk is measured to the signal-bar stop price
        d2 = d.copy()                                                                      # changing today's bar cannot change yesterday's signal
        d2.iloc[-1, d2.columns.get_loc("close")] *= 0.5
        self.assertTrue(st.frame(d2)["entry"].iloc[:-1].equals(f["entry"].iloc[:-1]))

    def test_gap_through_the_stop_fills_at_the_open_and_ranking(self):
        from alpacabot.engine import simulate_swing
        from alpacabot.strategies import SectorTopDown
        d, spy = self._data()
        st = SectorTopDown(spy["close"])
        f = st.frame(d)
        t0 = simulate_swing("X", d, st, Costs(0, 0))[0]
        i = d.index.get_loc(t0.entry_time) - 1
        k = d.index.get_loc(t0.entry_time) + 2
        d.iloc[k, d.columns.get_loc("open")] = float(f["stop"].iloc[i]) - 5.0              # gap far below the stop
        d.iloc[k, d.columns.get_loc("low")] = float(f["stop"].iloc[i]) - 6.0
        t = simulate_swing("X", d, st, Costs(0, 0))[0]
        self.assertEqual(t.reason, "stop")
        self.assertAlmostEqual(t.exit_px, float(f["stop"].iloc[i]) - 5.0)                  # the adverse open, not the stop level
        self.assertTrue(t.rank >= 0.03)


class SwingValidateTests(unittest.TestCase):
    def test_placebo_keeps_the_entry_count_and_candidates_are_predeclared(self):
        from alpacabot.experts import ExpertSwing
        from alpacabot.research import SWING_CANDIDATES
        self.assertEqual(SWING_CANDIDATES, [("brooks_pullback", 10), ("williams_r", 10), ("donchian_turtle", 10)])
        from alpacabot.research import RSI2_CANDIDATES
        self.assertEqual(RSI2_CANDIDATES, [("connors_rsi2", 10)])
        idx = pd.bdate_range("2018-01-02", periods=500, tz="America/New_York")
        rng = np.random.default_rng(8)
        c = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.012, len(idx))))
        o = np.r_[c[0], c[:-1]]
        d = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.005, "low": np.minimum(o, c) * 0.995, "close": c, "volume": 1e6}, index=idx)
        real = ExpertSwing("williams_r").frame(d)
        for seed in (1, 2):
            plc = ExpertSwing("williams_r", placebo_seed=seed).frame(d)
            self.assertEqual(int(plc["entry"].sum()), int(real["entry"].sum()))
        self.assertFalse(ExpertSwing("williams_r", placebo_seed=1).frame(d)["entry"].equals(ExpertSwing("williams_r", placebo_seed=2).frame(d)["entry"]))


class SwingUnitTests(unittest.TestCase):
    def test_holiday_and_same_day_plans_are_not_executed(self):
        import tempfile
        from zoneinfo import ZoneInfo
        from alpacabot.swing_paper import SwingRunner
        et = ZoneInfo("America/New_York")

        class B:
            sent = []
            def equity(self): return 50000.0
            def is_session(self, day): return day.isoformat() != "2026-11-26"          # Thanksgiving
            def submit_order(self, *a, **k): self.sent.append(a); return {"id": "x"}
            def order(self, oid): return {"id": oid, "status": "filled", "filled_qty": "10", "filled_avg_price": "100"}
        with tempfile.TemporaryDirectory() as d:
            r = SwingRunner(B(), state_dir=d, fetch=lambda *a: pd.DataFrame(), sleep=lambda *_: None)
            r.state["plan"] = {"made": "2026-11-25", "exits": [], "entries": [{"symbol": "NVDA", "qty": 10, "stop": 90.0, "rs": 0.1, "signal_close": 100}]}
            self.assertEqual(r.open(datetime(2026, 11, 25, 9, 20, tzinfo=et)), {})          # plan made today: not today
            self.assertEqual(r.open(datetime(2026, 11, 26, 9, 20, tzinfo=et)), {})          # holiday: waits
            self.assertEqual(B.sent, [])
            r.open(datetime(2026, 11, 27, 9, 20, tzinfo=et), wait_until=datetime(2026, 11, 27, 9, 20, tzinfo=et))
            self.assertEqual([x[:5] for x in B.sent], [("NVDA", 10, "buy", "market", "day"), ("NVDA", 10, "sell", "stop", "gtc")])
            self.assertTrue(all(x[5].startswith("sw-") for x in B.sent))
            self.assertIn("NVDA", r.state["positions"])
            self.assertEqual(r.open(datetime(2026, 11, 27, 9, 21, tzinfo=et)), {})          # never twice

    def test_unfilled_entry_is_cancelled_and_a_partial_fill_is_tracked(self):
        import tempfile
        from unittest import mock
        from zoneinfo import ZoneInfo
        from alpacabot import swing_paper
        et = ZoneInfo("America/New_York")

        class B:
            def __init__(self): self.sent, self.cancelled = [], []
            def equity(self): return 50000.0
            def is_session(self, day): return True
            def submit_order(self, *a, **k): self.sent.append(a); return {"id": a[0]}
            def cancel_order(self, oid): self.cancelled.append(oid)
            def order(self, oid):
                if oid not in self.cancelled:
                    return {"id": oid, "status": "accepted", "filled_qty": "0"}
                return {"id": oid, "status": "canceled", "filled_qty": "4" if oid == "AMD" else "0", "filled_avg_price": "100"}
        b = B()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(swing_paper, "_alert"):
            r = swing_paper.SwingRunner(b, state_dir=d, fetch=lambda *a: pd.DataFrame(), sleep=lambda *_: None)
            r.state["plan"] = {"made": "2025-10-13", "exits": [], "entries": [
                {"symbol": "AMD", "qty": 5, "stop": 90.0, "rs": 0.1, "signal_close": 100},
                {"symbol": "META", "qty": 2, "stop": 90.0, "rs": 0.1, "signal_close": 100}]}
            r.open(datetime(2025, 10, 14, 9, 20, tzinfo=et), wait_until=datetime(2025, 10, 14, 9, 20, tzinfo=et))
            self.assertEqual(sorted(b.cancelled), ["AMD", "META"])                       # nothing left working at the broker
            self.assertEqual(r.state["positions"]["AMD"]["qty"], 4)                      # the partial fill is held, with its stop
            self.assertNotIn("META", r.state["positions"])
            self.assertIn(("AMD", 4, "sell", "stop", "gtc"), [x[:5] for x in b.sent])


class WeeklyLockTests(unittest.TestCase):
    def test_intraday_weekly_lock_writes_kill_switch_and_alerts(self):
        import tempfile
        from unittest import mock
        from alpacabot import paper

        class B:
            def positions(self): return {}
            def equity(self): return 50000.0
            def quote(self, s): return (100.0, 100.0)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(paper, "send_telegram") as tg:
            r = paper.PaperRunner(B(), state_dir=d, sleep=lambda *_: None)
            (Path(d) / "equity.jsonl").write_text(json.dumps({"date": "2026-10-09", "equity": 53000.0}) + "\n")
            now = datetime(2026, 10, 12, 11, 0, tzinfo=paper.ET)
            r.base_equity = 50500.0                                          # own equity 50,500 vs peak 53,000 = -4.7%: no lock
            r.check_session(now)
            self.assertFalse((Path(d) / "KILL_SWITCH").exists())
            r.base_equity = 50000.0                                          # -5.7%: lock
            r.check_session(now)
            self.assertTrue((Path(d) / "KILL_SWITCH").exists())
            self.assertTrue(r.exec.kill_switch)
            self.assertTrue(any("weekly loss lock" in c.args[0] for c in tg.call_args_list))

    def test_swing_weekly_lock_blocks_entries_only(self):
        import sys
        import tempfile
        from unittest import mock
        from zoneinfo import ZoneInfo
        sys.path.insert(0, str(Path(__file__).parent))
        import test_swing_replay as R
        from alpacabot import swing_paper
        data = {"AAA": R.series(2, 0.0020), "BBB": R.series(3, 0.0012), "SPY": R.series(1, 0.0003, 0.009, 400.0)}
        et = ZoneInfo("America/New_York")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(swing_paper, "_alert") as alert:
            box = {}
            fetch = lambda sym, tf, a, b, feed: data[sym][data[sym].index.date <= box["day"]]      # noqa: E731
            r = swing_paper.SwingRunner(R.SimBroker(data), state_dir=d, universe=["AAA", "BBB"], fetch=fetch, base_equity=100000.0)
            day = next(x.date() for x in R.DAYS[60:] if (box.update(day=x.date()) or True)
                       and r.scan(datetime(x.year, x.month, x.day, 16, 20, tzinfo=et)).get("entries"))
            r.state["equity_hist"] = [200000.0] * 4                       # a peak far above today's book: the lock must engage
            plan = r.scan(datetime(day.year, day.month, day.day, 16, 20, tzinfo=et))
            self.assertEqual(plan["entries"], [])
            self.assertTrue(alert.called)


class PartialFillTests(unittest.TestCase):
    def test_unfinished_entry_is_cancelled_and_only_the_filled_part_is_tracked(self):
        import tempfile
        from alpacabot import paper
        cancelled = []

        class B:
            def positions(self): return {}
            def equity(self): return 50000.0
            def quote(self, s): return (100.0, 100.0)
            def cancel_order(self, oid): cancelled.append(oid)
            def order(self, oid):
                if cancelled:
                    return {"id": oid, "status": "canceled", "filled_qty": "57", "filled_avg_price": "226.10"}
                return {"id": oid, "status": "partially_filled", "filled_qty": "40", "filled_avg_price": "226.00"}
        with tempfile.TemporaryDirectory() as d:
            r = paper.PaperRunner(B(), state_dir=d, sleep=lambda *_: None)
            self.assertEqual(r.confirm_fill({"id": "o1", "status": "new"}), ("filled", 57, 226.10))   # the FINAL quantity, after cancelling the rest
            self.assertEqual(cancelled, ["o1"])

    def test_report_card_survives_a_restart(self):
        import tempfile
        from unittest import mock
        from alpacabot import paper

        class B:
            def positions(self): return {}
            def equity(self): return 50000.0
            def quote(self, s): return (100.0, 100.0)
            def is_session(self, day): return True
        with tempfile.TemporaryDirectory() as d, mock.patch.object(paper, "send_telegram") as tg:
            r = paper.PaperRunner(B(), state_dir=d, sleep=lambda *_: None)
            self.assertTrue(r.was_session(date(2026, 10, 12)))                     # known from the calendar, not from memory
            r.end_of_day(datetime(2026, 10, 12, 16, 6, tzinfo=paper.ET))
            self.assertEqual(tg.call_count, 1)
            r2 = paper.PaperRunner(B(), state_dir=d, sleep=lambda *_: None)          # restart
            self.assertEqual(r2.reported, date(2026, 10, 12))                       # will not send twice


class ReportStartTests(unittest.TestCase):
    def test_day_one_counts_from_starting_capital(self):
        import tempfile
        from alpacabot import paper
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "base_equity.json").write_text(json.dumps({"equity": 54419.0}))
            (Path(d) / "equity.jsonl").write_text(json.dumps({"date": "2026-10-12", "equity": 54159.0, "spy": 777.0}) + "\n")
            card = paper.report_card(d, date(2026, 10, 12))
        self.assertIn("ROI since start (2026-10-12): -0.48%", card)
        self.assertIn("day -0.48%", card)


class CheckpointTests(unittest.TestCase):
    def test_finished_runs_are_not_repeated_after_a_restart(self):
        import tempfile
        from alpacabot.research import Checkpoint
        calls = []
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ck.json"
            a = Checkpoint(p)
            self.assertEqual(a.get("x", lambda: calls.append(1) or {"pf": 1.1}), {"pf": 1.1})
            b = Checkpoint(p)                                         # a new process after a stop
            self.assertEqual(b.get("x", lambda: calls.append(2) or {"pf": 9}), {"pf": 1.1})
            self.assertEqual(calls, [1])


class PausePausesTests(unittest.TestCase):
    def test_paper_pause_toggles_the_kill_switch_file(self):
        import tempfile
        from alpacabot.cli import main
        with tempfile.TemporaryDirectory() as d:
            main(["paper-pause", "on", "--state", d, "--reason", "validation: no intraday edge"])
            self.assertIn("no intraday edge", (Path(d) / "KILL_SWITCH").read_text())
            main(["paper-pause", "off", "--state", d])
            self.assertFalse((Path(d) / "KILL_SWITCH").exists())


class ProfitLockTests(unittest.TestCase):
    def test_lock_arms_at_half_a_percent_and_flattens_after_giving_back_half(self):
        from alpacabot.execution import ProfitLock
        t = datetime(2026, 10, 14, 10, 0)
        pl = ProfitLock(arm=0.005, keep=0.5)
        self.assertFalse(pl.update(50000.0, t))                    # day start
        self.assertFalse(pl.update(50200.0, t))                    # +0.4%: not armed
        self.assertFalse(pl.update(50080.0, t))                    # gave back most of an unarmed gain: no lock
        self.assertFalse(pl.update(50400.0, t))                    # +0.8%: armed, peak +400
        self.assertFalse(pl.update(50250.0, t))                    # +250 > half of 400
        self.assertTrue(pl.update(50200.0, t))                     # +200 = half: lock
        self.assertFalse(pl.entries_allowed(t))
        self.assertFalse(pl.update(50100.0, t))                    # trips once
        nxt = datetime(2026, 10, 15, 9, 35)
        self.assertFalse(pl.update(50200.0, nxt))                  # a new day starts fresh
        self.assertTrue(pl.entries_allowed(nxt))

    def test_executor_flattens_and_blocks_entries_on_lock(self):
        from alpacabot.execution import Executor, ProfitLock

        class B:
            eq, flat = 50000.0, 0
            def equity(self): return self.eq
            def flatten_all(self): self.flat += 1
        b = B()
        ex = Executor(b, profit_lock=ProfitLock(), sleep=lambda *_: None)
        t = datetime(2026, 10, 14, 11, 0)
        ex.opened["NVDA"] = t
        ex.on_tick(t)
        b.eq = 50500.0
        ex.on_tick(t)
        b.eq = 50240.0
        self.assertEqual(ex.on_tick(t), ["profit lock: flattened"])
        self.assertEqual(b.flat, 1)
        self.assertEqual(ex.try_enter("AAPL", None, 100.0, t), "profit lock")


class BreadthGateTests(unittest.TestCase):
    def _series(self, seed, drift, n=900):
        idx = pd.bdate_range("2015-01-02", periods=n, tz="America/New_York")
        rng = np.random.default_rng(seed)
        c = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.012, n)))
        o = np.r_[c[0], c[:-1]]
        return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.004, "low": np.minimum(o, c) * 0.996, "close": c, "volume": 1e6}, index=idx)

    def test_gate_is_share_above_sma200_and_blocks_only_entries(self):
        from alpacabot.research import breadth_gates
        from alpacabot.strategies import GatedSectorTopDown, SectorTopDown
        up, down = self._series(1, 0.002), self._series(2, -0.002)
        data = {"A": up, "B": up.copy(), "C": down, "D": down.copy(), "E": down.copy()}
        g = breadth_gates(data, {"A": "XLK", "B": "XLK", "C": "XLE", "D": "XLE", "E": "XLE"})
        self.assertTrue(g["XLK"].iloc[:199].all())                           # no SMA200 yet: fewer than 2 rated -> open
        from alpacabot.indicators import sma
        exact = (up["close"] > sma(up["close"], 200)).iloc[199:]
        self.assertTrue(g["XLK"].iloc[199:].equals(exact))                  # both members identical: gate = that stock above its SMA200
        self.assertLess(g["XLE"].iloc[300:].mean(), g["XLK"].iloc[300:].mean())
        spy = self._series(3, 0.0005)["close"]
        base = SectorTopDown(spy).frame(up)
        shut = GatedSectorTopDown(SectorTopDown(spy), pd.Series(False, index=up.index)).frame(up)
        self.assertGreater(int(base["entry"].sum()), 0)
        self.assertEqual(int(shut["entry"].sum()), 0)
        self.assertTrue(shut["exit"].equals(base["exit"]))

    def test_study_runs_and_spec_is_predeclared(self):
        from alpacabot.engine import Costs
        from alpacabot.research import BREADTH_SPEC, breadth_gate_study
        self.assertEqual((BREADTH_SPEC["threshold"], BREADTH_SPEC["sma"], BREADTH_SPEC["beat_share"]), (0.6, 200, 0.9))
        spec = {**BREADTH_SPEC, "placebo_runs": 2, "train": ("2016-03-01", "2016-12-31"), "validate": ("2017-01-01", "2018-06-30")}
        smap = {"A": "XLK", "B": "XLK", "C": "XLE", "D": "XLE"}
        data = {s: self._series(10 + k, 0.001 * (k % 3)) for k, s in enumerate(smap)}
        res = breadth_gate_study({"u": (data, smap)}, self._series(3, 0.0005), Costs(slip_bps=5.0), spec)
        self.assertEqual([r["period"] for r in res["rows"]], ["train", "validate"])
        self.assertIn("beats_placebo_share", res["rows"][1])
        self.assertIsInstance(res["passes"], bool)


class FrozenSystemsTests(unittest.TestCase):
    def _df(self, close):
        idx = pd.bdate_range("2016-01-04", periods=len(close), tz="America/New_York")
        c = np.asarray(close, float)
        return pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 1e6}, index=idx)

    def test_book_a_enters_on_55_day_high_and_exits_below_20_day_low(self):
        from alpacabot.engine import Costs, simulate_swing
        from alpacabot.strategies import BookA
        c = [100.0] * 70 + [110.0] * 10 + [95.0] * 5                    # breakout at bar 70, collapse below the 20-day low at bar 80
        d = self._df(c)
        f = BookA().frame(d)
        self.assertEqual(list(np.flatnonzero(f["entry"].to_numpy())), [70])
        t = simulate_swing("X", d, BookA(), Costs(0, 0, 0))
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].entry_time, d.index[71])
        self.assertIn(t[0].reason, ("signal", "stop"))

    def test_elite20_reclaim_entry_fixed_stop_and_ma50_target(self):
        from alpacabot.strategies import Elite20
        rng = np.random.default_rng(4)
        c = list(100 + rng.normal(0, 0.5, 60)) + [90.0, 92.0] + [100.0] * 60
        f = Elite20().frame(self._df(c))
        e = list(np.flatnonzero(f["entry"].to_numpy()))
        s_ = pd.Series(c)
        z = (s_ - s_.rolling(20).mean()) / s_.rolling(20).std()
        expect = [i for i in range(1, len(c)) if z.iloc[i - 1] <= -2.5 and z.iloc[i] > -2.5]
        self.assertEqual(e, expect)
        self.assertTrue(expect and expect[0] in (61, 62))                 # the first bar back above -2.5 sigma after the flush
        i = expect[0]
        m, sd = s_.rolling(20).mean().iloc[i], s_.rolling(20).std().iloc[i]
        self.assertAlmostEqual(Elite20().stop_price(f, i), m - 3.5 * sd)

    def test_placebo_keeps_entry_count_and_one_per_sector_cap(self):
        from alpacabot.engine import Costs, Portfolio, simulate_swing, size_swing
        from alpacabot.strategies import BookA, RandomEntries
        rng = np.random.default_rng(1)
        d = self._df(100 * np.exp(np.cumsum(0.001 + rng.normal(0, 0.015, 600))))
        real, plc = BookA().frame(d), RandomEntries(BookA(), 3).frame(d)
        self.assertEqual(int(plc["entry"].sum()), int(real["entry"].sum()))
        data = {"A": d, "B": d.copy()}
        trades = sorted([t for s, x in data.items() for t in simulate_swing(s, x, BookA(), Costs(0, 0, 0), all_signals=True)], key=lambda t: t.entry_time)
        a, b = d.index[0].date(), d.index[-1].date()
        both, _ = size_swing(trades, {s: x["close"] for s, x in data.items()}, Costs(0, 0, 0), Portfolio(max_positions=3), a, b)
        one, _ = size_swing(trades, {s: x["close"] for s, x in data.items()}, Costs(0, 0, 0), Portfolio(max_positions=3), a, b, {"A": "XLK", "B": "XLK"})
        self.assertGreater(len(both), len(one))                          # same sector: never A and B at once
        self.assertTrue(set(one["symbol"]) <= {"A", "B"})

    def test_spec_is_predeclared(self):
        from alpacabot.research import FROZEN_SPEC
        self.assertEqual((FROZEN_SPEC["min_pf"], FROZEN_SPEC["beat_share"], FROZEN_SPEC["risk_pct"], FROZEN_SPEC["max_positions"]), (1.2, 0.9, 0.005, 3))


class StyleSplitTests(unittest.TestCase):
    def test_scalp_day_split_on_the_report_card(self):
        import tempfile
        from alpacabot import paper
        mk = lambda o, c, pnl: {"date": "2026-10-14", "symbol": "NVDA", "side": "long", "qty": 1, "entry": 1, "exit": 1, "pnl": pnl, "r": 0.1,  # noqa: E731
                                "reason": "x", "opened": o, **({"closed": c} if c else {})}
        rows = [mk("2026-10-14T10:00:00-04:00", "2026-10-14T10:20:00-04:00", 10.0),
                mk("2026-10-14T10:00:00-04:00", "2026-10-14T13:00:00-04:00", -4.0),
                mk("2026-10-13T10:00:00-04:00", None, 2.0)]
        self.assertEqual([paper.trade_style(r) for r in rows], ["scalp", "day", "unclassified"])
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "trades.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            txt = paper.report_card(d, date(2026, 10, 14))
        self.assertIn("BY STYLE", txt)
        self.assertIn("scalp: today 1 trades $+10", txt)
        self.assertIn("day: today 1 trades $-4", txt)


class GivebackTests(unittest.TestCase):
    def test_giveback_section_counts_trades_that_were_up_1r_and_closed_red(self):
        import tempfile
        from alpacabot import paper
        mk = lambda r, peak: {"date": "2026-10-14", "symbol": "NVDA", "side": "long", "qty": 1, "entry": 1, "exit": 1, "pnl": r * 10,  # noqa: E731
                              "r": r, "peak_r": peak, "reason": "x", "opened": "2026-10-14T10:00:00-04:00", "closed": "2026-10-14T10:10:00-04:00"}
        rows = [mk(-1.0, 1.5), mk(2.0, 2.4), mk(-1.0, 0.2)]
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "trades.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            txt = paper.report_card(d, date(2026, 10, 14))
        self.assertIn("PROFIT GIVEBACK", txt)
        self.assertIn("1 of 2 trades that reached +1R closed at break-even or a loss", txt)
        self.assertIn("avg peak open profit +1.37R, avg kept +0.00R", txt)


class TradeZoneTests(unittest.TestCase):
    def _day(self, closes):
        idx = pd.date_range("2025-03-03 09:30", periods=len(closes), freq="5min", tz="America/New_York")
        c = np.asarray(closes, float)
        o = np.r_[c[0], c[:-1]]
        return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.05, "low": np.minimum(o, c) - 0.05, "close": c, "volume": 1e5}, index=idx)

    def test_intraday_exhaustion_zone_and_no_chase(self):
        from alpacabot.engine import ZONES, Costs, simulate_day
        from alpacabot.strategies import Signal
        day = self._day([100, 101, 102, 101, 100, 100, 100.2, 100.5, 101, 101.5, 102.5, 103, 103, 103])
        sig = Signal(i=5, side=1, stop_dist=0.5, target_r=0, max_bars=0, atr=0.5)
        plain = simulate_day("X", day, [sig], Costs(0, 0, 0))[0]
        zoned = simulate_day("X", day, [sig], Costs(0, 0, 0), zones=ZONES)[0]
        self.assertEqual(zoned.reason, "zone")                              # prior high ~102.05 is >= 1R above the ~100 fill
        self.assertAlmostEqual(zoned.exit_px, 102.05)
        self.assertNotEqual(plain.reason, "zone")
        chased = self._day([100, 100, 100, 100, 100, 100, 103, 103, 103])
        chased.iloc[6, chased.columns.get_loc("open")] = 103.0                  # the fill bar opens 3 points (6 ATR) past the signal close
        chased.iloc[6, chased.columns.get_loc("high")] = 103.05
        self.assertEqual(simulate_day("X", chased, [Signal(i=5, side=1, stop_dist=0.5, atr=0.5)], Costs(0, 0, 0), zones=ZONES), [])

    def test_mean_reversion_experts_target_the_average(self):
        from alpacabot.engine import ZONE_PROFILES, Costs, simulate_day
        from alpacabot.strategies import Signal
        day = self._day([102, 102, 102, 102, 101, 100, 99, 98, 98.5, 99.5, 100.5, 101.5, 102, 102])
        sig = Signal(i=8, side=1, stop_dist=0.5, atr=0.5, tag="bollinger_bounce")
        t = simulate_day("X", day, [sig], Costs(0, 0, 0), zones={"profiles": ZONE_PROFILES})[0]
        mean = float(np.mean(day["close"].to_numpy()[0:9]))
        self.assertEqual(t.reason, "zone")
        self.assertAlmostEqual(t.exit_px, mean, places=6)                    # back at the 20-bar (here 9-bar) average
        trend = simulate_day("X", day, [Signal(i=8, side=1, stop_dist=0.5, atr=0.5, tag="donchian_turtle")], Costs(0, 0, 0),
                             zones={"profiles": ZONE_PROFILES})[0]
        self.assertAlmostEqual(trend.exit_px, 102.05)                        # a trend expert targets the prior high instead

    def test_short_trades_use_the_mirrored_zone(self):
        from alpacabot.engine import ZONES, Costs, simulate_day
        from alpacabot.strategies import Signal
        day = self._day([100, 99, 98, 99, 100, 100, 99.8, 99.5, 99, 98.5, 97.5, 97, 97, 97])
        t = simulate_day("X", day, [Signal(i=5, side=-1, stop_dist=0.5, atr=0.5)], Costs(0, 0, 0), zones=ZONES)[0]
        self.assertEqual(t.reason, "zone")
        self.assertAlmostEqual(t.exit_px, 97.95)                             # the prior low, 98 - 0.05

    def test_swing_zone_target(self):
        from alpacabot.engine import Costs, simulate_swing
        from alpacabot.strategies import Zoned

        class S:
            max_days, name = 10 ** 6, "s"
            def frame(self, d):
                return pd.DataFrame({"entry": [i == 25 for i in range(len(d))], "exit": False}, index=d.index)
            def stop_dist(self, f, i):
                return 2.0
        idx = pd.bdate_range("2025-01-02", periods=40, tz="America/New_York")
        c = np.r_[np.full(10, 110.0), np.full(16, 100.0), np.linspace(100.2, 115, 14)]
        d = pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1e6}, index=idx)
        t = simulate_swing("X", d, Zoned(S()), Costs(0, 0, 0))[0]
        self.assertEqual(t.reason, "zone")
        self.assertAlmostEqual(t.exit_px, 110.5)                             # the prior 20-day high


class ZoneStudyTests(unittest.TestCase):
    def test_verdict_needs_better_expectancy_pf_and_no_deeper_drawdown_in_every_row(self):
        from alpacabot.research import _zone_verdict
        good = {"without": {"expectancy_pct_notional": 0.01, "profit_factor": 1.0, "max_drawdown": -0.05},
                "with": {"expectancy_pct_notional": 0.02, "profit_factor": 1.1, "max_drawdown": -0.055}}
        bad_dd = {"without": good["without"], "with": {**good["with"], "max_drawdown": -0.08}}
        self.assertTrue(_zone_verdict([good, good]))
        self.assertFalse(_zone_verdict([good, bad_dd]))


class ShadowZoneTests(unittest.TestCase):
    def test_shadow_zone_and_report_lines(self):
        import tempfile
        from alpacabot import paper
        from alpacabot.strategies import Signal
        idx = pd.date_range("2025-03-03 09:30", periods=25, freq="5min", tz="America/New_York")
        c = np.r_[np.linspace(100, 104, 10), np.linspace(104, 100, 15)]
        day = pd.DataFrame({"open": c, "high": c + 0.1, "low": c - 0.1, "close": c, "volume": 1e5}, index=idx)
        z = paper.shadow_zone(day, Signal(i=24, side=1, stop_dist=1.0, atr=1.0), fill=100.2, ref=100.0)
        self.assertEqual(z, {"chase": False, "target": 104.1, "profile": "trend"})   # untagged = trend style; prior high >= 1R away
        z2 = paper.shadow_zone(day, Signal(i=24, side=1, stop_dist=5.0, atr=1.0), fill=101.0, ref=100.0)
        self.assertEqual(z2, {"chase": True, "target": 111.0, "profile": "trend"})   # chased 1 point (> 0.5 ATR); high < 1R away -> 2R
        z3 = paper.shadow_zone(day, Signal(i=24, side=1, stop_dist=1.0, atr=1.0, tag="connors_rsi2"), fill=100.2, ref=100.0)
        self.assertEqual(z3["profile"], "range")                             # mean-reversion expert: target = the 20-bar average
        self.assertAlmostEqual(z3["target"], round(float(day["close"].iloc[-20:].mean()), 4))
        rows = [{"date": "2026-10-14", "symbol": "A", "side": "long", "qty": 10, "entry": 100, "exit": 99, "pnl": -10, "r": -1, "peak_r": 1.5,
                 "reason": "stop", "opened": "2026-10-14T10:00:00-04:00", "closed": "2026-10-14T10:40:00-04:00",
                 "zone_chase": False, "zone_hit": True, "zone_pnl": 41.0}]
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "trades.jsonl").write_text(json.dumps(rows[0]) + "\n")
            txt = paper.report_card(d, date(2026, 10, 14))
        self.assertIn("TRADE ZONES (logged on every trade", txt)
        self.assertIn("would have made $+41 vs $-10 actual", txt)


class DirectionAndZoneMapTests(unittest.TestCase):
    def _daily(self, closes):
        idx = pd.bdate_range("2025-01-02", periods=len(closes), tz="America/New_York")
        c = np.asarray(closes, float)
        o = np.r_[c[0], c[:-1]]
        return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.5, "low": np.minimum(o, c) - 0.5, "close": c, "volume": 1e6}, index=idx)

    def test_bias_up_down_range(self):
        from alpacabot import zonemap
        up = self._daily(100 + np.arange(120) * 0.8)
        dn = self._daily(200 - np.arange(120) * 0.8)
        rng = self._daily(100 + 3 * np.sin(np.arange(120) / 2))
        self.assertEqual(int(zonemap.bias(up).iloc[-1]), 1)
        self.assertEqual(int(zonemap.bias(dn).iloc[-1]), -1)
        self.assertEqual(int(zonemap.bias(rng).iloc[-1]), 0)
        session = up.index[-1].date()
        self.assertEqual(zonemap.bias_for_session(up, session), int(zonemap.bias(up.iloc[:-1]).iloc[-1]))   # previous close only

    def test_level_map_counts_retests_and_targets_the_nearest_strong_zone(self):
        from alpacabot import zonemap
        x = np.arange(120)
        d = self._daily(100 + 5 * np.sin(x * 2 * np.pi / 20))              # tops near 105.5 and bottoms near 94.5, retested 6 times
        lv = zonemap.level_map(d)
        strong = [z for z in lv if z["strong"]]
        self.assertTrue(any(abs(z["price"] - 105.5) < 1 and z["touches"] >= 4 for z in strong))
        self.assertTrue(any(abs(z["price"] - 94.5) < 1 for z in strong))
        self.assertAlmostEqual(zonemap.zone_target(lv, 100.0, 1, 1.0), max(z["price"] for z in strong if z["price"] < 107), places=4)
        self.assertIsNone(zonemap.zone_target(lv, 105.0, 1, 5.0))          # nothing strong 5R above

    def test_direction_first_filters_signals(self):
        from alpacabot.brain import DirectionFirst
        from alpacabot.strategies import Signal

        class B:
            max_trades = 3
            def signals(self, day):
                return [Signal(i=1, side=1, tag="donchian_turtle"), Signal(i=2, side=-1, tag="S:velez_elephant"),
                        Signal(i=3, side=1, tag="connors_rsi2"), Signal(i=4, side=-1, tag="S:williams_r")]
        day = pd.DataFrame({"close": [1.0] * 5})
        df = DirectionFirst(B())
        for b, want in ((1, [1, 3]), (-1, [2, 4]), (0, [3, 4])):
            day.attrs["bias"] = b
            self.assertEqual([s.i for s in df.signals(day)], want)
        self.assertEqual(df.max_trades, 3)                                  # everything else passes through

    def test_engine_takes_profit_at_the_zone_map_level(self):
        from alpacabot.engine import ZONES, Costs, simulate_day
        from alpacabot.strategies import Signal
        idx = pd.date_range("2025-03-03 09:30", periods=12, freq="5min", tz="America/New_York")
        c = np.r_[np.full(6, 100.0), np.linspace(100.5, 106, 6)]
        day = pd.DataFrame({"open": np.r_[c[0], c[:-1]], "high": c + 0.05, "low": c - 0.05, "close": c, "volume": 1e5}, index=idx)
        day.attrs["levels"] = [{"price": 101.5, "touches": 1, "strong": False}, {"price": 103.0, "touches": 3, "strong": True}]
        t = simulate_day("X", day, [Signal(i=5, side=1, stop_dist=1.0, atr=1.0)], Costs(0, 0, 0), zones=dict(ZONES, levels=True))[0]
        self.assertEqual(t.reason, "zone")
        self.assertAlmostEqual(t.exit_px, 103.0)                            # the strong zone, not the weak one at 101.5


class LiveZoneMapTests(unittest.TestCase):
    def test_shadow_direction_flags_trades_against_the_bias(self):
        from alpacabot import paper
        from alpacabot.strategies import Signal
        zm = {"bias": -1, "levels": [{"price": 95.0, "touches": 3, "strong": True}, {"price": 104.0, "touches": 2, "strong": True}]}
        long_ = paper.shadow_direction(zm, Signal(i=1, side=1, stop_dist=1.0, tag="donchian_turtle"), 100.0)
        short = paper.shadow_direction(zm, Signal(i=1, side=-1, stop_dist=1.0, tag="S:donchian_turtle"), 100.0)
        self.assertEqual((long_["against"], long_["map_target"]), (True, 104.0))
        self.assertEqual((short["against"], short["map_target"]), (False, 95.0))
        self.assertEqual(paper.shadow_direction(None, Signal(i=1), 100.0), {})

    def test_runner_builds_and_saves_the_zone_map_once_a_day(self):
        import tempfile
        from alpacabot import paper
        idx = pd.bdate_range("2026-03-02", periods=150, tz="America/New_York")
        c = 100 + np.arange(150) * 0.5
        d = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1e6}, index=idx)

        class Raw:
            calls = 0
            def daily_history(self, sym, days=220):
                Raw.calls += 1
                return d
        r = paper.PaperRunner.__new__(paper.PaperRunner)
        r.symbols, r.raw = ["AAA", "BBB"], Raw()
        with tempfile.TemporaryDirectory() as tmp:
            r.dir = Path(tmp)
            r.journal = lambda *a, **k: None
            r.refresh_zonemap(datetime(2026, 10, 14, 9, 30, tzinfo=paper.ET))
            saved = json.loads((Path(tmp) / "zonemap.json").read_text())
        self.assertEqual(Raw.calls, 2)
        self.assertEqual(saved["stocks"]["AAA"]["bias"], 1)
        self.assertIn("levels", saved["stocks"]["BBB"])
        self.assertEqual(saved["stocks"]["AAA"]["stage"], 0)            # 150 bars: not enough for the 30-week stage

    def test_weinstein_stage(self):
        from alpacabot.zonemap import stage
        idx = pd.bdate_range("2025-01-02", periods=200, tz="America/New_York")
        up = pd.DataFrame({"close": 100 + np.arange(200) * 0.5}, index=idx)
        down = pd.DataFrame({"close": 300 - np.arange(200) * 0.5}, index=idx)
        top = pd.DataFrame({"close": np.r_[100 + np.arange(150) * 1.0, 250 - np.arange(50) * 2.2]}, index=idx)
        self.assertEqual(stage(up), 2)
        self.assertEqual(stage(down), 4)
        self.assertEqual(stage(top), 1)          # broke below a still-rising average
        self.assertEqual(stage(up.iloc[:100]), 0)

    def test_stage_filter_and_repeat_cap(self):
        import tempfile
        from alpacabot import paper
        from alpacabot.strategies import Signal
        with tempfile.TemporaryDirectory() as tmp:
            r = paper.PaperRunner.__new__(paper.PaperRunner)
            r.dir, r.sleep = Path(tmp), (lambda *_: None)

            class B:
                def quote(self, s): return (100.0, 100.0)
            r.broker = B()
            cfg = r.zone_config()
            self.assertTrue(cfg["stage_filter"])
            self.assertEqual(cfg["repeat_max"], 10)
            r.zonemap = {"S3": {"bias": 1, "stage": 3, "levels": []}, "S2": {"bias": 1, "stage": 2, "levels": []}}
            long_ = Signal(i=1, side=1, stop_dist=1.0, atr=1.0, tag="donchian_turtle")
            self.assertEqual(r.zone_entry_block("S3", long_, 100.0, cfg), "stage 3 (needs 2)")
            self.assertEqual(r.zone_entry_block("S2", long_, 100.0, cfg), "")


class LiveZoneTests(unittest.TestCase):
    def _runner(self, tmp, quote=(100.0, 100.02)):
        from alpacabot import paper
        r = paper.PaperRunner.__new__(paper.PaperRunner)
        r.dir, r.sleep = Path(tmp), (lambda *_: None)

        class B:
            def quote(self, s): return quote
        r.broker = B()
        return r

    def test_the_range_box_is_live_in_the_shipped_zone_config_and_the_zone_map_carries_the_box(self):
        import tempfile
        from datetime import datetime as _dt
        from alpacabot.paper import ET
        with tempfile.TemporaryDirectory() as tmp:
            r = self._runner(tmp)
            self.assertTrue((r.zone_config().get("box") or {}).get("live"))        # owner 2026-10-16: live without waiting for the test
            d = StockRangeBoxTests()._daily(True)
            class Raw:
                def daily_history(self, sym, days=330): return d
            r.raw, r.symbols, r.heat = Raw(), ["AAA"], {}
            r.journal = lambda *a, **k: None
            r.refresh_zonemap(_dt.combine(d.index[-1].date(), _dt.min.time()).replace(tzinfo=ET) + pd.Timedelta(days=1))
            zb = r.zonemap["AAA"]
            valid, top, bot = zb["box"]
            self.assertTrue(valid)
            self.assertGreater(top, bot)
            self.assertGreater(zb["box_atr"], 0)

    def test_zone_config_defaults_to_live_and_is_editable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            r = self._runner(tmp)
            cfg = r.zone_config()
            self.assertTrue(cfg["live"] and cfg["direction_first"])
            self.assertTrue((Path(tmp) / "zones_override.json").exists())
            (Path(tmp) / "zones_override.json").write_text(json.dumps({"live": True, "profiles": {"trend": {"min_r": 1.5}}}))
            cfg = r.zone_config()
            self.assertEqual(cfg["profiles"]["trend"]["min_r"], 1.5)
            self.assertEqual(cfg["profiles"]["trend"]["default_r"], 2.0)          # untouched keys keep their defaults
            (Path(tmp) / "zones_override.json").write_text("{not json")
            self.assertFalse(r.zone_config()["live"])                              # a broken file switches zones off, never crashes

    def test_entry_block_direction_then_chase(self):
        import tempfile
        from alpacabot.strategies import Signal
        with tempfile.TemporaryDirectory() as tmp:
            r = self._runner(tmp, quote=(100.6, 100.62))
            cfg = r.zone_config()
            r.zonemap = {"UP": {"bias": 1, "levels": []}}
            short = Signal(i=1, side=-1, stop_dist=1.0, atr=1.0, tag="S:donchian_turtle")
            self.assertTrue(r.zone_entry_block("UP", short, 100.0, cfg).startswith("against direction"))
            long_ = Signal(i=1, side=1, stop_dist=1.0, atr=1.0, tag="donchian_turtle")
            self.assertTrue(r.zone_entry_block("UP", long_, 100.0, cfg).startswith("chase"))   # ask 0.62 above the bar close > 0.5 ATR
            self.assertEqual(r.zone_entry_block("UP", long_, 100.5, cfg), "")


class LadderTests(unittest.TestCase):
    """Owner's scaling zones (drawing, 2026-10-14): one 1%-risk entry; against the trade sell 25% / 50% before the stop; in the TP zone
    sell 25% (stop to break-even) then 50% (stop locked at the zone) and let the rest ride. Tier sizing is kept for a multi-tier config."""
    CFG = {"entry_risk": [0.0025, 0.005, 0.01, 0.02], "entry_depth_atr": [0.0, 0.25, 0.5, 1.0],
           "cut_fracs": [0.25, 0.5], "cut_depth_r": [0.5, 0.75],
           "tp_fracs": [0.25, 0.5], "tp_depth_atr": [0.0, 0.25], "lock": ["breakeven", "tp"], "ride": True}

    def _runner(self, tmp, quote=(100.0, 100.0)):
        from alpacabot import paper
        from alpacabot.exits import FixedState
        r = paper.PaperRunner.__new__(paper.PaperRunner)
        r.dir, r.sleep, r.now, r.last_px, r.base_equity = Path(tmp), (lambda *_: None), None, {}, 100_000.0
        r.closed, r.sent = [], []

        class B:
            def quote(self, s): return quote
            def submit_market(self, s, q, coid=None, side="buy"): r.sent.append((s, q, side, coid))
            def close_tagged(self, s, q, side, coid): r.sent.append((s, q, "close", coid))

        class Store:
            def save(self, p): pass

        class Ex:
            positions = {}
        r.broker = r.raw = B()
        r.store, r.exits = Store(), Ex()
        r.exits.positions = {"X": {"state": FixedState(entry=100.0, stop=98.0, tp=104.0, atr=1.0, trail=2.0), "qty": 100, "side": 1,
                                   "opened": "t0", "zone_tp": 102.0,
                                   "ladder": {"ref": 100.0, "atr": 1.0, "tier": 0, "stop_px": 98.0, "fill": 100.0, "peak_qty": 100, "sold": 0,
                                              "tp_steps": 0, "cfg": self.CFG}}}
        r.journal = lambda *a, **k: None
        r.close_now = lambda sym, reason, bar: r.closed.append((sym, reason)) or r.exits.positions.pop(sym)
        return r

    def test_tier_from_depth(self):
        import tempfile
        from alpacabot.strategies import Signal
        with tempfile.TemporaryDirectory() as tmp:
            sig = Signal(i=1, side=1, stop_dist=2.0, atr=1.0, tag="t")
            self.assertEqual(self._runner(tmp, (100.0, 100.0)).ladder_tier("X", sig, 100.0, self.CFG), 0)
            self.assertEqual(self._runner(tmp, (99.4, 99.4)).ladder_tier("X", sig, 100.0, self.CFG), 2)     # 0.6 ATR into the buy zone
            self.assertEqual(self._runner(tmp, (98.9, 98.9)).ladder_tier("X", sig, 100.0, self.CFG), 3)
            short = Signal(i=1, side=-1, stop_dist=2.0, atr=1.0, tag="S:t")
            self.assertEqual(self._runner(tmp, (100.3, 100.3)).ladder_tier("X", short, 100.0, self.CFG), 1)  # shorts: deeper = higher

    def test_take_profit_scales_out_locks_stop_and_rides(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            r = self._runner(tmp)
            now = datetime(2026, 10, 14, 11, 0, tzinfo=paper_ET())
            pos = r.exits.positions["X"]
            self.assertFalse(r.ladder_take("X", pos, 102.0, now))        # at the zone: 25% out, stop to break-even
            self.assertEqual(pos["qty"], 75)
            self.assertEqual(pos["state"].stop, 100.0)
            self.assertFalse(r.ladder_take("X", pos, 102.1, now))        # same step: nothing more
            self.assertEqual(pos["qty"], 75)
            self.assertFalse(r.ladder_take("X", pos, 102.3, now))        # 0.3 ATR beyond: 50% out in all, stop locked at the zone
            self.assertEqual(pos["qty"], 50)
            self.assertEqual(pos["state"].stop, 102.0)
            self.assertFalse(r.ladder_take("X", pos, 105.0, now))        # far beyond: the last half rides the trend
            self.assertEqual(pos["qty"], 50)
            self.assertEqual(r.closed, [])
            rows = [json.loads(l) for l in (Path(tmp) / "trades.jsonl").read_text().splitlines()]
            self.assertEqual([(t["qty"], t["reason"]) for t in rows], [(25, "zone25"), (25, "zone50")])
            self.assertAlmostEqual(rows[0]["pnl"], 50.0)

    def test_rider_is_followed_by_a_trailing_virtual_stop(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            r = self._runner(tmp)
            now = datetime(2026, 10, 14, 11, 0, tzinfo=paper_ET())
            pos = r.exits.positions["X"]
            pos["ladder"]["cfg"] = {**self.CFG, "trail_atr": 1.0}
            r.ladder_take("X", pos, 102.0, now)
            r.ladder_take("X", pos, 102.3, now)                              # both TP steps done: stop at the zone (102)
            pos["best_px"] = 104.5
            lad = pos["ladder"]
            r.lock_stop("X", pos, pos["best_px"] - lad["cfg"]["trail_atr"] * lad["atr"], quiet=True)
            self.assertEqual(pos["state"].stop, 103.5)                      # 1 ATR behind the best price
            r.lock_stop("X", pos, 103.0, quiet=True)
            self.assertEqual(pos["state"].stop, 103.5)                      # never loosens

    def test_against_the_trade_cuts_in_steps(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            r = self._runner(tmp)
            now = datetime(2026, 10, 14, 11, 0, tzinfo=paper_ET())
            pos = r.exits.positions["X"]
            self.assertFalse(r.ladder_cut("X", pos, 99.5, now))           # a quarter of the way to the stop: hold
            self.assertEqual(pos["qty"], 100)
            self.assertFalse(r.ladder_cut("X", pos, 99.0, now))           # half way: sell 25%
            self.assertEqual(pos["qty"], 75)
            self.assertFalse(r.ladder_cut("X", pos, 98.5, now))           # three quarters: 50% out in all
            self.assertEqual(pos["qty"], 50)
            self.assertFalse(r.ladder_cut("X", pos, 98.4, now))           # the stop (98) takes the rest, not the cut ladder
            self.assertEqual(pos["qty"], 50)
            rows = [json.loads(l) for l in (Path(tmp) / "trades.jsonl").read_text().splitlines()]
            self.assertEqual([t["reason"] for t in rows], ["cut25", "cut50"])
            self.assertTrue(r.ladder_take("X", pos, 102.0, now) is False and pos["qty"] == 50)   # 25% TP already covered: nothing sold

    def test_whole_trades_folds_partials(self):
        from alpacabot import paper
        rows = [{"symbol": "X", "opened": "t0", "pnl": 50.0, "r": 0.5, "qty": 25, "partial": True},
                {"symbol": "X", "opened": "t0", "pnl": -10.0, "r": -0.1, "qty": 75},
                {"symbol": "Y", "opened": "t1", "pnl": 5.0, "r": 0.1, "qty": 10}]
        w = paper.whole_trades(rows)
        self.assertEqual(len(w), 2)
        self.assertEqual((w[0]["pnl"], w[0]["qty"], w[0]["partials"]), (40.0, 100, 1))


def paper_ET():
    from alpacabot.paper import ET
    return ET


class RegimeSwitchTests(unittest.TestCase):
    def test_mean_reversion_is_off_while_spy_trends(self):
        from types import SimpleNamespace as NS
        from alpacabot.paper import PaperRunner
        cfg = {"regime_switch": {"live": True, "adx_max": 20.0}}
        me = NS(zonemap={"SPY": {"adx": 27.3}})
        f = PaperRunner.regime_block
        self.assertIn("ADX 27.3", f(me, NS(tag="connors_rsi2"), cfg))
        self.assertIn("ADX", f(me, NS(tag="S:velez_180"), cfg))
        self.assertEqual(f(me, NS(tag="raschke_grail"), cfg), "")          # trend experts unaffected
        self.assertEqual(f(me, NS(tag="range_box"), cfg), "")              # box strategies unaffected
        self.assertEqual(f(NS(zonemap={"SPY": {"adx": 15.0}}), NS(tag="connors_rsi2"), cfg), "")
        self.assertEqual(f(NS(zonemap={}), NS(tag="connors_rsi2"), cfg), "")  # unknown ADX = allowed
        self.assertEqual(f(me, NS(tag="connors_rsi2"), {"regime_switch": {"live": False}}), "")


class SwingZoneLiveTests(unittest.TestCase):
    class B:
        def __init__(self, ask=101.0):
            self.sent, self.oco, self.orders, self.ask = [], [], {}, ask
        def equity(self): return 50000.0
        def is_session(self, day): return True
        def quote(self, s): return (self.ask - 0.02, self.ask)
        def submit_order(self, *a, **k):
            self.sent.append(a)
            return {"id": a[0], "status": "filled", "filled_qty": str(a[1]), "filled_avg_price": "100"}
        def submit_oco(self, sym, qty, limit, stop, coid):
            self.oco.append((sym, qty, limit, stop))
            o = {"id": "oco-" + sym, "status": "new", "legs": [{"id": "leg-" + sym, "status": "new"}]}
            self.orders[o["id"]] = o
            return o
        def order(self, oid): return self.orders.get(oid, {"id": oid, "status": "filled", "filled_qty": "5", "filled_avg_price": "100"})
        def cancel_order(self, oid): pass

    def _runner(self, b, tmp):
        from alpacabot.swing_paper import SwingRunner
        r = SwingRunner(b, state_dir=tmp, fetch=lambda *a: pd.DataFrame(), sleep=lambda *_: None)
        r.state["plan"] = {"made": "2025-10-13", "exits": [], "entries": [
            {"symbol": "AMD", "qty": 5, "stop": 95.0, "rs": 0.1, "signal_close": 100.0, "zone_tp": 110.0, "atr": 2.0}]}
        return r

    def test_entry_places_an_oco_with_the_zone_take_profit_and_settles_both_ways(self):
        import tempfile
        et = __import__("zoneinfo").ZoneInfo("America/New_York")
        with tempfile.TemporaryDirectory() as d:
            b = self.B(ask=100.5)
            r = self._runner(b, d)
            r.open(datetime(2025, 10, 14, 9, 20, tzinfo=et), wait_until=datetime(2025, 10, 14, 9, 20, tzinfo=et))
            self.assertEqual(b.oco, [("AMD", 5, 110.0, 95.0)])
            self.assertTrue(r.state["positions"]["AMD"]["oco"])
            b.orders["oco-AMD"].update(status="filled", filled_avg_price="110.0")      # the take-profit leg fills
            r.settle_stops(datetime(2025, 10, 15, 16, 20, tzinfo=et))
            t = json.loads((Path(d) / "trades.jsonl").read_text().splitlines()[-1])
            self.assertEqual((t["reason"], t["exit"]), ("zone", 110.0))

    def test_owner_close_cancels_the_oco_sells_and_books_it(self):
        import tempfile
        et = __import__("zoneinfo").ZoneInfo("America/New_York")
        with tempfile.TemporaryDirectory() as d:
            b = self.B(ask=100.5)
            r = self._runner(b, d)
            r.open(datetime(2025, 10, 14, 9, 20, tzinfo=et), wait_until=datetime(2025, 10, 14, 9, 20, tzinfo=et))
            cancelled = []
            b.cancel_order = cancelled.append
            out = r.close_all(datetime(2025, 10, 15, 12, 10, tzinfo=et))
            self.assertEqual(cancelled, ["oco-AMD"])
            self.assertEqual(b.sent[-1][:3], ("AMD", 5, "sell"))
            self.assertEqual(r.state["positions"], {})
            self.assertIn("closed", out["AMD"])
            t = json.loads((Path(d) / "trades.jsonl").read_text().splitlines()[-1])
            self.assertEqual(t["reason"], "owner_close")

    def test_gap_past_the_entry_zone_is_skipped(self):
        import tempfile
        et = __import__("zoneinfo").ZoneInfo("America/New_York")
        with tempfile.TemporaryDirectory() as d:
            b = self.B(ask=102.5)                                                          # 2.5 above the signal close > 0.5 x ATR 2
            r = self._runner(b, d)
            r.open(datetime(2025, 10, 14, 9, 20, tzinfo=et), wait_until=datetime(2025, 10, 14, 9, 20, tzinfo=et))
            self.assertEqual(b.sent, [])
            self.assertNotIn("AMD", r.state["positions"])

    def test_swing_zone_helper(self):
        from alpacabot.engine import ZONE_PROFILES
        from alpacabot.swing_paper import swing_zone
        idx = pd.bdate_range("2025-01-02", periods=60, tz="America/New_York")
        c = np.r_[np.full(30, 120.0), np.full(30, 100.0)]
        d = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1e6}, index=idx)
        z = swing_zone(d, 100.0, 95.0, {"use_map": False, "profiles": ZONE_PROFILES})
        self.assertEqual(z["zone_tp"], 110.0)                                               # prior 20-day high 101 < 1R away -> 2R


class RepoZoneConfigTests(unittest.TestCase):
    def test_repo_file_is_valid_and_matches_the_engine_profiles(self):
        from alpacabot.engine import ZONE_PROFILES
        cfg = json.loads((Path(__file__).resolve().parents[1] / "alpacabot" / "zones.json").read_text())
        self.assertTrue(cfg["intraday"]["live"] and cfg["swing"]["live"])
        for k in ("trend", "range"):
            self.assertEqual(set(cfg["intraday"]["profiles"][k]), set(ZONE_PROFILES[k]))
