import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from marv import betcard
from marv.edges import payout
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


def _row(market, side, line, price, units=1.0, home="Lions", away="Bears"):
    """A logged bet-card row, as grade() reads it from state/betcard_log.json."""
    start = datetime(2026, 11, 17, 18, tzinfo=timezone.utc)
    return {"sport": "nfl", "game": f"{away} @ {home}", "start": start.isoformat(),
            "home": home, "away": away, "market": market, "side": side, "line": line, "price": price,
            "tier": "HIGH", "score": 0.1, "units": units, "why": "test", "result": None}


class MlPriceGuardTests(unittest.TestCase):
    """American moneylines cannot fall strictly between -100 and +100. A spread such as -19
    copied into the ML price field is rejected up front and voided if it is already logged.
    Spread lines and total numbers are not prices, so they stay on the card."""

    def test_spread_in_ml_price_is_rejected_and_graded_void(self):
        bad = pred("ml19", "Lions", "Bears", [Pick("ml", "Lions", -19, -19, 0.80, 0.18, 0.12)])
        with self.assertLogs("marv.betcard", level="WARNING") as cm:
            self.assertEqual(betcard.candidates("nfl", [bad]), [])
        self.assertTrue(any("ML price -19" in line for line in cm.output))

        start = datetime(2026, 11, 17, 18, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            (state / "betcard_log.json").write_text(json.dumps([_row("ml", "Lions", -19, -19)]))
            # Home covers easily. At a real price this would pay; -19 must not.
            g = Game("ml19", "nfl", start, "Lions", "Bears", completed=True, home_score=31, away_score=10)
            with self.assertLogs("marv.betcard", level="WARNING") as graded:
                betcard.grade(state, "nfl", [g])
            book = json.loads((state / "betcard_log.json").read_text())
            self.assertEqual(book[0]["result"], "void")
            self.assertEqual(book[0]["profit"], 0.0)
            self.assertNotAlmostEqual(book[0]["profit"], payout(-19))
            self.assertTrue(any("void" in line and "ML price -19" in line for line in graded.output))
            self.assertNotIn("HIGH:", betcard.record(state))

    def test_real_ml_prices_and_boundaries_stay(self):
        for price in (-110, 150, -100, 100):
            p = pred("ok", "Lions", "Bears", [Pick("ml", "Lions", price, price, 0.72, 0.10, 0.08)])
            with self.assertNoLogs("marv.betcard", level="WARNING"):
                cands = betcard.candidates("nfl", [p])
            self.assertEqual(len(cands), 1, price)
            self.assertEqual((cands[0].market, cands[0].price), ("ml", price))

        start = datetime(2026, 11, 17, 18, tzinfo=timezone.utc)
        rows = [
            _row("ml", "Lions", -110, -110),   # home wins -> win
            _row("ml", "Lions", 150, 150),
            _row("ml", "Lions", -100, -100),
            _row("ml", "Lions", 100, 100),
            _row("ml", "Bears", 100, 100),     # away ML, home wins -> loss
        ]
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            (state / "betcard_log.json").write_text(json.dumps(rows))
            g = Game("ok", "nfl", start, "Lions", "Bears", completed=True, home_score=24, away_score=17)
            betcard.grade(state, "nfl", [g])
            book = json.loads((state / "betcard_log.json").read_text())
            by_price_side = {(b["side"], b["price"]): b for b in book}
            for side, price in (("Lions", -110), ("Lions", 150), ("Lions", -100), ("Lions", 100)):
                row = by_price_side[(side, price)]
                self.assertEqual(row["result"], "win", (side, price))
                self.assertAlmostEqual(row["profit"], payout(price))
            loss = by_price_side[("Bears", 100)]
            self.assertEqual(loss["result"], "loss")
            self.assertAlmostEqual(loss["profit"], -1.0)
            self.assertTrue(all(b["result"] != "void" for b in book))

    def test_spreads_and_totals_are_not_price_checked(self):
        # -19 is a spread, 47.5 is a total. Both prices are real American odds (-110).
        p = pred("st", "Lions", "Bears", [
            Pick("ml", "Lions", -19, -19, 0.80, 0.18, 0.12),
            Pick("spread", "Lions", -19, -110, 0.60, 0.08, 0.05),
            Pick("total", "Under", 47.5, -110, 0.57, 0.06, 0.04),
            Pick("total", "Over", 45.5, 150, 0.55, 0.04, 0.03),
        ])
        with self.assertLogs("marv.betcard", level="WARNING") as cm:
            cands = betcard.candidates("nfl", [p])
        self.assertTrue(any("ML price -19" in line for line in cm.output))
        self.assertFalse([b for b in cands if b.market == "ml"])
        kept = {(b.market, b.side, b.line, b.price) for b in cands}
        self.assertIn(("spread", "Lions", -19, -110), kept)
        self.assertIn(("total", "Under", 47.5, -110), kept)
        self.assertIn(("total", "Over", 45.5, 150), kept)

        start = datetime(2026, 11, 17, 18, tzinfo=timezone.utc)
        rows = [
            _row("spread", "Lions", -19, -110),   # 31-10 covers -19
            _row("total", "Under", 47.5, -110),   # 41 stays under 47.5
            _row("total", "Over", 45.5, 150),     # 41 stays under 45.5
        ]
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            (state / "betcard_log.json").write_text(json.dumps(rows))
            g = Game("st", "nfl", start, "Lions", "Bears", completed=True, home_score=31, away_score=10)
            betcard.grade(state, "nfl", [g])
            book = json.loads((state / "betcard_log.json").read_text())
            spread, under, over = book
            self.assertEqual(spread["result"], "win")
            self.assertAlmostEqual(spread["profit"], payout(-110))
            self.assertEqual(under["result"], "win")
            self.assertAlmostEqual(under["profit"], payout(-110))
            self.assertEqual(over["result"], "loss")
            self.assertAlmostEqual(over["profit"], -1.0)
            self.assertTrue(all(b["result"] != "void" for b in book))
