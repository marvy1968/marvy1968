"""Owner 2026-11-05 two-week shorts-only Brain test (research.brain_shorts_study): runs end to end on synthetic bars and applies its rule."""
import tempfile
import unittest
from pathlib import Path

from alpacabot import gates
from alpacabot.engine import split_days
from alpacabot.experts import features_both
from alpacabot.zonemap import attach
from alpacabot.research import BRAIN_SHORT_RULE, brain_shorts_study
from tests.test_alpaca_replay import SESSIONS, daily_bars, minute_bars


class BrainShortsTest(unittest.TestCase):
    def test_runs_and_rules(self):
        syms = ["SPY", "QQQ", "IWM", "SOFI", "RKLB"]
        minute = {s: minute_bars(20 + k, 20 + 5 * k) for k, s in enumerate(syms)}
        daily = {"SPY": daily_bars(-0.004, 1), "QQQ": daily_bars(-0.002, 2), "IWM": daily_bars(0.0, 3), "XLF": daily_bars(-0.004, 4),
                 "XLI": daily_bars(-0.004, 5)}
        gates.add_sector("SOFI", "XLF")
        gates.add_sector("RKLB", "XLI")
        days = gates.add_gates(split_days(minute, minutes=5, feature_fn=features_both), daily)
        days = attach(days, {**daily, "SOFI": daily_bars(-0.005, 7), "RKLB": daily_bars(-0.005, 8)})
        d = [x.date() for x in SESSIONS]
        with tempfile.TemporaryDirectory() as td:
            res = brain_shorts_study(days, (d[1], d[3]), (d[4], d[6]), ckpt=Path(td) / "ck.json", symbols=["SOFI", "RKLB"], placebo_runs=1)
        self.assertEqual(res["symbols"], ["RKLB", "SOFI"])
        self.assertIn("SHORTS ONLY (live short experts)", res["periods"]["first week"])
        self.assertIn("SHORTS ONLY at 2x costs", res["periods"]["both weeks"])
        self.assertEqual(set(res["checks"]), {"10+ trades", "PF >= 1.1 week 1", "PF >= 1.1 week 2", "PF >= 1.0 at 2x costs",
                                              "avg R beats random timing"})
        self.assertEqual(res["passes"], all(res["checks"].values()))
        self.assertTrue(res["verdict"].startswith("PASS" if res["passes"] else "FAIL"))
        self.assertIn("10+ trades", BRAIN_SHORT_RULE)
        self.assertGreater(res["periods"]["both weeks"]["SHORTS ONLY"]["trades"], 0)     # the full live filter stack lets shorts through


if __name__ == "__main__":
    unittest.main()
