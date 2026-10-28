import unittest
from datetime import date

import numpy as np
import pandas as pd

from fxbot import newsfilter as nf


def _bars(start="2024-03-01", n=24 * 30, px=1.1):
    idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
    mid = px + np.cumsum(np.random.default_rng(0).normal(0, 0.0005, n))
    d = pd.DataFrame(index=idx)
    for k in "ohlc":
        d["b" + k], d["a" + k] = mid - 0.00005, mid + 0.00005
    d["bh"], d["ah"] = d["bh"] + 0.0003, d["ah"] + 0.0003
    d["bl"], d["al"] = d["bl"] - 0.0003, d["al"] - 0.0003
    d["volume"] = 100.0
    return d


class CalendarTests(unittest.TestCase):
    def test_parse_bls_reads_release_dates(self):
        html = '<a href="/news.release/archives/cpi_10102024.htm">x</a> <a href="/news.release/archives/cpi_09112024.htm">y</a> cpi_13452024.htm'
        self.assertEqual(nf.parse_bls("cpi", html), [date(2024, 9, 11), date(2024, 10, 10)])

    def test_nfp_rule_matches_known_dates(self):
        self.assertEqual(nf.nfp_rule(2024, 11), date(2024, 11, 1))
        self.assertEqual(nf.nfp_rule(2024, 10), date(2024, 10, 4))
        self.assertEqual(nf.nfp_rule(2025, 6), date(2025, 6, 6))

    def test_calendar_without_network_uses_rule_and_fomc(self):
        ev, notes = nf.calendar(date(2024, 1, 1), date(2024, 12, 31))
        kinds = {k for _, k in ev}
        self.assertEqual(kinds, {"nfp", "fomc"})
        fomc = [t for t, k in ev if k == "fomc"]
        self.assertEqual(len(fomc), 8)
        self.assertEqual(fomc[0], pd.Timestamp("2024-01-31 19:00", tz="UTC"))        # 14:00 New York in winter
        self.assertTrue(any("left out" in n for n in notes))

    def test_fake_calendar_keeps_count_and_avoids_real_days(self):
        ev, _ = nf.calendar(date(2024, 1, 1), date(2024, 12, 31))
        fake = nf.fake_calendar(ev, date(2024, 1, 1), date(2024, 12, 31), 3)
        self.assertEqual(len(fake), len(ev))
        real = {t.tz_convert(nf.NY).date() for t, _ in ev}
        self.assertTrue(all(min(abs((t.tz_convert(nf.NY).date() - r).days) for r in real) >= 2 for t, _ in fake))


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.df = _bars()
        self.ev = [(pd.Timestamp("2024-03-08 13:30", tz="UTC"), "nfp")]

    def _t(self, entry, exit_, side=1):
        e, x = pd.Timestamp(entry, tz="UTC"), pd.Timestamp(exit_, tz="UTC")
        fill = float(self.df["ao"].loc[e])
        return {"inst": "EUR_USD", "side": side, "entry": e, "exit": x, "fill": fill, "risk": 0.002, "r": 0.5, "fin": 0.0}

    def test_entry_inside_window_is_skipped(self):
        tr = [self._t("2024-03-08 12:00", "2024-03-08 18:00"), self._t("2024-03-08 14:00", "2024-03-08 18:00"),
              self._t("2024-03-08 15:00", "2024-03-08 18:00"), self._t("2024-03-08 10:00", "2024-03-08 11:00")]
        out = nf.apply(tr, self.ev, {"EUR_USD": self.df}, flat=False)
        self.assertEqual([t["entry"].hour for t in out], [15, 10])

    def test_open_trade_closed_before_release(self):
        t = self._t("2024-03-07 20:00", "2024-03-08 20:00")
        out = nf.apply([t], self.ev, {"EUR_USD": self.df}, flat=True)
        self.assertEqual(out[0]["exit"], pd.Timestamp("2024-03-08 13:00", tz="UTC"))
        exp = (float(self.df["bo"].loc["2024-03-08 13:00"]) - t["fill"]) / 0.002
        self.assertAlmostEqual(out[0]["r"], exp)
        untouched = nf.apply([t], self.ev, {"EUR_USD": self.df}, flat=False)
        self.assertEqual(untouched[0]["r"], 0.5)

    def test_near_news_split(self):
        a, b = nf.near_news([self._t("2024-03-08 09:00", "2024-03-08 16:00"), self._t("2024-03-05 09:00", "2024-03-05 16:00")], self.ev)
        self.assertEqual((len(a), len(b)), (1, 1))


if __name__ == "__main__":
    unittest.main()
