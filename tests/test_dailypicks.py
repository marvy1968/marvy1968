import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from marv import dailypicks as D


def leg(sport, game, market, side, price, tier="LEAN", strength=1.0, line=None, start=None):
    h, a = game.split(" @ ")[1], game.split(" @ ")[0]
    return {"sport": sport, "game": game, "home": h, "away": a, "market": market, "side": side, "line": line,
            "price": price, "tier": tier, "strength": strength, "why": "x",
            "start": start or datetime(2026, 10, 12, 18, tzinfo=timezone.utc).isoformat()}


class G:
    def __init__(self, home, away, hs, as_, start):
        self.home, self.away, self.home_score, self.away_score, self.start, self.completed = home, away, hs, as_, start, True


class DailyPicksTest(unittest.TestCase):
    def test_combined_price(self):
        self.assertEqual(D.combined([-110, -110]), 264)
        self.assertEqual(D.dec_to_american(2.38), 138)

    def test_parlays_ranked_distinct_games(self):
        legs = [leg("nfl", "A @ B", "ml", "B", -150, "HIGH", 3.5), leg("nfl", "A @ B", "total", "Under", -110, "SIGNAL", 2.6, 44.5),
                leg("nfl", "C @ D", "ml", "D", 120, "LEAN", 1.2), leg("nfl", "E @ F", "total", "Over", -110, "LEAN", 1.1, 40)]
        ps = D.parlays(legs, 10)
        self.assertTrue(all(p["legs"][0]["game"] != p["legs"][1]["game"] for p in ps))
        self.assertEqual([p["score"] for p in ps], sorted([p["score"] for p in ps], reverse=True))
        self.assertAlmostEqual(ps[0]["score"], 4.7)

    def test_card_text_has_no_model_percent(self):
        now = datetime(2026, 10, 12, 13, tzinfo=timezone.utc)
        legs = [leg("nhl", "A @ B", "ml", "B", 112, "HIGH", 3.2), leg("nhl", "C @ D", "total", "Under", -110, "LEAN", 1.1, 6.5)]
        card = D.build(legs, ["x"], ["nhl"], now)
        txt = D.text(card, test=True)
        self.assertIn("TEST", txt)
        self.assertIn("NHL", txt)
        self.assertNotIn("%", txt.replace("52.4%", "").replace("no model %", "").replace("print a %", ""))

    def test_log_and_grade_parlay(self):
        with tempfile.TemporaryDirectory() as d:
            st = datetime(2026, 10, 12, 18, tzinfo=timezone.utc)
            legs = [leg("nhl", "A @ B", "ml", "B", 112, "HIGH", 3.2), leg("nhl", "C @ D", "total", "Under", -110, "LEAN", 1.1, 6.5)]
            card = D.build(legs, [], ["nhl"], st - timedelta(hours=5))
            self.assertEqual(D.log_card(Path(d), card), 3)
            self.assertEqual(D.log_card(Path(d), card), 0)  # idempotent
            D.grade(Path(d), "nhl", [G("B", "A", 3, 2, st), G("D", "C", 2, 1, st)])
            book = json.loads((Path(d) / "marv_predict" / D.LOG).read_text())
            par = next(r for r in book if r["kind"] == "parlay2")
            self.assertEqual(par["result"], "win")
            self.assertGreater(par["profit"], 0)
            self.assertIn("2-leg parlays 1-0", D.record(Path(d)))


if __name__ == "__main__":
    unittest.main()
