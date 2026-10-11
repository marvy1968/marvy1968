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

    def test_short_is_not_supported_yet(self):
        with self.assertRaises(NotImplementedError):
            simulate_day("X", flat_day(), [Signal(100, -1, 1.0, 1.0)], self.costs)


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
