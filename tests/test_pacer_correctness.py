"""Correctness: remainder meta, one game clock, optional overfill guard.

Accounting helpers do not rewrite historical ledgers. The overfill guard is
off unless KALSHI_REFUSE_OVERFILL is set; it would not have caught MIA@SAC.
"""
import os
import sys
import json
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "review_src", "kalshi-demo"))
sys.path.insert(0, os.path.join(ROOT, "review_src", "polymarket-paper"))
sys.path.insert(0, os.path.join(ROOT, "review_src", "marv-feed", "live_props"))
sys.path.insert(0, os.path.join(ROOT, "review_src", "marv-feed", "reports", "prop_pacer"))
sys.path.insert(0, os.path.join(ROOT, "review_src"))

import game_clock as GC
import grader
import kalshi_demo as KD
import pm_nba_live as PM
import live_props as LP
import pacer_nba_live as PNL
import nba_pacer_shadow as SHADOW

FIX = os.path.join(ROOT, "review_src", "fixtures", "grader_meta_cases.json")
PARENT = "01a1c742-c420-773c-b081-772ea0e3fc51"
CHILD = "01a1c742-c420-7fd2-9f64-1fde2a73cc81"
ROLLINS = "01a19f49-65f0-72da-a717-c904484c7c78"


def _orders_passing_meta(path):
    with open(path) as fh:
        return [line.strip() for line in fh if ".place_order(" in line]


class GameClockTests(unittest.TestCase):
    def test_shared_parser_cases(self):
        self.assertIsNone(GC.parse_period_clock("38.0", 12))
        self.assertIsNone(GC.parse_period_clock("0.0", 12))
        self.assertIsNone(GC.parse_period_clock(None, 12))
        self.assertIsNone(GC.parse_period_clock(38.0, 12))
        self.assertIsNone(GC.parse_period_clock("nope", 12))
        self.assertIsNone(GC.parse_period_clock("2:61", 12))
        self.assertIsNone(GC.parse_period_clock("", 12))
        self.assertAlmostEqual(GC.parse_period_clock("2:31", 12), 2 + 31 / 60.0)
        self.assertEqual(GC.parse_period_clock("0:00", 12), 0.0)
        # Not the old minutes reading (38) and not the old seconds reading (38/60).
        self.assertNotEqual(GC.parse_period_clock("38.0", 48), 38.0)
        self.assertNotEqual(GC.parse_period_clock("38.0", 48), 38.0 / 60.0)

    def test_period_plus_clock_and_callers_agree(self):
        q4 = GC.game_minutes_left(4, "2:31", period_minutes=12, n_periods=4, ot_minutes=5)
        self.assertAlmostEqual(q4, 2 + 31 / 60.0)
        q3 = GC.game_minutes_left(3, "2:31", period_minutes=12, n_periods=4, ot_minutes=5)
        self.assertAlmostEqual(q3, 12 + 2 + 31 / 60.0)
        self.assertIsNone(GC.game_minutes_left(4, "38.0", period_minutes=12, n_periods=4, ot_minutes=5))
        self.assertIsNone(GC.game_minutes_left(4, "0.0", period_minutes=12, n_periods=4, ot_minutes=5))
        self.assertIsNone(GC.game_minutes_left(4, None, period_minutes=12, n_periods=4, ot_minutes=5))
        self.assertIsNone(GC.game_minutes_left(None, "2:31", period_minutes=12, n_periods=4, ot_minutes=5))
        self.assertAlmostEqual(PM.remaining(4, "2:31"), q4)
        self.assertAlmostEqual(LP.remaining(4, "2:31", sport="nba"), q4)
        self.assertAlmostEqual(PNL.clk("2:31"), 2 + 31 / 60.0)
        self.assertAlmostEqual(SHADOW.clk("2:31"), PNL.clk("2:31"))
        self.assertIsNone(PM.remaining(4, "38.0"))
        self.assertIsNone(PM.remaining(4, "0.0"))
        self.assertIsNone(PM.remaining(4, None))
        self.assertIsNone(PM.remaining(2, "bad"))
        self.assertIsNone(LP.remaining(4, "38.0", sport="nba"))
        self.assertIsNone(LP.remaining(1, "0.0", sport="nfl"))
        self.assertIsNone(LP.remaining(2, "nope", sport="nhl"))
        for bad in ("38.0", "0.0", None, "bad", "2:61"):
            with self.assertRaises(ValueError):
                PNL.clk(bad)
            with self.assertRaises(ValueError):
                SHADOW.clk(bad)
        self.assertEqual(PNL.clk("0:00"), 0.0)
        self.assertIsNone(PM.proj({"cur": {}}, {"period": 4, "clock": "38.0", "half": ""}, {}))
        self.assertIsNone(PM.proj({"cur": {}}, {"period": 4, "clock": None, "half": ""}, {}))
        priced = PM.proj(
            {"cur": {"MIN": 20, "PF": 1}, "starter": True},
            {"period": 4, "clock": "2:31", "half": "4th", "margin": 4},
            {"min": 32},
        )
        self.assertIsNotNone(priced)
        self.assertAlmostEqual(LP.remaining(4, "14:56", sport="nfl"), 14 + 56 / 60.0)
        self.assertIsNone(LP.remaining(4, "38.0", sport="nfl"))
        self.assertAlmostEqual(LP.remaining(2, "67'", sport="epl"), 23.0)
        self.assertIsNone(LP.remaining(2, "38.0", sport="epl"))


class RemainderMetaTests(unittest.TestCase):
    def setUp(self):
        with open(FIX) as fh:
            self.fx = json.load(fh)

    def test_nov10_child_inherits_parent_meta(self):
        nov = self.fx["nov10"]
        self.assertEqual(nov["parent_order_id"], PARENT)
        self.assertEqual(nov["child_order_id"], CHILD)
        self.assertIsNone(nov["child_ledger_row"]["me_type"])
        self.assertIsNone(nov["child_ledger_row"]["key"])
        rows = grader.join_meta(
            [nov["parent_ledger_row"], nov["child_ledger_row"]],
            [nov["parent_demo_log"]],
            nov["resting_orders"],
            [],
        )
        child = next(r for r in rows if r["order_id"] == CHILD)
        self.assertEqual(child["meta_src"], "parent")
        self.assertEqual(child["key"], "master:total:nba:401909963:over")
        self.assertEqual(child["pick"], "Over 228.5")
        self.assertEqual(child["lane"], "marv_nba")
        self.assertEqual(child["bet_type"], "master_total:nba")
        self.assertEqual(child["me_type"], "master_total:nba")
        self.assertEqual(child["date"], "2026-11-10")
        self.assertEqual(child["side"], "yes")
        self.assertNotEqual(child["me_type"], "untagged")
        # Same inheritance from the parent log's embedded rest id, with no resting file.
        via_log = grader.join_meta([nov["child_ledger_row"]], [nov["parent_demo_log"]], [], [])
        self.assertEqual(via_log[0]["me_type"], "master_total:nba")
        self.assertEqual(via_log[0]["meta_src"], "parent")
        # No link and no calib row stays untagged. Nothing is written to disk.
        bare = grader.join_meta([nov["child_ledger_row"]], [], [], [])
        self.assertEqual(bare[0]["meta_src"], "missing")
        self.assertIsNone(bare[0]["me_type"])

    def test_rollins_recovered_from_same_second_calib_without_demo_log(self):
        rol = self.fx["rollins"]
        self.assertEqual(rol["order_id"], ROLLINS)
        self.assertEqual(rol["demo_log_rows"], [])
        self.assertEqual(grader.load_jsonl(os.path.join(ROOT, "review_src", "no-such-demo-log.jsonl")), [])
        rows = grader.join_meta([rol["ledger_row"]], [], [], [rol["calib"]])
        row = rows[0]
        self.assertEqual(row["order_id"], ROLLINS)
        self.assertEqual(row["meta_src"], "calib_decisions")
        self.assertEqual(row["me_type"], "nba_prop_live_pacer")
        self.assertEqual(row["bet_type"], "nba_prop_live_pacer")
        self.assertIsNone(row["key"])
        self.assertIsNone(row["pick"])
        late = dict(rol["calib"], ts="2026-11-02T20:42:31.006186-06:00")
        missed = grader.join_meta([rol["ledger_row"]], [], [], [late])
        self.assertEqual(missed[0]["meta_src"], "missing")
        self.assertIsNone(missed[0]["me_type"])
        other = dict(rol["calib"], ticker="OTHER")
        missed = grader.join_meta([rol["ledger_row"]], [], [], [other])
        self.assertIsNone(missed[0]["me_type"])

    def test_marv_callers_pass_meta(self):
        kd = os.path.join(ROOT, "review_src", "kalshi-demo")
        for name in ("runner.py", "alt70_kalshi.py", "marv_cbb_kalshi.py"):
            calls = _orders_passing_meta(os.path.join(kd, name))
            self.assertTrue(calls, name)
            for call in calls:
                self.assertIn("meta=", call, name + " " + call)


class OverfillGuardTests(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("KALSHI_REFUSE_OVERFILL")
        os.environ.pop("KALSHI_REFUSE_OVERFILL", None)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("KALSHI_REFUSE_OVERFILL", None)
        else:
            os.environ["KALSHI_REFUSE_OVERFILL"] = self._prev

    def _client(self, fill):
        class Fake:
            def _place_ioc(self, ticker, side, count, limit_cents):
                return {"order_id": "parent-ioc", "fill_count": fill}
            def _req(self, *args, **kwargs):
                raise AssertionError("remainder rest should not run for these cases")
        return Fake()

    def test_guard_off_by_default_and_refuses_only_when_opted_in(self):
        self.assertFalse(KD.refuse_overfill_on(os.path.join(ROOT, "review_src", "kalshi-demo", "data")))
        client = self._client("12")
        out = KD.KalshiDemo.place_order(client, "TICK", "yes", 10, 40)
        self.assertEqual(out["filled_ioc"], 12.0)
        self.assertFalse(out.get("overfill_refused"))
        os.environ["KALSHI_REFUSE_OVERFILL"] = "on"
        client = self._client("12")
        with self.assertRaises(KD.OverfillRefused) as caught:
            KD.KalshiDemo.place_order(client, "TICK", "yes", 10, 40)
        self.assertTrue(caught.exception.result["overfill_flag"])
        self.assertTrue(caught.exception.result["overfill_refused"])
        self.assertEqual(caught.exception.intended, 10)
        self.assertEqual(caught.exception.filled, 12.0)
        exact = KD.KalshiDemo.place_order(self._client("10"), "TICK", "yes", 10, 40)
        self.assertEqual(exact["filled_ioc"], 10.0)
        self.assertFalse(exact.get("overfill_refused"))


if __name__ == "__main__":
    unittest.main()
