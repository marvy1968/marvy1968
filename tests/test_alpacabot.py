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
        down, up = heat_state(self._daily(-0.004)), heat_state(self._daily(0.004))
        self.assertEqual(down.iloc[-1], -1)
        self.assertEqual(up.iloc[-1], 1)
        d = self._daily(-0.004)
        full = heat_state(d)
        cut = heat_state(d.iloc[:-1])                   # the last day's own bar is not used for that day's state
        self.assertTrue(full.iloc[:-1].equals(cut))

    def test_gates_block_longs_and_allow_shorts_only_when_bearish(self):
        from alpacabot import gates
        day = pd.date_range("2024-04-23 09:30", periods=10, freq="5min", tz="America/New_York")
        d = pd.DataFrame({"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 1.0}, index=day)
        bear = pd.Series({day[0].date(): -1.0})
        neutral = pd.Series({day[0].date(): 0.0})
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
