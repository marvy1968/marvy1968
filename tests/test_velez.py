import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from alpacabot import velez as ve
from alpacabot import vwapday as vd

ET = ZoneInfo("America/New_York")


def minutes(seed, days, trend_days=(), base=100.0, gap_days=()):
    rng = np.random.default_rng(seed)
    out, px = [], base
    for k, d in enumerate(days):
        if k in gap_days:
            px *= 1.02
        mu = 0.00025 if k in trend_days else 0.0
        r = mu + rng.normal(0, 0.0007, 390)
        c = px * np.exp(np.cumsum(r))
        o = np.r_[px, c[:-1]]
        v = rng.uniform(5e3, 2e4, 390) * np.r_[np.full(5, 4.0 if k in gap_days else 1.0), np.ones(385)]
        idx = pd.date_range(pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=30), periods=390, freq="1min", tz=ET)
        out.append(pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.0003, "low": np.minimum(o, c) * 0.9997, "close": c, "volume": v}, index=idx))
        px = c[-1]
    return pd.concat(out)


class VelezTests(unittest.TestCase):
    def test_features_no_lookahead(self):
        days = pd.bdate_range("2026-06-01", periods=12)
        mf = vd.minute_frame(minutes(1, days, trend_days=set(range(12))))
        f_full = ve.features(mf)
        cut = mf[(mf["d"] < days[-1].date()) | (mf["m"] < 200)]
        f_cut = ve.features(cut)
        a = f_full[(f_full["d"] == days[-1].date()) & (f_full["close_min"] < 199)].reset_index(drop=True)
        b = f_cut[(f_cut["d"] == days[-1].date()) & (f_cut["close_min"] < 199)].reset_index(drop=True)
        self.assertTrue(len(a) > 50)
        for col in ("trend", "exh", "votes", "sma20", "stop_t"):
            np.testing.assert_array_equal(a[col].to_numpy(), b[col].to_numpy(), err_msg=col) if a[col].dtype == bool else \
                np.testing.assert_allclose(a[col].to_numpy(), b[col].to_numpy(), equal_nan=True, err_msg=col)

    def test_walk_half_at_2r_then_breakeven(self):
        n = 390
        sd = {"o": np.full(n, 100.0), "h": np.full(n, 100.0), "l": np.full(n, 100.0), "n": n}
        sd["h"][50] = 102.1                                     # +2R with R = 1: half off at 102, stop to 100
        sd["l"][60] = 99.9                                      # back through break-even
        fd = pd.DataFrame({"close_min": [], "c": [], "sma20": []})
        ev = ve.walk(sd, 40, 100.0, 99.0, fd)
        self.assertEqual([x[3] for x in ev], ["t1", "be"])
        self.assertAlmostEqual(ve.r_of(ev, 100.0, 1.0, 0.0), 1.0, places=2)

    def test_stop_floor(self):
        r = {"atr": 1.0, "stop_t": 99.9, "stop_x": 99.0}
        self.assertAlmostEqual(ve.stop_for(r, 100.0, "trend"), 99.5)

    def test_backtest_runs(self):
        days = pd.bdate_range("2026-06-01", "2026-09-30")
        syms = ["AAA", "BBB", "CCC"]
        data = {s: minutes(i + 3, days, trend_days=set(range(0, len(days), 2)), gap_days=set(range(1, len(days), 3))) for i, s in enumerate(syms + ["SPY"])}

        def load(sym, tf, a, b):
            if tf == "1Min":
                f = data[sym]
            else:
                f = data[sym].resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
                f.index = f.index.normalize()
            return f[(f.index.date >= a) & (f.index.date <= b)]
        with tempfile.TemporaryDirectory() as tmp, mock.patch("alpacabot.universe.STOCKS", syms), mock.patch.dict(ve.CFG, {"adv_sh": 1}):
            lines = ve.backtest(load, date(2026, 10, 1), Path(tmp), log=lambda m: None, seeds=2, days_back=110)
            self.assertTrue(lines[0].startswith("## Velez"))
            self.assertTrue(any(x.startswith("| all") for x in lines))

    def test_live_day_runs_and_flattens(self):
        from tests.test_vwaplive import FakeBroker
        import tests.test_vwaplive as tv
        days = pd.bdate_range(end="2026-11-03", periods=70)
        syms = ["AAA", "BBB"]
        data = {s: minutes(i + 7, days, trend_days=set(range(len(days) - 8, len(days))), gap_days={len(days) - 1}) for i, s in enumerate(syms)}

        def fetch(sym, tf, start, end, feed):
            a, b = pd.Timestamp(start), pd.Timestamp(end)
            if tf == "1Day":
                f = data[sym].resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
                f = f[f.index.date < date(2026, 11, 3)]
                return f[(f.index >= a) & (f.index < b)].iloc[-120:]
            m = data[sym]
            return m[(m.index >= a) & (m.index + pd.Timedelta(minutes=1) <= b)]
        with tempfile.TemporaryDirectory() as d, mock.patch("alpacabot.universe.STOCKS", syms), mock.patch.dict(tv.MIN, data):
            b = FakeBroker()
            live = ve.VelezLive(b, state_dir=d, cfg={**ve.VELEZ_LIVE, "adv_sh": 1, "start_date": ""}, fetch=fetch, sleep=lambda *_: None)
            t = datetime(2026, 11, 3, 9, 34, 10, tzinfo=ET)
            while t <= datetime(2026, 11, 3, 15, 52, 10, tzinfo=ET):
                b.now = pd.Timestamp(t) - pd.Timedelta(minutes=1)
                live.step(t)
                t += timedelta(minutes=1)
            with open(f"{d}/journal.jsonl") as fh:
                j = [json.loads(x) for x in fh]
            self.assertTrue(any(x["kind"] == "inplay" for x in j))
            self.assertTrue(any(x["kind"] == "open" for x in j), "the live test day took no trade")
            self.assertEqual(b.positions(), {})
            self.assertEqual(live.state["open"], {})
            for x in j:
                if x["kind"] == "open":
                    self.assertLessEqual(x["qty"] * abs(x["fill"] - x["stop"]), 0.001 * 100_000 + 1e-6)


if __name__ == "__main__":
    unittest.main()
