import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from alpacabot import vwapday as vd


def sd_from(c, v=None, spread=0.0005):
    c = np.asarray(c, float)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + spread)
    l = np.minimum(o, c) * (1 - spread)
    v = np.full(len(c), 1000.0) if v is None else np.asarray(v, float)
    return {"o": o, "h": h, "l": l, "c": c, "v": v, "n": len(c)}


def minute_bars(seed, days, drift=0.0, vol=0.0008, base=100.0, trend_days=()):
    rng = np.random.default_rng(seed)
    out, px = [], base
    for k, d in enumerate(days):
        mu = drift + (0.0004 if k in trend_days else 0.0)
        r = mu + rng.normal(0, vol, 390)
        c = px * np.exp(np.cumsum(r))
        o = np.r_[px, c[:-1]]
        idx = pd.date_range(pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=30), periods=390, freq="1min", tz=vd.NY)
        v = rng.uniform(5e4, 2e5, 390) * (3 if k % 2 == 0 else 1)
        out.append(pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.0004, "low": np.minimum(o, c) * 0.9996, "close": c, "volume": v}, index=idx))
        px = c[-1]
    return pd.concat(out)


class VwapDayTests(unittest.TestCase):
    def test_vwap_sigma(self):
        sd = sd_from([10, 10, 10, 10])
        vw, sg = vd.vwap_sigma(sd)
        self.assertTrue(np.allclose(vw, 10.0, atol=0.01))
        sd = sd_from([10, 11, 12], v=[1, 1, 2])
        vw, _ = vd.vwap_sigma(sd)
        self.assertGreater(vw[-1], vw[0])

    def test_costs(self):
        R = vd.r_of([(5, 1.0, 101.0, "x")], 100.0, 1.0, 1, 1.0)
        self.assertAlmostEqual(R, 1.0 - 100 * vd.CB - 101 * (vd.CB + vd.SEC), places=6)

    def test_orb_stop_first_and_eod(self):
        c = np.full(390, 100.0)
        sd = sd_from(c, spread=0.0)
        sd["l"][40], sd["h"][40] = 98.5, 103.0                 # stop and a +3R move in one bar: stop first
        ev, mfe, mae, st = vd.walk("orb", sd, 1, 35, 100.0, 99.0)
        self.assertEqual((ev[-1][0], ev[-1][2]), (40, 99.0))
        sd = sd_from(np.full(390, 100.0), spread=0.0)
        ev, *_ = vd.walk("orb", sd, 1, 35, 100.0, 99.0)
        self.assertEqual(ev[-1][3], "eod_1550")
        self.assertEqual(ev[-1][0], 380)

    def test_orb_trail_after_one_r(self):
        c = np.r_[np.full(36, 100.0), np.linspace(100, 103, 20), np.full(334, 103.0)]
        sd = sd_from(c, spread=0.0)
        sd["l"][100] = 99.5                                       # below break-even after the trail is on
        ev, *_ = vd.walk("orb", sd, 1, 36, 100.0, 99.0)
        self.assertIn(ev[-1][3], ("trail", "vwap"))
        self.assertGreaterEqual(ev[-1][2], 99.99)

    def test_band_target_vwap(self):
        c = np.r_[np.full(40, 100.0), np.full(10, 98.0), np.full(340, 100.0)]
        sd = sd_from(c, spread=0.0)
        ev, *_ = vd.walk("band", sd, 1, 45, 98.0, 97.0)
        self.assertEqual(ev[-1][3], "target")
        self.assertGreater(ev[-1][2], 98.0)

    def test_signals_do_not_see_the_future(self):
        days = pd.bdate_range("2024-01-02", periods=1)
        m = vd.minute_frame(minute_bars(3, days, drift=0.0003))
        sd = vd.symbol_days(m, {days[0].date()}, {days[0].date(): 390})[days[0].date()]
        spy = vd.symbol_days(vd.minute_frame(minute_bars(4, days, drift=0.0003)), {days[0].date()}, {days[0].date(): 390})[days[0].date()]
        dd = {"atrd": 2.0, "prev_close": 100.0}
        for fn, p in ((vd.sig_orb, {"or": 30, "k": 0.10}), (vd.sig_reclaim, {}), (vd.sig_band, {"ks": 1.5, "adx": 99})):
            sd["adx5"] = np.full(78, 10.0, dtype=np.float32)
            sig, _ = fn(sd, spy, dd, 1, p)
            if not sig:
                continue
            j = sig["j"]
            sd2 = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in sd.items()}
            for k in ("o", "h", "l", "c"):
                sd2[k][j + 1:] = sd2[k][j + 1:] * 1.5
            sig2, _ = fn(sd2, spy, dd, 1, p)
            self.assertEqual(sig["j"], sig2["j"])
            self.assertAlmostEqual(sig["stop"], sig2["stop"], places=4)

    def test_end_to_end_synthetic(self):
        pool = {"XLK": ["AAA", "BBB", "CCC", "DDD"], "XLF": ["EEE", "FFF", "GGG"]}
        days = pd.bdate_range("2023-07-03", "2024-03-29")
        cache = {}

        def load(sym, tf, a, b):
            if (sym, tf) not in cache:
                seed = abs(hash(sym)) % 1000
                if tf == "1Min":
                    cache[(sym, tf)] = minute_bars(seed, days, drift=0.00002 * (seed % 5 - 2), vol=0.0016, trend_days=set(range(0, len(days), 3)))
                else:
                    d = pd.bdate_range("2022-01-03", "2024-03-29")
                    rng = np.random.default_rng(seed)
                    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, len(d))))
                    cache[(sym, tf)] = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 5e6}, index=d.tz_localize(vd.NY))
            f = cache[(sym, tf)]
            return f[(f.index.date >= a) & (f.index.date <= b)]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(vd, "POOL", pool), mock.patch.object(vd, "SECTOR", {s: e for e, ss in pool.items() for s in ss}), \
                mock.patch.object(vd, "ETFS", ["XLK", "XLF"]), mock.patch.object(vd, "MIN_TRAIN", 3), mock.patch.object(vd, "TRAIN", (date(2023, 7, 1), date(2023, 12, 31))), \
                mock.patch.object(vd, "GRIDS", {"orb": [{"or": 30, "k": 0.10}, {"or": 30, "k": 0.15}], "reclaim": [{}], "velezc": [{}], "band": [{"ks": 1.5, "adx": 99}]}), \
                mock.patch("alpacabot.universe.SECTOR_TOP5", {"XLK": ["AAA", "BBB", "CCC"], "XLF": ["EEE", "FFF", "GGG"]}):
            lines = vd.main(load, date(2024, 3, 30), Path(tmp), log=lambda m: None, seeds=2, start=date(2023, 7, 3))
            self.assertTrue(any(x.startswith("- verdict") for x in lines))
            out = next(Path(tmp).iterdir())
            for f in ("summary.md", "config.json", "universe_log.csv", "trades.csv", "signals.csv"):
                self.assertTrue((out / f).exists(), f)
            self.assertGreater((out / "trades.csv").stat().st_size, 10, "no trades at all in the synthetic run")
            tr = pd.read_csv(out / "trades.csv")
            self.assertGreater(tr["variant"].str.startswith("orb").sum() + tr["variant"].str.startswith("reclaim").sum(), 0)
            if len(tr):
                self.assertTrue(((tr["entry_min"] >= 30) & (tr["entry_min"] <= 375)).all())
                self.assertTrue(tr.groupby(["variant", "date"]).size().max() <= 4)

    def test_quick_two_week_sample(self):
        pool = {"XLK": ["AAA", "BBB", "CCC"], "XLF": ["EEE", "FFF", "GGG"]}
        days = pd.bdate_range("2024-01-02", "2024-05-31")
        cache = {}

        def load(sym, tf, a, b):
            if (sym, tf) not in cache:
                seed = abs(hash(sym)) % 1000
                if tf == "1Min":
                    cache[(sym, tf)] = minute_bars(seed, days, vol=0.0016, trend_days=set(range(0, len(days), 3)))
                else:
                    d = pd.bdate_range("2022-06-01", "2024-05-31")
                    rng = np.random.default_rng(seed)
                    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, len(d))))
                    cache[(sym, tf)] = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 5e6}, index=d.tz_localize(vd.NY))
            f = cache[(sym, tf)]
            return f[(f.index.date >= a) & (f.index.date <= b)]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(vd, "SECTOR", {s: e for e, ss in pool.items() for s in ss}), \
                mock.patch.object(vd, "ETFS", ["XLK", "XLF"]):
            lines = vd.quick(load, date(2024, 6, 1), Path(tmp), log=lambda m: None)
            self.assertIn("2024-05-20..2024-05-31", lines[0])
            self.assertEqual(sum(1 for x in lines if x.startswith("| orb") or x.startswith("| reclaim") or x.startswith("| band") or x.startswith("| velezc")), 8)

    def test_short_test_with_controls(self):
        pool = {"XLK": ["AAA", "BBB", "CCC"], "XLF": ["EEE", "FFF", "GGG"]}
        days = pd.bdate_range("2024-01-02", "2024-05-31")
        cache = {}

        def load(sym, tf, a, b):
            if (sym, tf) not in cache:
                seed = abs(hash(sym)) % 1000
                if tf == "1Min":
                    cache[(sym, tf)] = minute_bars(seed, days, vol=0.0016, trend_days=set(range(0, len(days), 3)))
                else:
                    d = pd.bdate_range("2022-06-01", "2024-05-31")
                    rng = np.random.default_rng(seed)
                    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, len(d))))
                    cache[(sym, tf)] = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 5e6}, index=d.tz_localize(vd.NY))
            f = cache[(sym, tf)]
            return f[(f.index.date >= a) & (f.index.date <= b)]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(vd, "SECTOR", {s: e for e, ss in pool.items() for s in ss}), \
                mock.patch.object(vd, "ETFS", ["XLK", "XLF"]):
            lines = vd.quick(load, date(2024, 6, 1), Path(tmp), sessions=None, days_back=150, seeds=2, name="short100", log=lambda m: None)
            self.assertIn("short test", lines[0])
            self.assertIn("Clears sample bar", lines[1])
            rows = [x for x in lines if x.startswith(("| orb", "| velezc", "| reclaim", "| band"))]
            self.assertEqual(len(rows), 8)
            self.assertTrue(all(r.count("/2") >= 2 for r in rows))
            self.assertTrue(any(x.startswith("- velezc") or x.startswith("- orb") for x in lines))
            self.assertTrue((next(Path(tmp).iterdir()) / "summary.md").exists())

    def test_velezc_signals_do_not_see_the_future(self):
        days = pd.bdate_range("2024-01-02", periods=14)
        mf = vd.minute_frame(minute_bars(5, days, drift=0.00004, vol=0.0009))
        want = {days[-1].date()}
        full = vd.velezc_by_day(mf, want)[days[-1].date()]
        cut = mf[(mf["d"] < days[-1].date()) | (mf["m"] < 240)]
        part = vd.velezc_by_day(cut, want)[days[-1].date()]
        for name in ("l", "s"):
            np.testing.assert_array_equal(full[name][:119], part[name][:119], err_msg=name)
        for name in ("raw_l", "raw_s", "atr", "sma20"):
            np.testing.assert_allclose(full[name][:119], part[name][:119], equal_nan=True, rtol=1e-5, err_msg=name)

    def test_velezc_walk_long_and_short(self):
        n = 390

        def sd_flat(level=100.0):
            sd = {k: np.full(n, level) for k in ("o", "h", "l", "c")}
            sd.update(n=n, v=np.full(n, 1000.0))
            sd["vc"] = {"sma20": np.full(195, 99.0, dtype=np.float32)}
            return sd

        def lift(sd, frm, level):                                  # price sits at `level` from minute `frm` on
            for k in ("o", "h", "l", "c"):
                sd[k][frm:] = level
        # long: +2R half (102), then +3R (103)
        sd = sd_flat()
        sd["h"][50] = 102.2
        lift(sd, 51, 102.5)
        sd["h"][60] = 103.1
        ev, *_ = vd.walk("velezc", sd, 1, 40, 100.0, 99.0)
        self.assertEqual([x[3] for x in ev], ["tp1", "t2"])
        self.assertAlmostEqual(vd.r_of(ev, 100.0, 1.0, 1, 0.0), 0.5 * 2 + 0.5 * 3, places=1)
        # long: half off, then the break-even stop
        sd = sd_flat()
        sd["h"][50] = 102.2
        lift(sd, 51, 101.0)
        sd["l"][70] = 99.9
        ev, *_ = vd.walk("velezc", sd, 1, 40, 100.0, 99.0)
        self.assertEqual([x[3] for x in ev], ["tp1", "be"])
        # short mirror: +2R half off then +3R
        sd = sd_flat()
        sd["l"][50] = 97.8
        lift(sd, 51, 97.5)
        sd["l"][60] = 96.9
        ev, *_ = vd.walk("velezc", sd, -1, 40, 100.0, 101.0)
        self.assertEqual([x[3] for x in ev], ["tp1", "t2"])
        # trail: after the half, a 2-minute close below SMA20 (99) ends the rest at the next open
        sd = sd_flat()
        sd["h"][50] = 102.2
        lift(sd, 51, 101.0)
        sd["c"][81] = 98.5
        ev, *_ = vd.walk("velezc", sd, 1, 40, 100.0, 99.0)
        self.assertEqual([x[3] for x in ev], ["tp1", "trail"])
        self.assertEqual(ev[-1][0], 82)

    def test_velezc_daily_cap_is_four_r(self):
        self.assertEqual(vd.DAILY_CAP_R.get("velezc"), 4.0)
        self.assertEqual(vd.DAILY_CAP_R.get("orb", 2.0), 2.0)


if __name__ == "__main__":
    unittest.main()
