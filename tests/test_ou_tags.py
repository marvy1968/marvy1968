import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from marv import linemove, ou_tags
from marv.models import Game, Odds


def form(overs, graded=3, base_total=50.0, d_yds=0.0):
    return {"overs": overs, "graded": graded, "base_total": base_total, "d_yds": d_yds}


class OUTagTests(unittest.TestCase):
    def test_college_fade_tags(self):
        tags = dict(ou_tags.tags_for_game("cfb", form(2), form(3), 55.0, 52.0, 0.07))
        self.assertEqual(tags["OVER-FADE"], "Under")
        self.assertEqual(tags["OVER-FADE+MOVE"], "Under")  # total rose 3 from the open
        self.assertEqual(tags["OVER-FADE+INFLATED"], "Under")  # 5 above the teams' usual totals, yardage flat
        self.assertEqual(dict(ou_tags.tags_for_game("cfb", form(0), form(1), 48.0, 48.0, 0.07)), {"UNDER-FADE": "Over"})
        self.assertEqual(ou_tags.tags_for_game("cfb", form(2), form(1), 50.0, None, 0.07), [])
        self.assertEqual(ou_tags.tags_for_game("cfb", None, form(3), 50.0, None, 0.07), [])

    def test_nfl_inflated_total_needs_the_yardage_to_lag(self):
        self.assertEqual(ou_tags.tags_for_game("nfl", form(1, base_total=44), form(1, base_total=44), 50.0, None, 0.063),
                         [("TOTAL-INFLATED", "Under")])
        # same total but both teams gained 100 more yards a game lately: the move is justified
        self.assertEqual(ou_tags.tags_for_game("nfl", form(1, base_total=44, d_yds=100), form(1, base_total=44, d_yds=100),
                                               50.0, None, 0.063), [])

    def test_team_form_uses_only_earlier_games_this_season(self):
        rows = []
        for i, (season, pts, total) in enumerate([(2023, 50, 45), (2024, 60, 50), (2024, 40, 50), (2024, 61, 50), (2024, 70, 52), (2024, 10, 40)]):
            rows.append({"game_id": str(i), "team": "A", "date": pd.Timestamp(f"{season}-09-01") + pd.Timedelta(days=7 * i),
                         "season": season, "total": total, "g_pts": pts, "g_yds": 700.0, "over": 1 if pts > total else -1})
        hist = pd.DataFrame(rows)
        f = ou_tags.team_form(hist, "A", 2024, pd.Timestamp("2024-10-01"))
        self.assertEqual(f["overs"], 2)  # games 2, 3, 4 (game 5 is later)
        self.assertAlmostEqual(f["base_total"], (45 * 4 + 50 * 1) / 5)  # last season x4 + the game before the last 3

    def test_log_and_grade_against_the_last_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            start = datetime.now(timezone.utc) + timedelta(days=1)
            g = Game("g1", "cfb", start, "Home U", "Away St", odds=Odds(total=55.0, total_open=52.0))
            ou_tags.log(state, "cfb", [g], {"g1": [("OVER-FADE", "Under")]})
            g.odds.total = 56.0  # later run before kickoff
            ou_tags.log(state, "cfb", [g], {"g1": [("OVER-FADE", "Under")]})
            final = Game("g1", "cfb", start, "Home U", "Away St", completed=True, home_score=30, away_score=25)
            self.assertEqual(ou_tags.grade(state, "cfb", [final]), {"cfb OVER-FADE": [1, 0]})  # 55 < 56
            self.assertIn("cfb OVER-FADE: 1-0", ou_tags.report(ou_tags.record(state)))

    def test_line_move_note(self):
        g = Game("x", "nfl", datetime.now(timezone.utc), "Lions", "Bears", odds=Odds(spread=-4.5, spread_open=-3, total=51, total_open=47.5))
        self.assertEqual(linemove.note(g), "line move: spread Lions -3 → -4.5 (toward Lions), total 47.5 → 51 (+3.5)")
        g.odds.spread_open, g.odds.total_open = -4.5, 51
        self.assertEqual(linemove.note(g), "")


if __name__ == "__main__":
    unittest.main()


class InjuryReportTests(unittest.TestCase):
    def test_report_stars_key_players_and_filters_teams(self):
        from unittest import mock
        from marv.data import injuries
        def fake_espn(sport, reports):
            injuries._add(reports, "Dallas Cowboys", "Dak Prescott", "Questionable", "ESPN")
            injuries._add(reports, "Dallas Cowboys", "Backup Guy", "Out", "ESPN")
            injuries._add(reports, "Tampa Bay Buccaneers", "Someone", "Out", "ESPN")
            return True
        with mock.patch.object(injuries, "espn_injuries", fake_espn), \
                mock.patch.object(injuries, "nfl_starting_qbs", lambda c, s: {"dallas cowboys": {"Dak Prescott"}}):
            text = injuries.report_text("nfl", Path("."), 2026, ["Cowboys"], None)
        self.assertIn("★ Dak Prescott: questionable", text)
        self.assertIn("Backup Guy: out", text)
        self.assertNotIn("Tampa", text)
        self.assertIn("no injury feed", injuries.report_text("cfb", Path("."), 2026).lower())
