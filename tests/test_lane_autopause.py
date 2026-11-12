"""Opt-in lane autopause. Default allowlist is empty. nba_prop_live_pacer is the
intended candidate; pm:nba_prop_live is excluded. force_track is not modified."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KD = os.path.join(ROOT, "review_src", "kalshi-demo")
sys.path.insert(0, KD)

import calib_size as CS
import kalshi_nba_pacer as PACER
import type_gate as TG

FIX = os.path.join(ROOT, "review_src", "fixtures", "pacer_autopause_sequence.json")
TYPE_GATE_SHA = "c40b34ceabf3938cc77d385bc249efc03f4887107c2ee186a4d8059ac11d45a8"


class LaneAutopauseTests(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("LANE_AUTOPAUSE_ALLOW", "LANE_AUTOPAUSE_CLEAR", "KALSHI_FORCE_TRACK")}
        os.environ.pop("LANE_AUTOPAUSE_ALLOW", None)
        os.environ.pop("LANE_AUTOPAUSE_CLEAR", None)
        os.environ.pop("KALSHI_FORCE_TRACK", None)
        with open(FIX) as fh:
            self.seq = json.load(fh)
        self.fills = self.seq["fills"]

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_allowlist_empty_and_pm_nba_live_excluded(self):
        self.assertEqual(CS.autopause_allowlist(), set())
        self.assertFalse(CS.autopause_allowlisted("kalshi", "nba_prop_live_pacer"))
        self.assertEqual(CS.AUTOPAUSE_INTENDED, ("kalshi", "nba_prop_live_pacer"))
        self.assertIn(("pm", "nba_prop_live"), CS.AUTOPAUSE_EXCLUDED)
        os.environ["LANE_AUTOPAUSE_ALLOW"] = "pm:nba_prop_live,polymarket_paper:nba_prop_live"
        self.assertFalse(CS.autopause_allowlisted("pm", "nba_prop_live"))
        self.assertFalse(CS.autopause_allowlisted("polymarket", "nba_prop_live"))
        self.assertEqual(CS.autopause_allowlist(), set())
        os.environ["LANE_AUTOPAUSE_ALLOW"] = "kalshi:nba_prop_live_pacer,pm:nba_prop_live"
        self.assertEqual(CS.autopause_allowlist(), {("kalshi", "nba_prop_live_pacer")})
        self.assertTrue(CS.autopause_allowlisted("kalshi_demo", "nba_prop_live_pacer"))
        self.assertFalse(CS.autopause_allowlisted("pm", "nba_prop_live"))

    def test_same_rule_as_calib_size_and_sizing_shrink_kept(self):
        import inspect
        src = inspect.getsource(CS.size)
        self.assertIn('p * s["act"] / s["pred"]', src)
        self.assertFalse(CS.overconfident(29, 0.70, 0.10))
        self.assertFalse(CS.overconfident(30, 0.70, 0.70 - 0.03))  # act == pred-0.03 stays calibrated
        self.assertTrue(CS.overconfident(30, 0.70, 0.70 - 0.03 - 1e-9))
        self.assertTrue(CS.overconfident(47, 0.643557, 0.574468))

    def test_fixture_trips_on_prior_fills_only(self):
        blocked = CS.replay_autopause(self.fills, allowlisted=False)
        self.assertIsNone(blocked["first"])
        self.assertTrue(all(not d["paused"] for d in blocked["decisions"]))

        rep = CS.replay_autopause(self.fills, allowlisted=True)
        first = rep["first"]
        self.assertIsNotNone(first)
        self.assertTrue(first["filled_CT"].startswith("2026-11-02T18:12:50"))
        self.assertEqual(first["n"], 47)
        self.assertAlmostEqual(first["pred"], 0.643557, places=6)
        self.assertAlmostEqual(first["act"], 0.574468, places=6)
        self.assertAlmostEqual(round(first["pred"], 3), 0.644, places=3)
        self.assertAlmostEqual(round(first["act"], 3), 0.574, places=3)
        self.assertEqual(self.seq["expected_first_fire"]["n_prior_settled"], 47)

        prior = CS.prior_settled(self.fills, first["filled_CT"])
        self.assertEqual(len(prior), 47)
        self.assertTrue(all(p["settled_CT"] < first["filled_CT"] for p in prior))
        self.assertFalse(any(p["filled_CT"] == first["filled_CT"] for p in prior))
        st = CS.stats_from_fills(prior)
        self.assertEqual(st["n"], 47)
        self.assertEqual(st["wins"], 27)
        self.assertAlmostEqual(st["act"], 27 / 47, places=6)

        paused_at = [d for d in rep["decisions"] if d["paused"]]
        self.assertEqual(paused_at[0]["filled_CT"], first["filled_CT"])
        idx = next(i for i, d in enumerate(rep["decisions"]) if d["tripped_here"])
        self.assertGreater(idx, 0)
        self.assertFalse(rep["decisions"][idx - 1]["paused"])
        self.assertTrue(all(d["paused"] for d in rep["decisions"][idx:]))

    def test_no_lookahead_when_later_outcomes_change(self):
        base = CS.replay_autopause(self.fills, allowlisted=True)["first"]
        flipped = deepcopy(self.fills)
        # The trip row itself and every fill that settles at or after it must not count.
        for row in flipped:
            if row["settled_CT"] >= base["filled_CT"] or row["filled_CT"] >= base["filled_CT"]:
                row["won"] = not row["won"]
        again = CS.replay_autopause(flipped, allowlisted=True)["first"]
        self.assertEqual(again["filled_CT"], base["filled_CT"])
        self.assertEqual(again["n"], base["n"])
        self.assertAlmostEqual(again["pred"], base["pred"], places=9)
        self.assertAlmostEqual(again["act"], base["act"], places=9)

        leaked = deepcopy(self.fills)
        prior = CS.prior_settled(leaked, base["filled_CT"])
        prior[0]["won"] = not prior[0]["won"]
        moved = CS.replay_autopause(leaked, allowlisted=True)["first"]
        self.assertNotAlmostEqual(moved["act"], base["act"], places=6)

    def test_latch_until_operator_clears_then_rearm(self):
        os.environ["LANE_AUTOPAUSE_ALLOW"] = "kalshi:nba_prop_live_pacer"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lane_autopause.json")
            bad = {"n": 47, "pred": 0.643557, "act": 0.574468}
            healthy = {"n": 40, "pred": 0.50, "act": 0.55}
            self.assertFalse(os.path.exists(path))
            os.environ.pop("LANE_AUTOPAUSE_ALLOW", None)
            cold = CS.autopause_status("kalshi", "nba_prop_live_pacer", stats=bad, state_path=path)
            self.assertFalse(cold["paused"])
            self.assertFalse(os.path.exists(path))
            os.environ["LANE_AUTOPAUSE_ALLOW"] = "kalshi:nba_prop_live_pacer"
            tripped = CS.autopause_status("kalshi", "nba_prop_live_pacer", stats=bad, state_path=path)
            self.assertTrue(tripped["paused"])
            self.assertTrue(tripped["tripped"])
            sticky = CS.autopause_status("kalshi", "nba_prop_live_pacer", stats=healthy, state_path=path)
            self.assertTrue(sticky["paused"])
            self.assertTrue(sticky["sticky"])
            CS.clear_autopause("kalshi", "nba_prop_live_pacer", path)
            lifted = CS.autopause_status("kalshi", "nba_prop_live_pacer", stats=bad, state_path=path)
            self.assertFalse(lifted["paused"])
            self.assertTrue(lifted["cleared"])
            CS.rearm_autopause("kalshi", "nba_prop_live_pacer", path)
            again = CS.autopause_status("kalshi", "nba_prop_live_pacer", stats=bad, state_path=path)
            self.assertTrue(again["paused"])

    def test_force_track_unchanged_and_wins_over_autopause(self):
        with open(os.path.join(KD, "type_gate.json"), "rb") as fh:
            raw = fh.read()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), TYPE_GATE_SHA)
        cfg = json.loads(raw)
        self.assertIn("nba_prop_live_pacer", cfg["force_track"])
        self.assertTrue(TG.force_track("nba_prop_live_pacer", live=True))
        self.assertFalse(TG.force_track("nba_prop_live", live=True))
        both = PACER.compose_order_reason(
            kill=None, stake=10, calib_skip=None, placed=0, max_orders=5, demo_auto=True, has_key=True,
            ask=40, edge=0.20, fav_on=False, min10_on=False, force_tracked=True, autopause_paused=True)
        self.assertEqual(both["reason"], "track-only: nba_prop_live_pacer (force_track)")
        self.assertTrue(both["ftrack"])
        self.assertFalse(both["ap_track"])
        only_ap = PACER.compose_order_reason(
            kill=None, stake=10, calib_skip=None, placed=0, max_orders=5, demo_auto=True, has_key=True,
            ask=40, edge=0.20, fav_on=False, min10_on=False, force_tracked=False, autopause_paused=True)
        self.assertEqual(only_ap["reason"], "track-only: nba_prop_live_pacer (lane autopause)")
        self.assertTrue(only_ap["ap_track"])
        self.assertFalse(only_ap["ftrack"])
        neither = PACER.compose_order_reason(
            kill=None, stake=10, calib_skip=None, placed=0, max_orders=5, demo_auto=True, has_key=True,
            ask=40, edge=0.20, fav_on=False, min10_on=False, force_tracked=False, autopause_paused=False)
        self.assertIsNone(neither["reason"])
        fav = PACER.compose_order_reason(
            kill=None, stake=10, calib_skip=None, placed=0, max_orders=5, demo_auto=True, has_key=True,
            ask=60, edge=0.20, fav_on=True, min10_on=False, force_tracked=False, autopause_paused=False)
        self.assertEqual(fav["reason"], "track-only: nba_prop_live_pacer_fav")


if __name__ == "__main__":
    unittest.main()
