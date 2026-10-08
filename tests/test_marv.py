import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from marv.data.espn import parse_event
from marv.data.nflverse import parse_rows
from marv.data.oddsapi import attach
from marv.data.teams import similarity
from marv.engine import grade, market_implied, predict
from marv.markets import evaluate, no_vig, settle
from marv.models import Game, Odds, Pick
from marv.ratings import RatingParams, fit_ratings
from marv.sims import baseball, basketball, football, hockey, soccer
from marv.sports import SPORTS, pitcher_factor
from marv.state import Store
from marv.telegram import format_card, split_message

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def league(sport="nba", n_teams=20, days=60, base=110.0, sd=10.0, mult=False, seed=1):
    rng = np.random.default_rng(seed)
    teams = [f"Team {i}" for i in range(n_teams)]
    strength = dict(zip(teams, rng.normal(0, 1, n_teams)))
    games = []
    for day in range(days):
        order = rng.permutation(teams)
        for i, (h, a) in enumerate(zip(order[::2], order[1::2])):
            if mult:
                hs = rng.poisson(base * np.exp(0.25 * (strength[h] - strength[a])) * 1.05)
                as_ = rng.poisson(base * np.exp(0.25 * (strength[a] - strength[h])) / 1.05)
            else:
                hs = round(base + 4 * (strength[h] - strength[a]) + 1.5 + rng.normal(0, sd))
                as_ = round(base + 4 * (strength[a] - strength[h]) - 1.5 + rng.normal(0, sd))
            games.append(Game(f"{day}-{i}", sport, T0 + timedelta(days=day), str(h), str(a),
                              completed=True, home_score=hs, away_score=as_))
    return games, strength


class MarketsTest(unittest.TestCase):
    def test_settle_and_quarter_lines(self):
        diff = np.array([2.0, 1.0, 0.0, -1.0])
        self.assertEqual(settle(diff, -1.0).tolist(), [1, 0, -1, -1])
        # -0.25: half on 0 (push on a draw), half on -0.5 (loss on a draw)
        self.assertEqual(settle(np.array([0.0]), -0.25).tolist(), [-0.5])
        self.assertEqual(settle(np.array([0.0]), 0.25).tolist(), [0.5])

    def test_evaluate_matches_break_even(self):
        outcome = np.array([1.0] * 524 + [-1.0] * 476)
        prob, ev, edge = evaluate(outcome, -110)
        self.assertAlmostEqual(prob, 0.524)
        self.assertAlmostEqual(edge, 0.0, delta=0.002)

    def test_no_vig(self):
        self.assertAlmostEqual(sum(no_vig(-110, -110)), 1.0)
        self.assertAlmostEqual(no_vig(-110, -110)[0], 0.5)


class RatingsTest(unittest.TestCase):
    def test_additive_recovers_strength(self):
        games, strength = league()
        r = fit_ratings(games, RatingParams(multiplicative=False, home_adv=2.0))
        est = [r.strength(t) for t in strength]
        self.assertGreater(np.corrcoef(est, list(strength.values()))[0, 1], 0.9)
        self.assertAlmostEqual(r.home_adv, 3.0, delta=1.0)

    def test_multiplicative_recovers_strength(self):
        games, strength = league("nhl", base=3.0, mult=True, days=80)
        r = fit_ratings(games, RatingParams(multiplicative=True, home_adv=1.05), as_of=T0 + timedelta(days=80))
        est = [r.strength(t) for t in strength]
        self.assertGreater(np.corrcoef(est, list(strength.values()))[0, 1], 0.8)
        self.assertAlmostEqual(r.home_adv, 1.05 ** 2, delta=0.08)


class SimTest(unittest.TestCase):
    rng = np.random.default_rng(0)

    def test_realistic_spreads(self):
        cases = [
            (football.simulate_game(23, 21, football.NFL, n=40000, rng=self.rng), 12.5, 15.5),
            (football.simulate_game(30, 27, football.CFB, n=40000, rng=self.rng), 14.5, 17.5),
            (basketball.simulate_game(115, 112, basketball.NBA, n=40000, rng=self.rng), 12, 14.5),
            (hockey.simulate_game(3.1, 2.9, n=40000, rng=self.rng), 2.1, 2.7),
            (baseball.simulate_game(4.6, 4.4, n=40000, rng=self.rng), 3.8, 4.6),
            (soccer.simulate_game(1.5, 1.1, n=40000, rng=self.rng), 1.4, 1.8),
        ]
        for sim, lo, hi in cases:
            self.assertTrue(lo <= sim.margin.std() <= hi, sim.margin.std())

    def test_no_ties_except_soccer(self):
        for sim in (football.simulate_game(20, 20, n=5000, rng=self.rng),
                    basketball.simulate_game(110, 110, n=5000, rng=self.rng),
                    hockey.simulate_game(3, 3, n=5000, rng=self.rng),
                    baseball.simulate_game(4.5, 4.5, n=5000, rng=self.rng)):
            self.assertEqual(sim.draw(), 0.0)
        self.assertGreater(soccer.simulate_game(1.2, 1.2, n=5000, rng=self.rng).draw(), 0.2)

    def test_garbage_time_and_hockey_ot(self):
        damped = football.simulate_game(45, 10, n=40000, rng=np.random.default_rng(1))
        free = football.simulate_game(45, 10, football.FootballParams(garbage_margin=999), n=40000,
                                      rng=np.random.default_rng(1))
        self.assertLess(damped.margin.mean(), free.margin.mean() - 1)
        h = hockey.simulate_game(3.0, 3.0, n=40000, rng=self.rng)
        self.assertTrue(0.17 < h.reg_tie.mean() < 0.26)

    def test_pitcher_factor(self):
        self.assertLess(pitcher_factor(2.0), 1.0)
        self.assertGreater(pitcher_factor(6.0), 1.0)
        self.assertEqual(pitcher_factor(None), 1.0)


class EngineTest(unittest.TestCase):
    def slate(self, sport, odds, n=4, base=110.0, mult=False):
        games, _ = league(sport, base=base, mult=mult, days=40)
        upcoming = [Game(f"u{i}", sport, T0 + timedelta(days=41), f"Team {2 * i}", f"Team {2 * i + 1}", odds=odds)
                    for i in range(n)]
        return games, upcoming

    def test_predict_every_sport(self):
        cases = {
            "nba": (110.0, False, Odds(spread=-3.5, total=221.5, home_ml=-160, away_ml=135)),
            "wnba": (82.0, False, Odds(spread=-2.5, total=163.5, home_ml=-140, away_ml=120)),
            "nfl": (22.0, False, Odds(spread=-3, total=44.5, home_ml=-150, away_ml=130)),
            "nhl": (3.0, True, Odds(spread=-1.5, home_spread_price=180, away_spread_price=-220,
                                   total=6.0, home_ml=-130, away_ml=110)),
            "mlb": (4.5, True, Odds(spread=-1.5, home_spread_price=150, away_spread_price=-170,
                                   total=8.5, home_ml=-125, away_ml=105)),
            "soccer": (1.4, True, Odds(spread=-0.25, total=2.5, home_ml=150, away_ml=180, draw_ml=230)),
        }
        for key, (base, mult, odds) in cases.items():
            history, upcoming = self.slate(key, odds, base=base, mult=mult)
            preds = predict(SPORTS[key], history, upcoming, T0 + timedelta(days=41), simulations=4000,
                            rng=np.random.default_rng(0))
            self.assertEqual(len(preds), 4, key)
            markets = {pk.market for p in preds for pk in p.picks}
            self.assertTrue({"spread", "total"} <= markets, key)
            if key == "nfl":
                preds[0].notes.append("O/U spots (tracked, not picks): TOTAL-INFLATED→Under [55.8%]")
                preds[1].notes.append("line move: total 44.5 → 47 (+2.5)")
            text = format_card(SPORTS[key], preds, {"spread": (3, 2, 0, 0.7)})
            self.assertIn("veto pass rate", text)
            if key == "nfl":
                self.assertIn("Tracked spots", text)
                block = text[text.index("Tracked spots"):]
                self.assertLess(block.index("TOTAL-INFLATED"), block.index("line move: total 44.5"))
            if key == "mlb":  # no probable pitchers in this fixture
                self.assertTrue(all("starting pitcher unconfirmed" in pk.vetoes for p in preds for pk in p.picks))

    def test_market_implied(self):
        h, a = market_implied(SPORTS["nba"], Odds(spread=-5, total=220))
        self.assertEqual((h, a), (112.5, 107.5))
        h, a = market_implied(SPORTS["nhl"], Odds(total=6.0, home_ml=-150, away_ml=130))
        self.assertGreater(h, a)
        self.assertAlmostEqual(h + a, 6.0)

    def test_trap_and_correlation_vetoes(self):
        history, upcoming = self.slate("nba", Odds(spread=-1.0, spread_open=-6.0, total=221.5,
                                                    home_ml=-115, away_ml=-105), n=1)
        sport = SPORTS["nba"]
        pred = predict(sport, history, upcoming, T0 + timedelta(days=41), 4000, model_weight=1.0,
                       rng=np.random.default_rng(0))[0]
        spread = next(p for p in pred.picks if p.market == "spread")
        if spread.side == pred.game.home:
            self.assertTrue(any("trap" in v for v in spread.vetoes))
        actives = [p for p in pred.picks if p.active and p.market in ("spread", "ml")]
        self.assertFalse(len(actives) == 2 and actives[0].side == actives[1].side)

    def test_grade(self):
        g = Game("1", "nba", T0, "A", "B", completed=True, home_score=110, away_score=100)
        self.assertAlmostEqual(grade(Pick("spread", "A", -7.5, -110, 0, 0, 0), g), 100 / 110)
        self.assertEqual(grade(Pick("spread", "B", 7.5, -110, 0, 0, 0), g), -1)
        self.assertEqual(grade(Pick("spread", "A", -10, -110, 0, 0, 0), g), 0)
        self.assertAlmostEqual(grade(Pick("total", "Under", 215.5, -110, 0, 0, 0), g), 100 / 110)
        self.assertEqual(grade(Pick("total", "Over", 215.5, -110, 0, 0, 0), g), -1)
        self.assertAlmostEqual(grade(Pick("ml", "B", 150, 150, 0, 0, 0), g), -1)
        draw = Game("2", "soccer", T0, "A", "B", completed=True, home_score=1, away_score=1)
        self.assertAlmostEqual(grade(Pick("draw", "Draw", 230, 230, 0, 0, 0), draw), 2.3)
        self.assertAlmostEqual(grade(Pick("spread", "A", -0.25, -110, 0, 0, 0), draw), -0.5)


class DataTest(unittest.TestCase):
    def test_espn_event(self):
        ev = {"id": "401", "date": "2026-01-10T00:30Z", "season": {"type": 2},
              "status": {"type": {"completed": False, "state": "pre", "name": "STATUS_SCHEDULED"}},
              "competitions": [{"neutralSite": False, "competitors": [
                  {"homeAway": "home", "score": "0", "team": {"displayName": "Boston Celtics", "abbreviation": "BOS"},
                   "probables": [{"athlete": {"displayName": "Ace"},
                                  "statistics": [{"abbreviation": "ERA", "displayValue": "3.10"}]}]},
                  {"homeAway": "away", "score": "0", "team": {"displayName": "LA Lakers", "abbreviation": "LAL"}}],
                  "odds": [{"provider": {"name": "ESPN BET"}, "details": "LAL -2.5", "overUnder": 228.5,
                            "homeTeamOdds": {"moneyLine": 120, "favorite": False},
                            "awayTeamOdds": {"moneyLine": -140, "favorite": True}}]}]}
        g = parse_event(ev, "nba")
        self.assertEqual((g.home, g.away, g.completed), ("Boston Celtics", "LA Lakers", False))
        self.assertEqual(g.odds.spread, 2.5)  # away favored -> home +2.5
        self.assertEqual((g.odds.total, g.odds.home_ml, g.odds.away_ml), (228.5, 120, -140))
        self.assertEqual(g.info["home_pitcher_era"], 3.10)
        self.assertEqual(g.start, datetime(2026, 1, 10, 0, 30, tzinfo=timezone.utc))

    def test_nflverse_rows(self):
        rows = [{"game_id": "2026_01_NE_SEA", "season": "2026", "game_type": "REG", "week": "1",
                 "gameday": "2026-09-09", "gametime": "20:20", "away_team": "NE", "home_team": "SEA",
                 "away_score": "10", "home_score": "13", "location": "Home", "spread_line": "3",
                 "total_line": "44.5", "home_moneyline": "-166", "away_moneyline": "140",
                 "home_spread_odds": "-118", "away_spread_odds": "-102", "over_odds": "-108", "under_odds": "-112"}]
        g = parse_rows(rows)[0]
        self.assertEqual((g.home, g.away), ("Seattle Seahawks", "New England Patriots"))
        self.assertEqual(g.odds.spread, -3.0)  # nflverse: positive spread_line = home favored
        self.assertEqual(g.start.hour, 0)  # 20:20 ET -> 00:20 UTC
        self.assertTrue(g.completed)

    def test_odds_api_matching(self):
        game = Game("x", "nba", T0, "LA Clippers", "Boston Celtics")
        ev = {"home_team": "Los Angeles Clippers", "away_team": "Boston Celtics",
              "commence_time": "2026-01-01T01:00:00Z",
              "bookmakers": [{"markets": [{"key": "totals", "outcomes": [
                  {"name": "Over", "point": 220.5, "price": -110}, {"name": "Under", "point": 220.5, "price": -110}]}]}]}
        self.assertEqual(attach([game], [ev]), 1)
        self.assertEqual(game.odds.total, 220.5)
        self.assertGreater(similarity("Manchester City FC", "Man City"), 0.9)

    def test_odds_api_prefers_book(self):
        def book(key, total):
            return {"key": key, "title": key.title(), "markets": [{"key": "totals", "outcomes": [
                {"name": "Over", "point": total, "price": -110}, {"name": "Under", "point": total, "price": -110}]}]}
        ev = {"home_team": "Los Angeles Clippers", "away_team": "Boston Celtics", "commence_time": "2026-01-01T01:00:00Z",
              "bookmakers": [book("draftkings", 220.5), book("fanduel", 220.5), book("bovado", 221.5)]}
        game = Game("x", "nba", T0, "LA Clippers", "Boston Celtics")
        attach([game], [ev], book="bovado")
        self.assertEqual((game.odds.total, game.odds.provider), (221.5, "Bovado"))
        ev["bookmakers"] = ev["bookmakers"][:2]  # Bovado hasn't posted: fall back to the consensus
        attach([game], [ev], book="bovado")
        self.assertEqual(game.odds.total, 220.5)


class StateTest(unittest.TestCase):
    def test_ledger_round_trip(self):
        import tempfile
        from pathlib import Path
        from marv.models import Prediction
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d))
            g = Game("g1", "nba", datetime.now(timezone.utc), "A", "B", odds=Odds(spread=-3, total=220))
            store.remember_lines("nba", [g])
            self.assertEqual(g.odds.spread_open, -3)
            pred = Prediction(g, 112, 108, 4, 220, 0.6, picks=[Pick("spread", "A", -3, -110, 0.6, 0.1, 0.05)])
            self.assertEqual(store.new_picks("nba", [pred]), 1)
            store.log_picks("nba", [pred])
            self.assertEqual(store.new_picks("nba", [pred]), 0)  # already sent: the next run stays quiet
            done = Game("g1", "nba", g.start, "A", "B", completed=True, home_score=110, away_score=100)
            self.assertEqual(store.resolve("nba", [done]), 1)
            w, l, p, u = store.record("nba")["spread"]
            self.assertEqual((w, l), (1, 0))

    def test_split_message(self):
        chunks = split_message("\n\n".join(["x" * 1500] * 5) + "\n\n" + "y" * 9000)
        self.assertTrue(all(len(c) <= 4096 for c in chunks))


if __name__ == "__main__":
    unittest.main()
