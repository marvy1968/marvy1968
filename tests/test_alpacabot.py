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
