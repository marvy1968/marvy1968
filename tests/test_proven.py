import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from marv import bridge, proven


class ProvenGateTest(unittest.TestCase):
    def test_cfb_ml_has_no_percent(self):
        with mock.patch.dict(os.environ, {"PROVEN_GATE_SPORTS": "cfb"}):
            self.assertEqual(proven.pct("cfb", "ml", 0.95), proven.UNPROVEN)
            self.assertEqual(proven.pct("nfl", "ml", 0.81), "81%")  # not gated yet

    def test_proven_signal_prints_backtest_not_model(self):
        with mock.patch.dict(os.environ, {"PROVEN_GATE_SPORTS": "cfb"}):
            txt = proven.pct("cfb", "total", 0.9, signal="OVER-FADE+MOVE")
            self.assertIn("53.5%", txt)
            self.assertNotIn("90%", txt)
            self.assertEqual(proven.pct("cfb", "total", 0.6, signal="UNDER-FADE"), proven.UNPROVEN)

    def test_registry_meets_bar(self):
        for ev in proven.PROVEN.values():
            self.assertGreaterEqual(ev.n, 100)
            self.assertGreater(ev.roi, 0)
            self.assertLess(ev.hit, 0.6)  # no 90% claims

    def test_gate_off(self):
        with mock.patch.dict(os.environ, {"PROVEN_GATE_SPORTS": "none"}):
            self.assertFalse(proven.gated("cfb"))


class OverlayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        (self.state / "predictions.json").write_text(json.dumps({"cfb:1": {
            "sport": "cfb", "game_id": "1", "start": "2099-10-10T19:30:00+00:00", "home": "Ohio State", "away": "Maryland",
            "home_exp": 38, "away_exp": 16, "model_total": 54.0, "model_margin": 22.0, "home_win": 0.92,
            "qualified": [], "notes": ["O/U spots (tracked, not picks): OVER-FADE→Under [x]"]}}))
        (self.state / "marv_predict").mkdir()
        (self.state / "marv_predict" / "h2h_cfb.json").write_text(json.dumps({"games": [{
            "home": "Ohio State", "away": "Maryland", "margin": 22.2, "rating_total": 54.0, "cat_home": 2, "cat_away": 0,
            "stars_home": 3, "stars_away": 0, "fade": "UNDER"}]}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_ml_good_without_percent(self):
        o = bridge.overlay(self.state, "ncaaf", "h2h", "Ohio State Buckeyes", price=-1100)
        self.assertEqual(o.verdict, "good")
        self.assertEqual(o.prob, proven.UNPROVEN)
        self.assertIn("2-0", o.line())
        self.assertNotIn("92%", o.line())

    def test_ml_dog_not(self):
        o = bridge.overlay(self.state, "ncaaf", "h2h", "Maryland Terrapins", price=700)
        self.assertEqual(o.verdict, "not")

    def test_spread(self):
        o = bridge.overlay(self.state, "ncaaf", "spreads", "Ohio State Buckeyes", line=-32.5)
        self.assertEqual(o.verdict, "not")  # 3 H2H points for OSU but ratings say it doesn't cover... mixed or not
        o2 = bridge.overlay(self.state, "ncaaf", "spreads", "Ohio State Buckeyes", line=-10.5)
        self.assertEqual(o2.verdict, "good")

    def test_total_under_proven(self):
        o = bridge.overlay(self.state, "ncaaf", "totals", "Under", line=56.0,
                           text="🏈 Maryland Terrapins @ Ohio State Buckeyes · Under 56")
        self.assertEqual(o.verdict, "good")
        self.assertIn("53.5%", o.prob)
        live = bridge.overlay(self.state, "ncaaf", "totals", "Under", line=111.5, team="Ohio State", live=True)
        self.assertEqual(live.prob, proven.UNPROVEN)
        self.assertEqual(live.verdict, "n/a")

    def test_prop_no_read(self):
        o = bridge.overlay(self.state, "ncaaf", "player_reception_yds", "Over", line=60.5, team="Ohio State")
        self.assertEqual(o.verdict, "n/a")
        self.assertEqual(o.prob, proven.UNPROVEN)

    def test_card_only_game(self):
        (self.state / "marv_predict" / "h2h_cfb.json").write_text(json.dumps({"games": [{
            "home": "Missouri", "away": "Texas A&M", "margin": 5.8, "rating_total": 49.3, "cat_home": 2, "cat_away": 0,
            "stars_home": 3, "stars_away": 0, "fade": "OVER"}]}))
        o = bridge.overlay(self.state, "ncaaf", "h2h", "Texas A&M Aggies", text="Texas A&M Aggies @ Missouri Tigers ML +150")
        self.assertTrue(o.found)
        self.assertEqual(o.verdict, "not")
        o = bridge.overlay(self.state, "ncaaf", "totals", "Over", line=48.5, text="Texas A&M Aggies @ Missouri Tigers Over 48.5")
        self.assertEqual(o.verdict, "good")
        self.assertEqual(o.prob, proven.UNPROVEN)  # under-trend fade -> OVER is not proven

    def test_unknown_game_silent(self):
        o = bridge.overlay(self.state, "ncaaf", "h2h", "Rice Owls", text="Rice Owls @ Navy")
        self.assertFalse(o.found)
        self.assertEqual(o.line(), "")


if __name__ == "__main__":
    unittest.main()
