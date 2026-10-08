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
                mock.patch.object(injuries, "nfl_official", lambda *a: False), \
                mock.patch.object(injuries, "nfl_starting_qbs", lambda c, s: {"dallas cowboys": {"Dak Prescott"}}):
            text = injuries.report_text("nfl", Path("."), 2026, ["Cowboys"], None)
        self.assertIn("★ Dak Prescott: questionable", text)
        self.assertIn("Backup Guy: out", text)
        self.assertNotIn("Tampa", text)
        self.assertIn("no injury feed", injuries.report_text("cfb", Path("."), 2026).lower())


class RosterNoteTests(unittest.TestCase):
    def test_nfl_roster_line_flags_eliminations(self):
        from marv.roster_notes import nfl_note
        tg = pd.DataFrame({"game_id": ["g", "g"], "team": ["DAL", "TB"], "my_starters_out": [2, 3],
                           "my_snaps_lost": [1.4, 2.5], "my_qb_out": [0, 1]})
        note = nfl_note(tg, "g", "DAL", "Dallas Cowboys", "Tampa Bay Buccaneers")
        self.assertIn("Dallas Cowboys 2 starters out (1.4 full-time)", note)
        self.assertIn("Tampa Bay Buccaneers 3 starters out (2.5 full-time) + QB OUT ⚠️ elimination", note)
        self.assertNotIn("1.4 full-time) ⚠️", note)

    def test_college_qb_change_from_play_by_play(self):
        from marv.roster_notes import cfb_note, cfb_qb_changes
        games = pd.DataFrame({"game_id": ["1", "2", "3"], "date": pd.to_datetime(["2026-09-06", "2026-09-13", "2026-09-20"])})
        players = pd.DataFrame({"game_id": ["1", "2", "3", "3"], "team": ["Alabama"] * 4,
                                "player": ["Starter", "Starter", "Starter", "Backup"], "attempts": [30, 32, 4, 25]})
        ch = cfb_qb_changes(players, games)
        self.assertIn("Backup threw most last game, season leader Starter", ch["Alabama"])
        self.assertIn("QB change", cfb_note(ch, "Alabama", "Georgia", "Alabama Crimson Tide", "Georgia Bulldogs"))

    def test_recent_results_fill_the_trend(self):
        hist = ou_tags.team_games(
            pd.DataFrame({"game_id": ["1"], "date": [pd.Timestamp("2026-09-06")], "season": [2026], "total": [50.0]}),
            pd.DataFrame({"game_id": ["1", "1"], "team": ["Alabama", "Georgia"], "opp": ["Georgia", "Alabama"],
                          "points": [30.0, 28.0], "yards": [400.0, 380.0]}))
        games = pd.DataFrame({"game_id": ["1"], "date": [pd.Timestamp("2026-09-06")], "season": [2026],
                              "home": ["Alabama"], "away": ["Georgia"]})
        g = Game("99", "cfb", datetime(2026, 10, 4, tzinfo=timezone.utc), "Alabama Crimson Tide", "Auburn Tigers",
                 completed=True, home_score=40, away_score=21, odds=Odds(total=52.5))
        out = ou_tags.add_recent(hist, games, [g])
        added = out[out["game_id"] == "99"]
        self.assertEqual(list(added["team"]), ["Alabama"])  # Auburn isn't in the tables: skipped
        self.assertEqual(float(added["over"].iloc[0]), 1.0)  # 61 > 52.5


class BackupQBTests(unittest.TestCase):
    def test_qb_starts_counts_any_team_and_latest_starter(self):
        import tempfile
        from unittest import mock
        from marv.data import injuries
        rows = [("Vet", "QB", "MIN", 1, 30), ("Vet", "QB", "MIN", 2, 31), ("Kid", "QB", "TB", 1, 5),
                ("Star", "QB", "TB", 1, 35), ("Star", "QB", "TB", 2, 33), ("Kid", "QB", "TB", 3, 28)]
        last = [("Vet", "QB", "ARI", w, 30) for w in range(1, 6)]
        with tempfile.TemporaryDirectory() as d:
            for yr, data in ((2026, rows), (2025, last)):
                pd.DataFrame(data, columns=["player_display_name", "position", "team", "week", "attempts"]) \
                    .to_csv(Path(d) / f"nfl_players_week_{yr}.csv", index=False)
            with mock.patch.object(injuries, "fetch", lambda url, path, max_age_hours=None: path):
                out = injuries.nfl_qb_starts(Path(d), 2026)
        self.assertEqual(out["tampa bay buccaneers"], ("Kid", 1))  # backup started the latest game
        self.assertEqual(out["minnesota vikings"], ("Vet", 7))  # veteran on a new team keeps his starts
