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


class CLVTests(unittest.TestCase):
    def test_clv_points_and_price(self):
        self.assertEqual(betcard.clv({"market": "total", "side": "Under", "line": 48.5, "price": -110,
                                      "close_line": 47.5, "close_price": -110})[0], 1.0)
        self.assertEqual(betcard.clv({"market": "spread", "side": "Browns", "line": 2.5, "price": -110,
                                      "close_line": 1.5, "close_price": -110})[0], 1.0)
        pts, price = betcard.clv({"market": "ml", "side": "Lions", "line": None, "price": -150, "close_price": -170})
        self.assertIsNone(pts)
        self.assertGreater(price, 0)  # bet at -150, closed -170: beat the close

    def test_update_close_and_report(self):
        from unittest import mock
        from marv.config import Settings
        with tempfile.TemporaryDirectory() as d:
            start = datetime.now(timezone.utc) + timedelta(hours=5)
            g = pred("9", "Dallas Cowboys", "Tampa Bay Buccaneers", [Pick("total", "Under", 48.5, -110, 0.58, 0.1, 0.06)])
            g.game.start = start
            bets = betcard.select(betcard.candidates("nfl", [g]))
            betcard.log(Path(d), bets, datetime.now(timezone.utc))
            ev = {"id": "e", "home_team": "Dallas Cowboys", "away_team": "Tampa Bay Buccaneers",
                  "commence_time": start.isoformat(), "bookmakers": [{"key": "bovada", "title": "Bovada", "markets": [
                      {"key": "totals", "outcomes": [{"name": "Over", "point": 47.5, "price": -110},
                                                     {"name": "Under", "point": 47.5, "price": -110}]}]}]}
            with mock.patch("marv.data.oddsapi.fetch", lambda key, sport: [ev]):
                n = betcard.update_close(Path(d), Settings(state_dir=d, odds_api_key="x"))
            self.assertEqual(n, 1)
            self.assertIn("beat the close 1", betcard.clv_report(Path(d)))


class TagOnlyTests(unittest.TestCase):
    def test_trend_bets_for_games_marv_does_not_project(self):
        g = Game("77", "cfb", datetime.now(timezone.utc) + timedelta(hours=6), "Ball State", "Bowling Green",
                 odds=Odds(total=58.5, under_price=-112, over_price=-108))
        none = Game("78", "cfb", datetime.now(timezone.utc) + timedelta(hours=6), "No Total U", "Other U")
        bets = betcard.tag_candidates("cfb", [g, none], {"77": [("OVER-FADE", "Under"), ("OVER-FADE+INFLATED", "Under")],
                                                         "78": [("OVER-FADE", "Under")]})
        self.assertEqual(len(bets), 2)  # the game without a total is skipped
        chosen = betcard.select(bets)
        self.assertEqual(len(chosen), 1)  # both tags are one bet
        self.assertEqual((chosen[0].label, chosen[0].tier), ("UNDER 58.5 (-112)", "SIGNAL"))
        self.assertIn("OVER-FADE", chosen[0].why)


class BetCardOncePerDayTests(unittest.TestCase):
    def test_one_card_per_day(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            day = datetime(2026, 10, 9, 10, 7)
            self.assertIsNone(betcard.already_sent(state, day))
            betcard.mark_sent(state, [], day)
            self.assertIsNotNone(betcard.already_sent(state, datetime(2026, 10, 9, 11, 21)))
            self.assertIsNone(betcard.already_sent(state, datetime(2026, 10, 10, 10, 0)))
