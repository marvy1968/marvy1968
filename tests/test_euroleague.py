import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from marv.stats import euroleague as EL


def fake_box(season, gamecode):
    rows = []
    for home, team, pts in ((1, "RMB", 88 + gamecode % 7), (0, "OLY", 80 + gamecode % 5)):
        for player in ("A Player", "Team", "Total"):
            rows.append({"Season": season, "Gamecode": gamecode, "Home": home, "Team": team, "Player": player,
                         "Points": pts, "FieldGoalsMade2": 20, "FieldGoalsAttempted2": 38, "FieldGoalsMade3": 10,
                         "FieldGoalsAttempted3": 27, "FreeThrowsMade": 18, "FreeThrowsAttempted": 22,
                         "OffensiveRebounds": 9, "DefensiveRebounds": 25, "TotalRebounds": 34, "Assistances": 19,
                         "Steals": 6, "Turnovers": 12, "BlocksFavour": 3, "BlocksAgainst": 2, "FoulsCommited": 19,
                         "FoulsReceived": 21, "Valuation": 101})
    return pd.DataFrame(rows)


class FakeBoxScoreData:
    def __init__(self, comp):
        pass

    def get_players_boxscore_stats(self, season, gamecode):
        return fake_box(season, gamecode)


def fake_meta(season):
    n = 40
    return pd.DataFrame({"gameCode": range(1, n + 1), "Phase": "RS", "played": [True] * (n - 2) + [False] * 2,
                         "hometeam": ["REAL MADRID" if i % 2 else "OLYMPIACOS PIRAEUS" for i in range(n)],
                         "awayteam": ["OLYMPIACOS PIRAEUS" if i % 2 else "REAL MADRID" for i in range(n)],
                         "homescore": 90, "awayscore": 80,
                         "date_parsed": pd.date_range(f"{season}-10-01", periods=n, freq="7D")})


class EuroLeagueTest(unittest.TestCase):
    def test_box_mapping_and_load(self):
        pkg = types.ModuleType("euroleague_api")
        bx = types.ModuleType("euroleague_api.boxscore_data")
        bx.BoxScoreData = FakeBoxScoreData
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.dict(sys.modules, {"euroleague_api": pkg, "euroleague_api.boxscore_data": bx}), \
             mock.patch.object(EL, "season_meta", fake_meta):
            tot = EL.game_totals(2024, 3, Path(d))
            self.assertEqual(list(tot["team_home_away"]), ["home", "away"])
            self.assertEqual(tot["field_goals_attempted"].iloc[0], 65)  # 38 two-pointers + 27 threes
            games, tg = EL.EUROLEAGUE.load(Path(d), [2024])
        self.assertEqual(len(games), 38)  # only played games
        self.assertIn("efg", tg.columns)
        self.assertTrue(tg["oreb_pct"].between(0, 1).all())
        self.assertEqual(set(tg["team"]), {"REAL MADRID", "OLYMPIACOS PIRAEUS"})


if __name__ == "__main__":
    unittest.main()
