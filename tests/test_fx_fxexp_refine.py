import unittest
from types import SimpleNamespace as T

from fxbot import fxexp_refine as fr


def tr(pair="EUR_USD", hour=9, r=1.0, risk=0.0020, day="2026-01-05"):
    return T(pair=pair, entry_time=f"{day}T{hour:02d}:00:00", r=r, risk=risk)


class FilterTests(unittest.TestCase):
    def test_cost_filter_skips_tight_stops(self):
        sp, sl = (lambda p: 0.00014), (lambda p: 0.00003)           # 1.7 pips round-trip cost
        self.assertTrue(fr.keep_cost(tr(risk=0.0020), sp, sl))      # 8.5% of a 20-pip stop
        self.assertFalse(fr.keep_cost(tr(risk=0.0012), sp, sl))     # 14% of a 12-pip stop
        self.assertFalse(fr.keep_cost(tr(risk=0.0), sp, sl))        # no stop distance: never kept

    def test_session_filter(self):
        self.assertTrue(fr.keep_session(tr(hour=7)))
        self.assertTrue(fr.keep_session(tr(hour=16)))
        self.assertFalse(fr.keep_session(tr(hour=17)))
        self.assertFalse(fr.keep_session(tr(hour=3)))

    def test_pair_filters(self):
        usdjpy = fr.filter_for("C1", None, None)
        self.assertTrue(usdjpy(tr(pair="USD_JPY")))
        self.assertFalse(usdjpy(tr(pair="EUR_USD")))
        drop = fr.filter_for("D1", None, None)
        self.assertFalse(drop(tr(pair="AUD_USD")))
        self.assertFalse(drop(tr(pair="NZD_USD")))
        self.assertTrue(drop(tr(pair="EUR_JPY")))

    def test_random_removal_is_seeded_and_below_the_best_possible(self):
        rs = [1.0] * 20 + [-1.0] * 40
        a, b = fr.random_removal_p95(rs, 30), fr.random_removal_p95(rs, 30)
        self.assertEqual(a, b)
        self.assertLess(a, 1.0)


class JudgeTests(unittest.TestCase):
    def test_a_filter_that_keeps_only_winners_passes(self):
        base = [tr(r=1.0, day="2025-03-01") for _ in range(20)] + [tr(r=-1.0, day="2025-03-01") for _ in range(20)] + \
               [tr(r=1.0, day="2026-03-01") for _ in range(20)] + [tr(r=-1.0, day="2026-03-01") for _ in range(20)]
        kept = [t for t in base if t.r > 0]
        j = fr.judge(base, kept, kept, "2026-01-01")
        self.assertTrue(j["pass"], j["checks"])

    def test_too_few_trades_fails(self):
        base = [tr(r=1.0 if i % 2 else -1.0, day="2025-03-01" if i < 20 else "2026-03-01") for i in range(40)]
        kept = base[:10]
        j = fr.judge(base, kept, kept, "2026-01-01")
        self.assertFalse(j["checks"]["30+ kept trades"])
        self.assertFalse(j["pass"])


if __name__ == "__main__":
    unittest.main()
