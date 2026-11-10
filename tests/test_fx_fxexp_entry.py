import datetime as dt
import unittest

import numpy as np
import pandas as pd

from fxbot import fxexp_entry as fe


def _h1(days=900, seed=1, px=1.10):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=pd.Timestamp("2026-11-06 20:00", tz="UTC"), periods=days * 24, freq="h")
    idx = idx[idx.dayofweek < 5]
    c = px * np.exp(np.cumsum(rng.normal(0, 0.0012, len(idx))))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + 0.0004)
    l = np.minimum(o, c) * (1 - 0.0004)
    s = 0.00007
    return pd.DataFrame({"bo": o - s, "bh": h - s, "bl": l - s, "bc": c - s,
                         "ao": o + s, "ah": h + s, "al": l + s, "ac": c + s, "volume": 100.0}, index=idx)


class FxexpEntryTest(unittest.TestCase):
    def test_groups_cover_the_daily_systems_and_rule_is_fixed(self):
        self.assertEqual(set(fe.STOP) & set(fe.OTHER), set())
        self.assertIn("pre-registered", fe.ENTRY_RULE)
        _, _, strategies = fe._fxexp()
        for sid in fe.STOP + fe.OTHER:
            self.assertFalse(getattr(strategies.BY_SID[sid], "intraday", False), sid)

    def test_to_fxexp_makes_mid_bars(self):
        df = fe.to_fxexp(_h1(30))
        self.assertEqual(list(df.columns), ["time", "open", "high", "low", "close"])
        self.assertTrue((df["high"] >= df["low"]).all())

    def test_double_spread_costs_more_and_is_restored(self):
        bt, _, _ = fe._fxexp()
        before = bt.spread("EUR_USD")
        frames = {"EUR_USD": fe.to_fxexp(_h1(500))}
        a = fe.run_all(frames, ("williams-vbo",))["williams-vbo"]
        b = fe.run_all(frames, ("williams-vbo",), spread_mult=2.0)["williams-vbo"]
        self.assertEqual(bt.spread("EUR_USD"), before)
        self.assertTrue(a and b)
        self.assertLess(sum(t.r for t in b) / len(b), sum(t.r for t in a) / len(a))

    def test_report_runs_and_gives_a_verdict(self):
        lines = fe.report({"EUR_USD": _h1(400), "USD_JPY": _h1(400, seed=2, px=150.0)}, dt.date(2026, 11, 6))
        self.assertTrue(lines[-1].startswith("VERDICT: "))
        self.assertTrue(any(l.startswith("STOP  half 2") for l in lines))

    def test_verdict_pass_needs_every_check(self):
        class T:
            def __init__(self, r, day): self.r, self.entry_time = r, day
        good = [T(1.0, "2024-01-01"), T(-0.5, "2024-01-02"), T(1.0, "2026-01-01"), T(-0.5, "2026-01-02")] * 10
        bad = [T(0.5, "2024-01-01"), T(-1.0, "2024-01-02"), T(0.5, "2026-01-01"), T(-1.0, "2026-01-02")] * 10
        base = {"lien-fader": good, "tharp-random": bad}
        _, _, _, checks = fe.verdict(base, {"lien-fader": good}, "2025-06-01")
        self.assertTrue(all(checks.values()))
        _, _, _, checks = fe.verdict(base, {"lien-fader": bad}, "2025-06-01")
        self.assertFalse(checks["STOP PF >= 1.0 at 2x spread"])


if __name__ == "__main__":
    unittest.main()
