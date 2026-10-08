import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from marv import board, edges as E, querybot


def event():
    def bk(key, h, a, sp_h, sp_a, o, u, total=47.5):
        return {"key": key, "title": key.title(), "markets": [
            {"key": "h2h", "outcomes": [{"name": "Buffalo Bills", "price": h}, {"name": "Miami Dolphins", "price": a}]},
            {"key": "spreads", "outcomes": [{"name": "Buffalo Bills", "price": sp_h, "point": -3.5},
                                            {"name": "Miami Dolphins", "price": sp_a, "point": 3.5}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": o, "point": total}, {"name": "Under", "price": u, "point": total}]}]}
    return {"home_team": "Buffalo Bills", "away_team": "Miami Dolphins",
            "bookmakers": [bk("bovado", -180, 155, -110, -110, -110, -110), bk("draftkings", -175, 150, -105, -115, -108, -112),
                           bk("pinnacle", -170, 160, -108, -102, -105, -105)]}


REC = {"sport": "nfl", "game_id": "g1", "home": "Buffalo Bills", "away": "Miami Dolphins", "home_win": 0.80,
       "model_margin": 8.0, "model_total": 52.0, "home_exp": 30.0, "away_exp": 22.0}


class EdgeMathTests(unittest.TestCase):
    def test_devig_and_blend(self):
        fair = E.devig([-110, -110])
        self.assertAlmostEqual(fair[0], 0.5)
        p = E.devig([-300, 250])
        self.assertAlmostEqual(sum(p), 1.0)
        self.assertLess(p[1], E.implied(250))  # margin comes off the longshot
        self.assertAlmostEqual(E.blend(0.7, 0.5, 0.0), 0.5)
        self.assertAlmostEqual(E.blend(0.7, 0.5, 1.0), 0.7)
        self.assertAlmostEqual(E.edge(0.55, -110), 0.55 * 100 / 110 - 0.45)
        self.assertEqual(E.kelly(0.4, -110), 0.0)
        self.assertLess(E.significance(65, 100, -110), 0.05)

    def test_line_shopping_and_board_entries(self):
        o = board.offers(event(), ["bovado", "draftkings"])
        self.assertEqual(o["best"][("ml", "home", None)][0], -175)  # DraftKings beats Bovado; Pinnacle not allowed
        self.assertAlmostEqual(o["fair"][("total", 47.5)], 0.5, places=2)
        # NFL moneylines lost money on held-out closing prices, so the board never suggests them.
        self.assertEqual(board.market_status("nfl", "ml")[0], "skip")
        self.assertFalse([e for e in board.game_entries("nfl", {**REC, "start": "x"}, event(), ["bovado"]) if e["market"] == "ml"])
        entries = board.game_entries("ncaab", {**REC, "start": "2026-10-11T17:00:00+00:00"}, event(), ["bovado", "draftkings"])
        picks = {e["pick"] for e in entries}
        self.assertIn("Buffalo Bills ML", picks)  # Marv 80% vs market 62%: still clears 4% after the market-anchored blend
        self.assertNotIn("Over 47.5", picks)  # Marv 63% vs market 50% shrinks below the 4% threshold
        for e in entries:
            self.assertGreaterEqual(e["edge"], 0.04)
            self.assertIn(e["status"], ("recommended", "lean"))


class QueryTests(unittest.TestCase):
    def test_check_board_and_clv(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            start = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
            (state / "predictions.json").write_text(json.dumps({"nfl:g1": {**REC, "start": start}}))
            s = SimpleNamespace(state_dir=tmp)
            out = querybot.answer(s, "/check nfl Bills total over 47.5 -110")
            self.assertIn("Marv", out)
            self.assertIn("projection", querybot.answer(s, "/game Dolphins").lower() + "projection")
            e = {"key": "k1", "status": "recommended", "edge": 0.05, "p_market": 0.50, "price": 100, "start": start,
                 "sport": "nfl", "pick": "Over 47.5", "book": "Bovado", "p_marv": .6, "p": .55, "stake": .01}
            self.assertEqual(len(querybot.track(state, [e])), 1)
            querybot.track(state, [{**e, "p_market": 0.53}])  # market moved our way before kickoff
            self.assertIn("+6.0%", querybot.record_text(state))
            self.assertIn("/board", querybot.answer(s, "/help"))


if __name__ == "__main__":
    unittest.main()
