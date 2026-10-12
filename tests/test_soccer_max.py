"""Marv Soccer Predict Max node: engine contract, no-% overlay, paper verdict (never a veto), grading."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from marv import bridge
from marv.soccer_max import data as D
from marv.soccer_max import engine as E
from marv.soccer_max import names as N
from marv.soccer_max import node as SN


def synthetic(seasons=(2024, 2025, 2026), seed=1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    teams = ["Arsenal", "Man United", "Nott'm Forest", "Brighton", "Wolves", "Everton", "Fulham", "Brentford"]
    strength = dict(zip(teams, np.linspace(0.5, -0.5, len(teams))))
    rows = []
    for s in seasons:
        day = pd.Timestamp(f"{s}-08-10")
        for rnd in range(14 if s < 2026 else 6):
            order = rng.permutation(teams)
            for i in range(0, len(order), 2):
                h, a = order[i], order[i + 1]
                lh, la = np.exp(0.3 + strength[h] - strength[a] * 0.5), np.exp(0.1 + strength[a] - strength[h] * 0.5)
                hg, ag = rng.poisson(lh), rng.poisson(la)
                hs, as_ = hg * 3 + rng.integers(4, 12), ag * 3 + rng.integers(3, 10)
                rows.append({"league": "eng.1", "date": day, "home": h, "away": a, "hg": hg, "ag": ag, "hs": hs,
                             "as_": as_, "hst": hg + rng.integers(1, 4), "ast": ag + rng.integers(0, 3),
                             "hc": rng.integers(2, 9), "ac": rng.integers(1, 8), "hxg": np.nan, "axg": np.nan,
                             "o_h": 2.1, "o_d": 3.4, "o_a": 3.6, "o_over": 1.9, "o_under": 1.95, "c_h": 2.05,
                             "c_d": 3.4, "c_a": 3.7, "c_over": 1.9, "c_under": 1.95, "season": s})
            day += pd.Timedelta(days=7)
    df = pd.DataFrame(rows)
    df["played"] = True
    return df


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.df = synthetic()
        self.proxy = E.fit_proxy(self.df) or (0.3, 0.03)
        self.r = E.fit_ratings(self.df, pd.Timestamp("2026-12-01"), E.Params(), self.proxy)

    def test_point_in_time(self):
        cut = pd.Timestamp("2025-09-01")
        r = E.fit_ratings(self.df, cut, E.Params(), self.proxy)
        later = self.df.copy()
        later.loc[later["date"] >= cut, ["hg", "ag"]] = 9  # future results must not move the ratings
        r2 = E.fit_ratings(later, cut, E.Params(), self.proxy)
        self.assertEqual(r["teams"]["Arsenal"]["att"], r2["teams"]["Arsenal"]["att"])

    def test_strength_order(self):
        t = self.r["teams"]
        self.assertGreater(t["Arsenal"]["att"] / t["Arsenal"]["def"], t["Brentford"]["att"] / t["Brentford"]["def"])

    def test_projection_contract(self):
        m = E.MarvSoccerPredictMax(self.r)
        p = m.project_match("Arsenal", "Brentford", key="x")
        self.assertAlmostEqual(p["p_home"] + p["p_draw"] + p["p_away"], 1.0, places=3)
        self.assertGreater(p["p_home"], p["p_away"])
        self.assertEqual(p, m.project_match("Arsenal", "Brentford", key="x"))  # deterministic seed
        self.assertLess(p["sim_gap"], 0.01)
        for k in ("home_xg", "away_xg", "p_btts", "totals", "asian_handicap", "team_totals", "score_distribution",
                  "confidence", "vetoes", "model_version"):
            self.assertIn(k, p)
        ah = p["asian_handicap"]["-0.25"]
        self.assertAlmostEqual(ah["home"] + ah["push"] + ah["away"], 1.0, places=3)

    def test_market_after_projection(self):
        m = E.MarvSoccerPredictMax(self.r)
        p = m.project_match("Arsenal", "Brentford")
        ev = m.evaluate_market(p, {"h": 1.5, "d": 4.2, "a": 7.0, "over": 1.8, "under": 2.0})
        self.assertEqual(len(ev["edges"]), 5)
        nv = E.novig([1.5, 4.2, 7.0])
        self.assertAlmostEqual(sum(nv), 1.0)
        bad = m.evaluate_market(p, {"h": 1.01, "d": 30, "a": 60})
        self.assertFalse(bad["qualified"])

    def test_vectorised_matches_single(self):
        m = E.score_matrices([1.4, 0.9], [1.1, 1.6], -0.06)
        s = E.score_matrices(1.4, 1.1, -0.06)[0]
        self.assertTrue(np.allclose(m[0], s))


class NodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        df = synthetic()
        r = E.fit_ratings(df, pd.Timestamp("2026-12-01"), E.Params(), E.fit_proxy(df) or (0.3, 0.03))
        r.update({"league": "eng.1"})
        (SN.out_dir(self.state) / "ratings_eng.1.json").write_text(json.dumps(r, default=float))

    def tearDown(self):
        self.tmp.cleanup()

    def test_names(self):
        teams = ["Man United", "Man City", "Nott'm Forest", "Inter", "Milan"]
        self.assertEqual(N.resolve("Manchester United", teams), "Man United")
        self.assertEqual(N.resolve("Nottingham Forest", teams), "Nott'm Forest")
        self.assertEqual(N.resolve("Inter Milan", teams), "Inter")
        self.assertEqual(N.resolve("AC Milan", teams), "Milan")
        self.assertIsNone(N.resolve("Real Madrid", teams))

    def test_league_keys(self):
        self.assertEqual(D.league_key("soccer_epl"), "eng.1")
        self.assertEqual(D.league_key("eng.1"), "eng.1")
        self.assertIsNone(D.league_key("ncaaf"))

    def test_overlay_paper_no_pct_no_veto(self):
        for market, side, line, live, text in [
            ("totals", "Under", 4.5, True, "🎯 TOTAL SWING — UNDER 4.5\n⚽ BRE 1–2 ARS · 64'"),
            ("totals", "Over", 2.5, False, ""),
            ("h2h", "Arsenal", None, False, ""),
            ("spreads", "Brentford", 0.5, False, ""),
            ("alternate_totals", "Under", 7.5, True, "no score here"),
        ]:
            o = bridge.overlay(self.state, "eng.1", market, side, line, "Arsenal", "Brentford FC", text, -120, live)
            self.assertTrue(o.found, market)
            self.assertEqual(o.verdict, "paper")
            line_txt = o.line()
            self.assertNotIn("%", line_txt.replace("no %", ""))
            self.assertNotIn("👎", line_txt)
            self.assertIn("paper", line_txt)
        log = (SN.out_dir(self.state) / "overlay_log.jsonl").read_text().splitlines()
        self.assertEqual(len(log), 5)
        self.assertEqual(json.loads(log[0])["goals_at"], 3)

    def test_overlay_unknown_and_other_sports_untouched(self):
        self.assertFalse(bridge.overlay(self.state, "eng.1", "totals", "Under", 2.5, "Real Madrid", "Barcelona").found)
        self.assertFalse(bridge.overlay(self.state, "esp.1", "totals", "Under", 2.5, "Arsenal", "Brentford").found)

    def test_game_read(self):
        o = bridge.game_h2h(self.state, "soccer_epl", "Arsenal", "Brentford")
        self.assertTrue(o.found)
        self.assertNotIn("%", o.line().replace("no %", ""))

    def test_live_parse_and_total(self):
        self.assertEqual(SN.parse_live("⚽ MON 0–1 LAZ · 23'"), (1, 23.0))
        self.assertEqual(SN.parse_live("⚽ A 1-1 B · HT"), (2, 45.0))
        o, pu, u = SN.total_settle(1.0, 2.0)
        self.assertAlmostEqual(o + pu + u, 1.0, places=6)
        self.assertEqual(SN.total_settle(1.0, -0.5), (1.0, 0.0, 0.0))

    def test_grading_summary(self):
        ledger = {"k": {"league": "eng.1", "date": "2026-10-01", "home": "A", "away": "B", "p": [0.5, 0.3, 0.2],
                        "result": [2, 0], "pick": {"selection": "home", "price": 2.0, "win": True, "profit": 1.0}}}
        s = SN.summary(ledger, [{"lean": "for", "bet_won": True}])
        self.assertEqual(s["picks"]["n"], 1)
        self.assertEqual(s["overlay_for"]["n"], 1)


if __name__ == "__main__":
    unittest.main()
