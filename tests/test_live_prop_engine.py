import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from marv import live_prop_engine as E

NOW = datetime(2026, 11, 8, 19, 0, tzinfo=timezone.utc)
FRESH = "2026-11-08T18:59:00Z"


def nfl_summary(rec_yds="40", rec="3", tgts="5", plays="30"):
    return {"boxscore": {
        "players": [{"team": {"displayName": "Dallas Cowboys"}, "statistics": [
            {"name": "passing", "labels": ["C/ATT", "YDS", "AVG", "TD", "INT"], "athletes": [
                {"athlete": {"displayName": "Dak Prescott"}, "stats": ["12/17", "151", "8.9", "1", "0"]}]},
            {"name": "receiving", "labels": ["REC", "YDS", "AVG", "TD", "LONG", "TGTS"], "athletes": [
                {"athlete": {"displayName": "CeeDee Lamb"}, "stats": [rec, rec_yds, "12.4", "0", "28", tgts]}]}]}],
        "teams": [{"team": {"displayName": "Dallas Cowboys"}, "statistics": [
            {"name": "totalOffensivePlays", "displayValue": plays},
            {"name": "completionAttempts", "displayValue": "12-17"}]},
            {"team": {"displayName": "New York Giants"}, "statistics": [
                {"name": "totalOffensivePlays", "displayValue": "28"}]}]}}


def odds(line=60.5, over=-115, under=-115, upd=FRESH, player="CeeDee Lamb", market="player_reception_yds"):
    return {"bookmakers": [{"key": "bovada", "markets": [{"key": market, "last_update": upd, "outcomes": [
        {"name": "Over", "description": player, "point": line, "price": over},
        {"name": "Under", "description": player, "point": line, "price": under}]}]}]}


class ParseTests(unittest.TestCase):
    def test_box_made_attempts_and_team_plays(self):
        box = E.parse_box(nfl_summary())
        dak = box["players"]["Dak Prescott"]["stats"]["passing"]
        self.assertEqual((dak["C/ATT"], dak["ATT"], dak["YDS"]), (12.0, 17.0, 151.0))
        self.assertEqual(E.team_plays("nfl", box["teams"]["Dallas Cowboys"]), 30.0)

    def test_basketball_possessions(self):
        t = {"fieldGoalsAttempted": 40, "freeThrowsAttempted": 10, "offensiveRebounds": 5, "totalTurnovers": 7}
        self.assertAlmostEqual(E.team_plays("nba", t), 46.4)

    def test_stale_and_one_sided_lines_dropped(self):
        self.assertEqual(len(E.bovada_lines(odds(), now=NOW)), 1)
        self.assertEqual(E.bovada_lines(odds(upd="2026-11-08T18:40:00Z"), now=NOW), [])
        one = odds()
        one["bookmakers"][0]["markets"][0]["outcomes"].pop()
        self.assertEqual(E.bovada_lines(one, now=NOW), [])
        self.assertEqual(E.bovada_lines({"bookmakers": [{**odds()["bookmakers"][0], "key": "fanduel"}]}, now=NOW), [])


class ProjectionTests(unittest.TestCase):
    def st(self, **kw):
        base = dict(sport="nfl", market="player_reception_yds", player="X", so_far=40.0, anchor=70.0,
                    elapsed=30.0, team_plays=31.0)
        base.update(kw)
        return E.PropState(**base)

    def test_faster_pace_projects_more(self):
        # same production per play; the faster game has more plays left to produce in
        slow, fast = E.project(self.st(team_plays=24, so_far=24.0)), E.project(self.st(team_plays=40, so_far=40.0))
        self.assertGreater(fast["rem_mean"], slow["rem_mean"])
        self.assertGreater(fast["pace"], 1.0)

    def test_trailing_team_passes_more_and_rout_rests_starters(self):
        trail = E.project(self.st(margin=-14))["rem_mean"]
        lead = E.project(self.st(margin=14))["rem_mean"]
        self.assertGreater(trail, lead)
        rush_trail = E.project(self.st(market="player_rush_yds", margin=-14))["rem_mean"]
        rush_lead = E.project(self.st(market="player_rush_yds", margin=14))["rem_mean"]
        self.assertLess(rush_trail, rush_lead)
        rout = E.project(self.st(margin=28, elapsed=48))
        self.assertLess(rout["script"], 0.7)

    def test_basketball_foul_out_and_blowout(self):
        b = dict(sport="nba", market="player_points", so_far=14, anchor=24, elapsed=36, team_plays=75)
        self.assertEqual(E.project(self.st(**b, fouls=6))["rem_mean"], 0.0)
        self.assertLess(E.project(self.st(**b, fouls=5))["rem_mean"], E.project(self.st(**b))["rem_mean"])
        self.assertLess(E.project(self.st(**b, margin=25))["rem_mean"], E.project(self.st(**b, margin=3))["rem_mean"])

    def test_p_over_monotone_and_bounded(self):
        hi = E.p_over("nfl", "player_reception_yds", 40, 30, 55.5)[0]
        lo = E.p_over("nfl", "player_reception_yds", 40, 30, 85.5)[0]
        self.assertGreater(hi, lo)
        self.assertEqual(E.p_over("nfl", "player_reception_yds", 60, 0, 55.5)[0], 1.0)
        self.assertEqual(E.p_over("nfl", "player_reception_yds", 40, 0, 55.5)[0], 0.0)
        # receptions: integer line has a push mass
        _, push = E.p_over("nfl", "player_receptions", 3, 2.5, 5)
        self.assertGreater(push, 0.1)

    def test_mean_of_simulation_matches_projection(self):
        import numpy as np
        rng = np.random.default_rng(1)
        spec = E.specs("nfl")["player_reception_yds"]
        lam = rng.gamma(spec.disp, 30 / spec.unit / spec.disp, 50000)
        n = rng.poisson(lam)
        shape = 1 / spec.cv ** 2
        tot = np.where(n > 0, rng.gamma(np.maximum(n, 1) * shape, spec.unit / shape), 0)
        self.assertAlmostEqual(float(tot.mean()), 30, delta=1.0)


class ScanTests(unittest.TestCase):
    def test_soft_line_recommended_fair_line_not(self):
        # Lamb: 40 yds on 5 targets at the half, pregame 70; Bovada hangs 55.5 -> clear Over
        priced, recs = E.scan_game("nfl", nfl_summary(), odds(line=55.5), {("ceedee lamb", "player_reception_yds"): 70.0},
                                   30.0, "Dallas Cowboys", "New York Giants", 14, 10, now=NOW)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["side"], "Over")
        self.assertGreater(r["edge"], 0.07)
        # a line at Marv's projection: no recommendation
        pr = priced[0]["proj"]
        _, recs2 = E.scan_game("nfl", nfl_summary(), odds(line=round(pr) + 0.5), {("ceedee lamb", "player_reception_yds"): 70.0},
                               30.0, "Dallas Cowboys", "New York Giants", 14, 10, now=NOW)
        self.assertEqual(recs2, [])

    def test_no_anchor_no_price_and_no_usage_blocked(self):
        priced, _ = E.scan_game("nfl", nfl_summary(), odds(line=55.5), {}, 30.0, "Dallas Cowboys", "New York Giants", 14, 10, now=NOW)
        self.assertEqual(priced, [])
        mem = {"_elapsed": 15.0, "ceedee lamb|player_reception_yds": 5.0}  # no new targets since the end of Q1
        _, recs = E.scan_game("nfl", nfl_summary(), odds(line=55.5), {("ceedee lamb", "player_reception_yds"): 70.0},
                              30.0, "Dallas Cowboys", "New York Giants", 14, 10, prev_usage=mem, now=NOW)
        self.assertEqual(recs, [])

    def test_gate_rejects_bad_prices_and_late(self):
        ev = {"side": "Over", "p": 0.7, "market_p": 0.5, "edge": 0.2, "ev": 0.3, "price": -250, "line": 50.5,
              "proj": 70, "left": 30, "so_far": 30}
        self.assertEqual(E.qualifies(ev, "nfl", "player_reception_yds"), (False, "price out of range"))
        self.assertEqual(E.qualifies({**ev, "price": -110, "left": 3}, "nfl", "player_reception_yds")[1], "too early/late")
        self.assertTrue(E.qualifies({**ev, "price": -110}, "nfl", "player_reception_yds")[0])
        with mock.patch.dict(os.environ, {"LIVE_PROP_MIN_EDGE": "0.25"}):
            self.assertEqual(E.qualifies({**ev, "price": -110}, "nfl", "player_reception_yds")[1], "edge")


class LedgerTests(unittest.TestCase):
    def test_record_dedupe_grade_and_record(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            rec = {"player": "CeeDee Lamb", "market": "player_reception_yds", "label": "Receiving yards", "side": "Over",
                   "line": 55.5, "price": -115, "p": 0.66, "market_p": 0.5, "edge": 0.16, "so_far": 40.0, "proj": 75.0}
            self.assertEqual(len(E.record_recs(d, "nfl", "g1", "NYG @ DAL", "end of Q2", [rec])), 1)
            self.assertEqual(E.record_recs(d, "nfl", "g1", "NYG @ DAL", "end of Q3", [rec]), [])  # never twice
            graded = E.grade_game(d, "nfl", "g1", nfl_summary(rec_yds="81"))
            self.assertEqual(graded[0]["result"], "win")
            r = E.record(d)
            self.assertEqual((r["wins"], r["losses"], r["proven"]), (1, 0, False))
            self.assertIn("1-0", E.final_text("NYG 10 – 24 DAL", graded, r))
            self.assertIn("all", E.report(d))

    def test_anchor_prefers_marv_projection(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            E.save_pregame(d, {"id": "ev1", "commence_time": "2026-11-08T18:00:00Z"},
                           [{"player": "CeeDee Lamb", "market": "player_reception_yds", "line": 72.5}])
            self.assertEqual(E.anchors(d, "nfl", "ev1")[("ceedee lamb", "player_reception_yds")], 72.5)
            (d / "props_priced_nfl.json").write_text('[{"player": "CeeDee Lamb", "market": "player_reception_yds", "proj": 66.0}]')
            self.assertEqual(E.anchors(d, "nfl", "ev1")[("ceedee lamb", "player_reception_yds")], 66.0)

    def test_alert_text_is_labelled_paper(self):
        r = {"player": "CeeDee Lamb", "side": "Over", "line": 55.5, "label": "Receiving yards", "price": -115,
             "so_far": 40.0, "proj": 75.0, "p": 0.66, "market_p": 0.5, "edge": 0.16, "pace": 1.05, "script": 1.0}
        t = E.alert_text("nfl", "NYG @ DAL", "end of Q2", "NYG 10 – 14 DAL", [r])
        self.assertIn("PAPER", t)
        self.assertIn("Over 55.5", t)


if __name__ == "__main__":
    unittest.main()
