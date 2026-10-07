import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pandas as pd

from marv.data import injuries as I
from marv.data.espn_box import parse_summary
from marv.data.nba_injury_pdf import candidate_urls, parse_report
from marv.models import Game
from marv.stats.live import freshness, missing_game_ids
from marv.stats.nba import NBA

SUMMARY = {
    "header": {"season": {"year": 2026, "type": 2},
               "competitions": [{"date": "2026-01-10T00:30Z", "competitors": [
                   {"team": {"id": "2"}, "homeAway": "home", "score": "112"},
                   {"team": {"id": "13"}, "homeAway": "away", "score": "104"}]}]},
    "boxscore": {"teams": [
        {"team": {"id": "2", "displayName": "Boston Celtics"}, "homeAway": "home", "statistics": [
            {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "42-88"},
            {"name": "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "displayValue": "15-40"},
            {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "13-16"},
            {"name": "offensiveRebounds", "displayValue": "10"}, {"name": "defensiveRebounds", "displayValue": "35"},
            {"name": "totalTurnovers", "displayValue": "12"}]},
        {"team": {"id": "13", "displayName": "Los Angeles Lakers"}, "homeAway": "away", "statistics": [
            {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "39-90"},
            {"name": "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "displayValue": "11-35"},
            {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "15-20"},
            {"name": "offensiveRebounds", "displayValue": "12"}, {"name": "defensiveRebounds", "displayValue": "30"},
            {"name": "totalTurnovers", "displayValue": "14"}]}]},
}


class ScrapeTest(unittest.TestCase):
    def test_espn_summary_matches_box_columns(self):
        rows = parse_summary(SUMMARY, "401")
        self.assertEqual(len(rows), 2)
        h = rows[0]
        self.assertEqual((h["field_goals_made"], h["field_goals_attempted"]), (42, 88))
        self.assertEqual(h["three_point_field_goals_made"], 15)
        self.assertEqual(h["total_turnovers"], 12)
        self.assertEqual((h["team_score"], h["opponent_team_display_name"]), (112, "Los Angeles Lakers"))

    def test_nba_pdf_parsing(self):
        text = ("Game Date Game Time Matchup Team Player Name Current Status Reason\n"
                "01/10/2026 07:30 (ET) LAL@BOS Boston Celtics Tatum, Jayson Questionable Injury/Illness - Ankle\n"
                "Brown, Jaylen Out Injury/Illness - Knee\n"
                "Los Angeles Lakers James, LeBron Probable Injury/Illness - Rest\n")
        rep = parse_report(text, ["Boston Celtics", "Los Angeles Lakers"])
        self.assertIn(("Jayson Tatum", "questionable"), rep["boston celtics"])
        self.assertIn(("Jaylen Brown", "out"), rep["boston celtics"])
        self.assertIn(("LeBron James", "probable"), rep["los angeles lakers"])
        urls = candidate_urls(datetime(2026, 1, 10, 18, 7, tzinfo=timezone.utc))
        self.assertTrue(urls[0].endswith("2026-01-10_01_00PM.pdf") or urls[0].endswith("2026-01-10_01PM.pdf"))

    def test_injury_merge_and_blind_veto(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            with mock.patch.object(I, "espn_injuries", return_value=False), \
                 mock.patch.object(I, "nba_official", return_value=False):
                v = I.injury_vetoes("nba", cache, 2026, ["Boston Celtics"])
            self.assertIn("no current injury data", v["Boston Celtics"][0])

            def espn(sport, reports):
                I._add(reports, "Boston Celtics", "Jayson Tatum", "Questionable", "ESPN")
                I._add(reports, "Boston Celtics", "Bench Guy", "Out", "ESPN")
                return True
            with mock.patch.object(I, "espn_injuries", side_effect=espn), \
                 mock.patch.object(I, "nba_official", return_value=False), \
                 mock.patch.object(I, "basketball_key_players", return_value={"boston celtics": {"Jayson Tatum"}}):
                v = I.injury_vetoes("nba", cache, 2026, ["Boston Celtics", "Miami Heat"])
            self.assertEqual(list(v), ["Boston Celtics"])
            self.assertIn("Jayson Tatum (questionable, ESPN)", v["Boston Celtics"][0])


class FreshnessTest(unittest.TestCase):
    def test_missing_games_and_stale_veto(self):
        tg = pd.DataFrame({"game_id": ["1", "1"], "date": pd.to_datetime(["2026-01-05"] * 2), "season": 2026,
                           "team": ["Boston Celtics", "Miami Heat"], "opp": ["Miami Heat", "Boston Celtics"],
                           "home": [1.0, 0.0], "points": [100.0, 90.0], "field_goals_made": [40.0, 35.0]})
        hist = [Game("1", "nba", datetime(2026, 1, 5, 23, tzinfo=timezone.utc), "Boston Celtics", "Miami Heat", completed=True),
                Game("2", "nba", datetime(2026, 1, 8, 23, tzinfo=timezone.utc), "Boston Celtics", "Orlando Magic", completed=True)]
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        with mock.patch.object(type(NBA), "stat_columns", return_value=["points", "field_goals_made"]):
            self.assertEqual(missing_game_ids(tg, hist, NBA, now), ["2"])
            v = freshness(NBA, tg, hist, ["Boston Celtics", "Miami Heat"], now)
        self.assertIn("Boston Celtics", v)
        self.assertNotIn("Miami Heat", v)


if __name__ == "__main__":
    unittest.main()
