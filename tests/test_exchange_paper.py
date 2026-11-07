import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from marv import exchange_paper as X
from marv.models import Game, Odds

# Shapes copied from live api.elections.kalshi.com / gamma-api.polymarket.com responses (Nov 7 2026).
K_MARKETS = [
    {"ticker": "KXNBAGAME-26NOV07BKNATL-ATL", "status": "active", "title": "Atlanta wins", "yes_sub_title": "Atlanta",
     "yes_ask_dollars": "0.7200", "yes_bid_dollars": "0.7100", "no_ask_dollars": "0.2900", "no_bid_dollars": "0.2800"},
    {"ticker": "KXNBASPREAD-26NOV07BKNATL-ATL7", "status": "active", "title": "Atlanta wins by over 7.5 points",
     "floor_strike": 7.5, "yes_ask_dollars": "0.4800", "yes_bid_dollars": "0.4600"},
    {"ticker": "KXNBATOTAL-26NOV07BKNATL-230", "status": "active", "title": "Full Game: Over 229.5 points scored",
     "floor_strike": 229.5, "yes_ask_dollars": "0.4400", "yes_bid_dollars": "0.4300", "no_ask_dollars": "0.5700",
     "no_bid_dollars": "0.5600"},
    {"ticker": "KXNBAPTS-26NOV07BKNATL-ATLJJOHNSON1-20", "status": "active", "title": "Jalen Johnson: 20+ points",
     "floor_strike": 19.5, "yes_ask_dollars": "0.5000", "yes_bid_dollars": "0.4700"},
    {"ticker": "KXNBATOTAL-26NOV07BKNATL-250", "status": "finalized", "title": "x", "floor_strike": 249.5,
     "yes_ask_dollars": "0.1", "yes_bid_dollars": "0.05"},
]
SCHED = {"exponent": 1, "rate": 0.05, "takerOnly": True}
PM_EVENT = {"title": "Nets vs. Hawks", "eventDate": "2026-11-07", "markets": [
    {"sportsMarketType": "moneyline", "question": "Nets vs. Hawks", "outcomes": '["Nets", "Hawks"]',
     "bestBid": 0.28, "bestAsk": 0.29, "feesEnabled": True, "feeSchedule": SCHED, "conditionId": "0xml"},
    {"sportsMarketType": "spreads", "line": -7.5, "question": "Spread: Hawks (-7.5)", "outcomes": '["Hawks", "Nets"]',
     "bestBid": 0.46, "bestAsk": 0.47, "feesEnabled": True, "feeSchedule": SCHED, "conditionId": "0xsp"},
    {"sportsMarketType": "totals", "line": 229.5, "question": "Nets vs. Hawks: O/U 229.5", "outcomes": '["Over", "Under"]',
     "bestBid": 0.43, "bestAsk": 0.44, "feesEnabled": True, "feeSchedule": SCHED, "conditionId": "0xtot"},
    {"sportsMarketType": "points", "line": 19.5, "question": "Jalen Johnson: Points O/U 19.5", "outcomes": '["Yes", "No"]',
     "bestBid": 0.48, "bestAsk": 0.5, "feesEnabled": True, "feeSchedule": SCHED, "conditionId": "0xpts"},
]}
HOME, AWAY = "Atlanta Hawks", "Brooklyn Nets"


class ParseTests(unittest.TestCase):
    def test_kalshi_both_sides_and_kinds(self):
        cs = X.kalshi_contracts("nba", K_MARKETS)
        kinds = sorted({c["kind"] for c in cs})
        self.assertEqual(kinds, ["ml", "prop", "spread", "total"])
        self.assertEqual(len(cs), 8)  # finalized market skipped; NO side derived from 1 - yes bid when missing
        sp_no = next(c for c in cs if c["kind"] == "spread" and c["direction"] == "under")
        self.assertAlmostEqual(sp_no["ask"], 0.54)
        self.assertEqual((sp_no["team"], sp_no["line"]), ("Atlanta", 7.5))
        prop = next(c for c in cs if c["kind"] == "prop" and c["direction"] == "over")
        self.assertEqual((prop["player"], prop["market"], prop["line"]), ("Jalen Johnson", "player_points", 19.5))
        self.assertAlmostEqual(prop["fee"], 0.07 * 0.5 * 0.5)

    def test_polymarket_spread_sides_follow_question(self):
        cs = X.pm_contracts("nba", PM_EVENT)
        sp = [c for c in cs if c["kind"] == "spread"]
        hawks = next(c for c in sp if c["team"] == "Hawks")
        nets = next(c for c in sp if c["team"] == "Nets")
        self.assertEqual((hawks["line"], hawks["ask"]), (7.5, 0.47))   # Hawks must win by 8+
        self.assertEqual((nets["line"], nets["ask"]), (-7.5, 0.54))    # Nets +7.5 = 1 - Hawks bid
        self.assertAlmostEqual(hawks["fee"], 0.05 * 0.47 * 0.53)
        under = next(c for c in cs if c["kind"] == "total" and c["direction"] == "under")
        self.assertAlmostEqual(under["ask"], 0.57)

    def test_event_matching_needs_both_teams_and_date(self):
        self.assertGreaterEqual(X.match_event(("Brooklyn", "Atlanta"), HOME, AWAY), 0.75)
        self.assertLess(X.match_event(("Brooklyn", "Boston"), HOME, AWAY), 0.75)
        start = datetime(2026, 11, 8, 0, 30, tzinfo=timezone.utc)  # 7:30 pm ET on Nov 7
        self.assertTrue(X._date_ok(start, X._kalshi_date("KXNBAGAME-26NOV07BKNATL")))
        self.assertFalse(X._date_ok(start, X._kalshi_date("KXNBAGAME-26NOV12BKNATL")))


class FairAndDecideTests(unittest.TestCase):
    ST = {"p_home": 0.80, "exp_margin": 10.0, "sd_m": 8.0, "exp_total": 236.0, "sd_t": 9.0}

    def test_fair_prices_are_consistent(self):
        cs = X.kalshi_contracts("nba", K_MARKETS) + X.pm_contracts("nba", PM_EVENT)
        f = {(c["venue"], c["kind"], c["direction"], c.get("team")): X.fair(c, self.ST, HOME, AWAY, {}) for c in cs
             if c["kind"] != "prop"}
        self.assertAlmostEqual(f[("kalshi", "ml", "over", "Atlanta")], 0.80)
        self.assertAlmostEqual(f[("kalshi", "ml", "not", "Atlanta")], 0.20)
        self.assertAlmostEqual(f[("polymarket", "ml", "over", "Nets")], 0.20)
        # Kalshi 'Atlanta by over 7.5' == Polymarket 'Hawks (-7.5)'
        self.assertAlmostEqual(f[("kalshi", "spread", "over", "Atlanta")], f[("polymarket", "spread", "over", "Hawks")])
        self.assertAlmostEqual(f[("polymarket", "spread", "over", "Nets")], 1 - f[("polymarket", "spread", "over", "Hawks")])
        self.assertGreater(f[("kalshi", "total", "over", None)], 0.7)

    def test_decide_takes_cheap_contracts_only(self):
        cs = X.kalshi_contracts("nba", K_MARKETS) + X.pm_contracts("nba", PM_EVENT)
        priced, picks = X.decide(cs, self.ST, HOME, AWAY, {}, None, 24.0, "nba")
        self.assertTrue(picks)
        self.assertTrue(all(p["edge"] >= 0.05 for p in picks))
        self.assertLessEqual(len({(p["kind"]) for p in picks}), len(picks))
        self.assertLessEqual(len(picks), 3)
        # nothing is bought in the last 3 minutes or in the first 6
        self.assertEqual(X.decide(cs, self.ST, HOME, AWAY, {}, None, 2.0, "nba")[1], [])
        self.assertEqual(X.decide(cs, self.ST, HOME, AWAY, {}, None, 45.0, "nba")[1], [])

    def test_bovada_veto_and_liquidity(self):
        cs = X.kalshi_contracts("nba", K_MARKETS[:1])
        st = {**self.ST, "p_home": 0.85}
        bov_against = Odds(home_ml=-150, away_ml=130)  # Bovada: Atlanta ~58%, far below the 72c ask
        priced, picks = X.decide(cs, st, HOME, AWAY, {}, bov_against, 24.0, "nba")
        atl = next(r for r in priced if r["team"] == "Atlanta" and r["direction"] == "over")
        self.assertEqual(atl["reason"], "bovada disagrees")
        wide = [{**K_MARKETS[0], "yes_bid_dollars": "0.40", "no_ask_dollars": "0.60"}]
        priced, _ = X.decide(X.kalshi_contracts("nba", wide), st, HOME, AWAY, {}, None, 24.0, "nba")
        self.assertEqual(next(r for r in priced if r["direction"] == "over")["reason"], "no liquidity")


class LedgerTests(unittest.TestCase):
    def test_record_grade_pnl_and_clv(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cs = X.kalshi_contracts("nba", K_MARKETS)
            ml = next(c for c in cs if c["kind"] == "ml" and c["direction"] == "over")
            tot = next(c for c in cs if c["kind"] == "total" and c["direction"] == "under")
            picks = [{**ml, "fair": 0.85, "edge": 0.12, "bovada_p": None},
                     {**tot, "fair": 0.70, "edge": 0.12, "bovada_p": None}]
            new = X.record(d, "nba", "g1", "BKN @ ATL", "end of Q2", picks, picks)
            self.assertEqual(len(new), 2)
            self.assertEqual(new[0]["contracts"], 13)  # $10 // 0.72
            self.assertEqual(X.record(d, "nba", "g1", "BKN @ ATL", "end of Q3", picks,
                                      [{**ml, "bid": 0.80, "ask": 0.81}]), [])  # never twice; marks closing value
            done = X.grade(d, "nba", "g1", HOME, AWAY, 118, 104, None)
            res = {t["kind"]: t for t in done}
            self.assertEqual(res["ml"]["result"], "win")
            self.assertAlmostEqual(res["ml"]["pnl"], round(13 - 13 * 0.72 - res["ml"]["fee_total"], 2))
            self.assertAlmostEqual(res["ml"]["clv"], 0.08)
            self.assertEqual(res["total"]["result"], "win")  # 222 points < 229.5: the Under ticket pays
            s = X.summary_stats(X.load(d))
            self.assertEqual(s["n"], 2)
            self.assertIn("Bar before any real order", X.report(d))

    def test_settle_rules(self):
        t = {"kind": "spread", "team": "Atlanta", "line": 7.5, "direction": "over"}
        self.assertEqual(X.settle(t, HOME, AWAY, 110, 100, None), "win")
        self.assertEqual(X.settle({**t, "direction": "under"}, HOME, AWAY, 110, 100, None), "loss")
        self.assertEqual(X.settle({"kind": "ml", "team": "Nets", "direction": "not"}, HOME, AWAY, 110, 100, None), "win")
        p = {"kind": "prop", "player": "Jalen Johnson", "market": "player_points", "line": 19.5, "direction": "over"}
        self.assertEqual(X.settle(p, HOME, AWAY, 1, 0, lambda *_: 22.0), "win")
        self.assertEqual(X.settle(p, HOME, AWAY, 1, 0, lambda *_: None), "void")


class HookTests(unittest.TestCase):
    def test_on_break_and_final_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            s = mock.Mock(state_dir=d)
            client = mock.Mock()
            client.summary.return_value = {"boxscore": {"players": [], "teams": []}}
            g = Game("g9", "nba", datetime(2026, 11, 8, 0, 30, tzinfo=timezone.utc), HOME, AWAY,
                     info={"live_home": 60, "live_away": 50, "clock": "0:00"})
            st = {"p_home": 0.85, "exp_margin": 12.0, "sd_m": 7.0, "exp_total": 238.0, "sd_t": 9.0}
            sent = []
            with mock.patch.object(X, "kalshi_game", return_value=X.kalshi_contracts("nba", K_MARKETS)), \
                    mock.patch.object(X, "pm_game", return_value=X.pm_contracts("nba", PM_EVENT)):
                self.assertEqual(X.on_break(s, client, "nba", g, 2, "end of Q2", st, sent.append), 1)
                self.assertEqual(X.on_break(s, client, "nba", g, 2, "end of Q2", st, sent.append), 0)
            self.assertIn("EXCHANGE PAPER", sent[0])
            self.assertIn("Paper only", sent[0])
            g.home_score, g.away_score = 121, 101
            self.assertEqual(X.on_final(s, client, "nba", g, sent.append), 1)
            self.assertIn("Paper record", sent[-1])
            self.assertTrue((Path(d) / "exchange_paper" / "snapshots.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
