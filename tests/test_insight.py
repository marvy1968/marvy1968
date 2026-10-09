"""Marv Predict insight (Oct 9 2026): every metric compared for / against -> one good / bad / mixed read, no % unless proven."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from marv import bridge, insight, proven


class InsightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        st = self.state = Path(self.tmp.name)
        start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        (st / "predictions.json").write_text(json.dumps({"nfl:G1": {
            "sport": "nfl", "game_id": "G1", "start": start, "home": "Dallas Cowboys", "away": "Tampa Bay Buccaneers",
            "home_exp": 27, "away_exp": 20, "model_total": 47.0, "model_margin": 7.0, "home_win": 0.7, "qualified": [],
            "notes": ["roster: Dallas Cowboys no regular starters out; Tampa Bay Buccaneers 3 starters out (2.5 full-time)"
                      " + QB OUT ⚠️ elimination",
                      "line move: spread Dallas Cowboys -8.5 → -9.5 (toward Dallas Cowboys), total 48.5 → 49 (+0.5)"]}}))
        (st / "marv_predict").mkdir()
        (st / "marv_predict" / "h2h_nfl.json").write_text(json.dumps({"card_day": datetime.now(timezone.utc).date().isoformat(),
            "games": [{"game_id": "G1", "home": "Dallas Cowboys", "away": "Tampa Bay Buccaneers", "margin": 8.0,
                       "rating_total": 44.0, "cat_home": 2, "cat_away": 0, "stars_home": 3, "stars_away": 1, "fade": "UNDER",
                       "pick": "Dallas Cowboys", "method": "category sweep 2-0", "total_line": 49.0}]}))
        (st / "sharp_gap_log.json").write_text(json.dumps({"k": {
            "sport": "nfl", "start": start, "home": "Dallas Cowboys", "away": "Tampa Bay Buccaneers", "market": "spread",
            "side": "Tampa Bay Buccaneers", "line": 9.5, "pinnacle_line": 8.5, "result": None}}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_qb_out_line_move_and_gap_are_compared(self):
        o = bridge.overlay(self.state, "nfl", "spreads", "Dallas Cowboys", line=-9.5, price=-110)
        self.assertIn(o.verdict, ("good", "mixed", "not"))
        ins = insight.side_insight(self.state, "nfl", bridge._engine(json.loads(
            (self.state / "predictions.json").read_text())["nfl:G1"]), json.loads(
            (self.state / "marv_predict" / "h2h_nfl.json").read_text())["games"][0], "home", -9.5, spread=True)
        labels = " | ".join(f.label for f in ins.factors)
        self.assertIn("Buccaneers QB out", labels)
        self.assertIn("line moved toward Cowboys", labels)
        self.assertIn("Pinnacle", labels)
        self.assertEqual(o.prob, proven.UNPROVEN)

    def test_ml_favourite_good(self):
        o = bridge.overlay(self.state, "nfl", "h2h", "Dallas Cowboys", price=-400)
        self.assertEqual(o.verdict, "good")
        self.assertIn("👍 good", o.line())
        self.assertNotIn("70%", o.line())

    def test_total_under_good_but_nfl_unproven(self):
        o = bridge.overlay(self.state, "nfl", "totals", "Under", line=49.0, team="Dallas Cowboys")
        self.assertEqual(o.verdict, "good")  # ratings 44, model 47, fade UNDER, QB out (vs total moved up)
        self.assertEqual(o.prob, proven.UNPROVEN)  # OVER-FADE is proven for CFB only

    def test_game_level_one_line(self):
        o = bridge.game_h2h(self.state, "nfl", "Cowboys")
        line = o.line()
        self.assertTrue(line.startswith("🧠 Marv H2H: Dallas Cowboys 👍 good"))
        self.assertIn("O/U: Under 49", line)
        self.assertNotIn("%", line.replace("no %", ""))

    def test_same_qb_is_not_a_change(self):
        q = insight.qb_issues(["roster: Georgia: QB change? Shotgun #14 G.Stockton threw most last game, season leader "
                               "Huddle #14 G.Stockton (no college injury feed: check team news)"], "Alabama", "Georgia")
        self.assertEqual(q, {"home": [], "away": []})


if __name__ == "__main__":
    unittest.main()
