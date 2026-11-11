import os, sys, types, json, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kalpoly as k

T = "2026-11-12T00:30:00Z"
KAL = {"events": [
    {"title": "Lakers vs Celtics", "category": "Sports", "markets": [
        {"title": "Will the Lakers win?", "yes_sub_title": "Lakers", "yes_ask_dollars": "0.4000", "yes_bid_dollars": "0.3800",
         "no_ask_dollars": "0.6200", "expected_expiration_time": T, "ticker": "KX-LAL"},
        {"title": "Will the Celtics win?", "yes_sub_title": "Celtics", "yes_ask": 61, "yes_bid": 59, "no_ask": 41,
         "expected_expiration_time": T, "ticker": "KX-BOS"}]},
    {"title": "LeBron James points", "category": "Sports", "markets": [
        {"title": "LeBron James: 25+ points", "yes_sub_title": "25+", "yes_ask_dollars": "0.4000", "yes_bid_dollars": "0.3800",
         "no_ask_dollars": "0.6200", "expected_expiration_time": T, "ticker": "KX-LBJ25"}]},
    {"title": "Election", "category": "Politics", "markets": [
        {"title": "x", "yes_ask_dollars": "0.5", "yes_bid_dollars": "0.4", "ticker": "POL"}]}], "cursor": ""}
POLY = [{"title": "Lakers vs. Celtics", "markets": [
    {"question": "Lakers vs. Celtics", "outcomes": json.dumps(["Lakers", "Celtics"]), "outcomePrices": json.dumps(["0.45", "0.55"]),
     "bestBid": "0.44", "bestAsk": "0.46", "gameStartTime": "2026-11-12 00:00:00+00", "slug": "lal-bos"}]}]
BOOK_EVENTS = [{"id": "e1"}]
BOOK_ODDS = {"away_team": "Los Angeles Lakers", "home_team": "Boston Celtics", "commence_time": T, "bookmakers": [
    {"key": "pinnacle", "markets": [{"key": "player_points", "outcomes": [
        {"name": "Over", "description": "LeBron James", "point": 24.5, "price": -120},
        {"name": "Under", "description": "LeBron James", "point": 24.5, "price": 100}]}]},
    {"key": "draftkings", "markets": [{"key": "player_points", "outcomes": [
        {"name": "Over", "description": "LeBron James", "point": 24.5, "price": -110},
        {"name": "Under", "description": "LeBron James", "point": 24.5, "price": -110}]}]}]}

def fake_get(url, params=None, tries=3):
    if "kalshi" in url: return KAL
    if "gamma" in url: return POLY if (params or {}).get("offset", 0) == 0 else []
    if url.endswith("/events"): return BOOK_EVENTS
    if "/odds" in url: return BOOK_ODDS
    raise AssertionError(url)

def args(**kw):
    d = dict(kalshi_series=None, poly_tag="sports", poly_fee=0.0, min_edge=0.01, sport="basketball_nba",
             markets="player_points", regions="us,eu", max_events=5)
    d.update(kw); return types.SimpleNamespace(**d)

class T1(unittest.TestCase):
    def setUp(self):
        k.get = fake_get; os.environ["ODDS_API_KEY"] = "dummy"; k.OUT_DIR = "out_test"
    def test_kalshi_prices_and_filter(self):
        kal = k.fetch_kalshi()
        self.assertEqual({c["id"] for c in kal}, {"KX-LAL", "KX-BOS", "KX-LBJ25"})
        bos = next(c for c in kal if c["id"] == "KX-BOS")
        self.assertAlmostEqual(bos["yes_ask"], 0.61); self.assertAlmostEqual(bos["no_ask"], 0.41)
    def test_poly_two_rows_per_team_market(self):
        p = k.fetch_poly()
        lal = next(r for r in p if r["outcome"] == "Lakers"); bos = next(r for r in p if r["outcome"] == "Celtics")
        self.assertAlmostEqual(lal["yes_ask"], 0.46); self.assertAlmostEqual(bos["yes_ask"], 0.56)   # 1 - bid
        self.assertAlmostEqual(bos["no_ask"], 0.46)
    def test_gaps_pairs_same_team_and_math(self):
        rows, kal, poly = k.cmd_gaps(args(), save=False)
        pair = {(r["kalshi"], r["poly"]) for r in rows}
        self.assertIn(("Will the Lakers win? [Lakers]", "Lakers vs. Celtics [Lakers]"), pair)
        self.assertNotIn(("Will the Lakers win? [Lakers]", "Lakers vs. Celtics [Celtics]"), pair)   # no wrong-side pairing
        r = next(r for r in rows if r["kalshi"].startswith("Will the Lakers") and r["trade"] == "K yes + P no")
        self.assertAlmostEqual(r["cost_w_fees"], round(0.40 + 0.56 + k.kalshi_fee(0.40), 4))
    def test_devig_and_ev(self):
        props = k.devig_props(k.fetch_book_props("basketball_nba", "player_points", "us", 5))
        self.assertEqual(len(props), 1)
        p = props[0]
        pin = (120/220) / (120/220 + 0.5); dk = 0.5
        self.assertAlmostEqual(p["fair_over"], round(sorted([pin, pin, dk])[1], 4), places=4)
        self.assertEqual(p["best_over"], ("draftkings", -110))
    def test_exchange_prop_match(self):
        props = k.devig_props(k.fetch_book_props("basketball_nba", "player_points", "us", 5))
        hits = k.find_exchange_prop(props[0], k.fetch_kalshi())
        self.assertEqual([(c["id"], o) for c, o in hits], [("KX-LBJ25", True)])
    def test_books_runs_end_to_end(self):
        ev, ex = k.cmd_books(args(), save=False)
        self.assertTrue(ev and ex)
    def test_model_math(self):
        # Poisson sanity: mean 2, P(X>1.5) = 1 - e^-2 (1 + 2)
        self.assertAlmostEqual(k.nb_or_poisson_over(2, 2, 1.5), 1 - 3 * __import__("math").exp(-2), places=9)
        # NB mean matches when line is far out
        self.assertAlmostEqual(k.nb_or_poisson_over(20, 60, -0.5), 1.0, places=9)
        self.assertEqual(k.prob_to_american(0.5), -100); self.assertEqual(k.prob_to_american(0.25), 300)

class Fixes(unittest.TestCase):
    def test_ambiguous_unlabeled_contract_is_not_paired(self):
        T = "2026-11-12T00:30:00Z"
        kal = [{"src": "kalshi", "event": "Lakers vs Celtics", "question": "Lakers vs Celtics: Lakers to win?", "outcome": "",
                "yes_ask": 0.40, "no_ask": 0.62, "time": T, "id": "K1"}]
        poly = [{"src": "poly", "event": "Lakers vs. Celtics", "question": "Lakers vs. Celtics", "outcome": o, "yes_ask": a, "no_ask": b,
                 "time": T, "id": "P"} for o, a, b in (("Lakers", 0.46, 0.56), ("Celtics", 0.56, 0.46))]
        self.assertEqual(k.match_markets(kal, poly, min_outcome=0.0), [])       # even with the outcome-similarity bar removed
        kal[0]["outcome"] = "Lakers"
        m = k.match_markets(kal, poly, min_outcome=0.0)
        self.assertEqual([p["outcome"] for _, _, p in m], ["Lakers"])
    def test_poly_market_without_live_book_is_skipped(self):
        import json
        k.get = lambda url, params=None, tries=3: ([{"title": "X vs Y", "markets": [{"question": "X vs Y", "outcomes": json.dumps(["X", "Y"]),
                    "outcomePrices": json.dumps(["0.30", "0.30"]), "slug": "xy"}]}] if (params or {}).get("offset", 0) == 0 else [])
        self.assertEqual(k.fetch_poly(), [])
    def test_kalshi_fee_rounds_up_per_order(self):
        self.assertAlmostEqual(k.kalshi_fee(0.5, contracts=1), 0.02)
        self.assertAlmostEqual(k.kalshi_fee(0.5, contracts=10), 0.018)     # ceil(17.5c) = 18c over 10 contracts
        self.assertAlmostEqual(k.kalshi_fee(0.5, contracts=100), 0.0175)

if __name__ == "__main__":
    unittest.main(verbosity=1)
