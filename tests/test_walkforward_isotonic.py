"""Opt-in walk-forward isotonic for Kalshi nba_prop_live_pacer only.

Default off. Polymarket nba_prop_live stays off even if the flag is set.
Skips do not reprice or resize. No per-game cap.
"""
import csv
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KD = os.path.join(ROOT, "review_src", "kalshi-demo")
sys.path.insert(0, KD)

import walkforward_isotonic as ISO
import kalshi_nba_pacer as PACER


def _losses(n=30, p=0.90):
    return [{"p": p, "won": False} for _ in range(n)]


class IsotonicTests(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("KALSHI_PACER_ISOTONIC")
        os.environ.pop("KALSHI_PACER_ISOTONIC", None)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("KALSHI_PACER_ISOTONIC", None)
        else:
            os.environ["KALSHI_PACER_ISOTONIC"] = self._prev

    def test_off_by_default_and_pm_nba_live_excluded(self):
        self.assertFalse(ISO.enabled("kalshi", "nba_prop_live_pacer"))
        self.assertIsNone(ISO.maybe_skip(0.9, 0.4, _losses(), venue="kalshi", lane="nba_prop_live_pacer"))
        self.assertIsNone(PACER.isotonic_skip(0.9, 40, prior=_losses()))
        os.environ["KALSHI_PACER_ISOTONIC"] = "on"
        self.assertTrue(ISO.enabled("kalshi", "nba_prop_live_pacer"))
        self.assertTrue(ISO.enabled("kalshi_demo", "nba_prop_live_pacer"))
        self.assertFalse(ISO.enabled("pm", "nba_prop_live"))
        self.assertFalse(ISO.enabled("polymarket_paper", "nba_prop_live"))
        self.assertFalse(ISO.enabled("kalshi", "nba_prop_live"))
        self.assertIsNone(ISO.maybe_skip(0.9, 0.4, _losses(), venue="pm", lane="nba_prop_live"))
        self.assertIn(("pm", "nba_prop_live"), ISO.EXCLUDED)
        with open(os.path.join(KD, "walkforward_isotonic.py")) as fh:
            self.assertNotIn("per_game", fh.read())
        self.assertFalse(hasattr(ISO, "GAME_CAP"))

    def test_pav_is_monotone_and_passthrough_under_30(self):
        centers = ISO.fit_isotonic([0.2, 0.2, 0.8], [0.0, 1.0, 1.0])
        xs = [i / 20 for i in range(21)]
        ys = [ISO.predict_isotonic(centers, x) for x in xs]
        self.assertTrue(all(ys[i] <= ys[i + 1] + 1e-12 for i in range(len(ys) - 1)))
        self.assertTrue(all(0.0 <= y <= 1.0 for y in ys))
        self.assertAlmostEqual(ISO.predict_isotonic(ISO.fit_isotonic([0.2, 0.8], [0.0, 1.0]), 0.5), 0.5)
        p_cal, how = ISO.calibrate(0.77, _losses(10))
        self.assertEqual(how, "passthrough")
        self.assertAlmostEqual(p_cal, 0.77)

    def test_skip_only_no_reprice_and_no_lookahead(self):
        os.environ["KALSHI_PACER_ISOTONIC"] = "on"
        decision = ISO.decide(0.90, 0.40, _losses())
        self.assertEqual(decision["action"], "skip")
        self.assertEqual(decision["reason"], "isotonic gate")
        self.assertAlmostEqual(decision["price"], 0.40)
        self.assertFalse(decision["resized"])
        self.assertLess(decision["p_cal"], 0.20)
        self.assertEqual(ISO.maybe_skip(0.90, 0.40, _losses()), "isotonic gate")
        self.assertEqual(PACER.isotonic_skip(0.90, 40, prior=_losses()), "isotonic gate")

        wins = [{"p": 0.70, "won": True} for _ in range(30)]
        kept = ISO.decide(0.70, 0.40, wins)
        self.assertEqual(kept["action"], "keep")
        self.assertAlmostEqual(kept["price"], 0.40)
        self.assertFalse(kept["resized"])
        self.assertNotIn("stake", kept)

        fills = []
        for i in range(45):
            fills.append({"filled": f"{i:04d}", "settled": f"{i + 2:04d}", "p": 0.80,
                          "won": i % 3 != 0, "price": 0.40, "pnl": 1.5 if i % 3 != 0 else -1.0})
        base = ISO.walk_forward(fills)
        for row, dec in zip(sorted(fills, key=lambda r: r["filled"]), base):
            self.assertAlmostEqual(dec["price"], row["price"])
            self.assertFalse(dec["resized"])
            if dec["action"] == "keep":
                self.assertEqual(dec["pnl"], row["pnl"])
            else:
                self.assertIsNone(dec["pnl"])
        flipped = [dict(r) for r in fills]
        flipped[-1]["won"] = not flipped[-1]["won"]
        again = ISO.walk_forward(flipped)
        self.assertEqual([(d["action"], round(d["p_cal"], 6)) for d in base[:-1]],
                         [(d["action"], round(d["p_cal"], 6)) for d in again[:-1]])
        leaked = [dict(r) for r in fills]
        leaked[0]["won"] = not leaked[0]["won"]
        moved = ISO.walk_forward(leaked)
        self.assertNotEqual([round(d["p_cal"], 6) for d in moved], [round(d["p_cal"], 6) for d in base])

    def test_loader_uses_lane_rows_and_model_p(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = os.path.join(d, "results_ledger.csv")
            log = os.path.join(d, "demo_log.jsonl")
            with open(ledger, "w", newline="") as fh:
                w = csv.DictWriter(fh, ["order_id", "me_type", "bet_type", "status", "result", "filled_ts", "settled_ts"])
                w.writeheader()
                w.writerow({"order_id": "a", "me_type": "nba_prop_live_pacer", "bet_type": "nba_prop_live_pacer",
                            "status": "settled", "result": "win", "filled_ts": "t1", "settled_ts": "t2"})
                w.writerow({"order_id": "b", "me_type": "value_live", "bet_type": "",
                            "status": "settled", "result": "loss", "filled_ts": "t1", "settled_ts": "t2"})
                w.writerow({"order_id": "c", "me_type": "nba_prop_live_pacer", "bet_type": "nba_prop_live_pacer",
                            "status": "open", "result": "", "filled_ts": "t3", "settled_ts": ""})
            with open(log, "w") as fh:
                fh.write(json.dumps({"order": {"order_id": "a"}, "p_side": 0.61}) + "\n")
                fh.write(json.dumps({"order": {"order_id": "c"}, "fair_c": 65}) + "\n")
            rows = ISO.load_prior_kalshi(ledger, log)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["order_id"], "a")
        self.assertTrue(rows[0]["won"])
        self.assertAlmostEqual(rows[0]["p"], 0.61)
        self.assertEqual(ISO.load_prior_kalshi(os.path.join(d, "missing.csv"), log), [])

    def test_force_track_text_remains(self):
        with open(os.path.join(KD, "kalshi_nba_pacer.py")) as fh:
            src = fh.read()
        self.assertIn('force_track("nba_prop_live_pacer", live=True)', src)
        self.assertIn("track-only: nba_prop_live_pacer (force_track)", src)


if __name__ == "__main__":
    unittest.main()
