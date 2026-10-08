import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from marv import betcard
from marv.models import Game, Odds, Pick, Prediction


def pred(gid, home, away, picks, notes=(), margin=3.0, total=47.0, spread=-3.0):
    g = Game(gid, "nfl", datetime.now(timezone.utc) + timedelta(hours=8), home, away,
             odds=Odds(spread=spread, total=total, home_ml=-150, away_ml=130))
    return Prediction(g, 25, 22, margin, total, 0.6, notes=list(notes), picks=picks)


class Proj:
    def __init__(self, tags):
        self.ou_tags = tags


class BetCardTests(unittest.TestCase):
    def setUp(self):
        self.high = pred("1", "Lions", "Bears", [Pick("ml", "Lions", -150, -150, 0.80, 0.18, 0.12)])
        self.model = pred("2", "Packers", "Vikings", [Pick("total", "Under", 44.5, -110, 0.57, 0.09, 0.05, ["O/U has no proven edge in backtests"])])
        self.blocked = pred("3", "Cowboys", "Buccaneers",
                            [Pick("spread", "Buccaneers", 9, -110, 0.6, 0.15, 0.08, ["backup QB: Tampa Bay Buccaneers Jalon Daniels has 1 start"])],
                            notes=["spots (tracked, not picks): MARV4→Buccaneers [x]"])
        self.tagged = pred("4", "Alabama", "Georgia", [], spread=-3.0, total=55.5)

    def test_ranking_tiers_and_hard_vetoes(self):
        c = betcard.candidates("nfl", [self.high, self.model, self.blocked, self.tagged],
                               {"4": Proj([("OVER-FADE+INFLATED", "Under")])})
        bets = betcard.select(c)
        self.assertEqual([b.tier for b in bets], ["HIGH", "SIGNAL", "MODEL"])
        self.assertEqual(bets[0].label, "Lions ML (-150)")
        self.assertEqual(bets[1].label, "UNDER 55.5 (-110)")
        self.assertFalse([b for b in bets if "Buccaneers" in b.game])  # backup QB blocks the whole game
        self.assertEqual(bets[2].units, 0.5)
        text = betcard.text(bets, datetime(2026, 10, 9))
        self.assertIn("HIGH CONFIDENCE", text)
        self.assertIn("Total risk", text)

    def test_max_ten_and_two_per_game_and_merge(self):
        many = [pred(str(i), f"H{i}", f"A{i}", [Pick("total", "Under", 44.5, -110, 0.57, 0.05 + i / 1000, 0.03, ["confidence 57% below 78%"]),
                                               Pick("spread", f"H{i}", -3, -110, 0.56, 0.04, 0.03, ["confidence"]),
                                               Pick("ml", f"H{i}", -150, -150, 0.62, 0.035, 0.02, ["confidence"])]) for i in range(8)]
        bets = betcard.select(betcard.candidates("nfl", many))
        self.assertEqual(len(bets), 10)
        per = {}
        for b in bets:
            per[b.game] = per.get(b.game, 0) + 1
        self.assertLessEqual(max(per.values()), 2)
        same = betcard.select([*betcard.candidates("nfl", [self.model]),
                               *betcard.candidates("nfl", [self.model], {"2": Proj([("OVER-FADE", "Under")])})])
        self.assertEqual(len(same), 1)  # the signal and the model edge on one bet merge
        self.assertEqual(same[0].tier, "SIGNAL")

    def test_log_and_grade(self):
        with tempfile.TemporaryDirectory() as d:
            bets = betcard.select(betcard.candidates("nfl", [self.high, self.model]))
            betcard.log(Path(d), bets, datetime.now(timezone.utc))
            g1 = Game("1", "nfl", self.high.game.start, "Lions", "Bears", completed=True, home_score=24, away_score=20)
            g2 = Game("2", "nfl", self.model.game.start, "Packers", "Vikings", completed=True, home_score=30, away_score=21)
            betcard.grade(Path(d), "nfl", [g1, g2])
            rec = betcard.record(Path(d))
            self.assertIn("HIGH: 1-0", rec)
            self.assertIn("MODEL: 0-1", rec)  # 51 > 44.5


if __name__ == "__main__":
    unittest.main()


class UntestedCapTests(unittest.TestCase):
    def test_at_most_two_untested_gaps(self):
        start = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        gaps = [{"sport": "nfl", "market": "total", "home": f"H{i}", "away": f"A{i}", "start": start, "side": "Over",
                 "line": 44.5, "price": -110, "pinnacle_line": 45} for i in range(6)]
        bets = betcard.select(betcard.gap_candidates(gaps))
        self.assertEqual(len(bets), 2)
