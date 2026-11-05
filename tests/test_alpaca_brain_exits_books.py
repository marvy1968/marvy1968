"""brain_exits_study(only_books=...) runs just the named book (2026-11-05: the VM run skips the shorts-only book after it failed its own test)."""
import unittest

from alpacabot import gates
from alpacabot.engine import split_days
from alpacabot.experts import features_both
from alpacabot.research import BRAIN_EXIT_VARIANTS, brain_exits_study
from alpacabot.zonemap import attach
from tests.test_alpaca_replay import SESSIONS, daily_bars, minute_bars


class BrainExitsBooks(unittest.TestCase):
    def test_only_long_short(self):
        syms = ["SPY", "QQQ", "IWM", "SOFI"]
        minute = {s: minute_bars(40 + k, 20 + 5 * k) for k, s in enumerate(syms)}
        daily = {"SPY": daily_bars(-0.004, 1), "QQQ": daily_bars(-0.002, 2), "IWM": daily_bars(0.0, 3), "XLF": daily_bars(-0.004, 4)}
        gates.add_sector("SOFI", "XLF")
        days = attach(gates.add_gates(split_days(minute, minutes=5, feature_fn=features_both), daily), {**daily, "SOFI": daily_bars(-0.005, 7)})
        d = [x.date() for x in SESSIONS]
        res = brain_exits_study(days, (d[1], d[3]), (d[4], d[6]), symbols=["SOFI"], only_books=("long + short",))
        self.assertEqual(len(res["checks"]), len(BRAIN_EXIT_VARIANTS) - 1)
        self.assertTrue(all(k.startswith("long + short") for k in res["periods"]))


if __name__ == "__main__":
    unittest.main()
