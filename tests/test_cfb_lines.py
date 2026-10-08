import unittest

import pandas as pd

from marv.data import cfb_lines


def rows(game, home_id, away_id, home, away, book, h_line, h_open, total, t_open, odds=-110):
    base = {"game_id": game, "season": 2023.0, "week": 5.0, "game_desc": f"{away}@{home}", "book": book,
            "home_team_id": home_id, "away_team_id": away_id}
    return [
        {**base, "market_type": "spread", "abbr": home, "lines": h_line, "opening_lines": h_open, "odds": odds},
        {**base, "market_type": "spread", "abbr": away, "lines": -h_line, "opening_lines": None if h_open is None else -h_open, "odds": odds},
        {**base, "market_type": "total", "abbr": "over", "lines": total, "opening_lines": t_open, "odds": odds},
        {**base, "market_type": "total", "abbr": "under", "lines": total, "opening_lines": t_open, "odds": odds},
    ]


class CfbLinesTests(unittest.TestCase):
    def test_home_perspective_open_close_and_books(self):
        d = pd.DataFrame(
            rows(1.0, 10, 20, "AAA", "BBB", "Bovada", -7.0, -3.0, 50.0, 47.0, -115)
            + rows(1.0, 10, 20, "AAA", "BBB", "PINNACLE", -6.5, None, 49.5, None)
            + rows(1.0, 10, 20, "AAA", "BBB", "DraftKings", -7.5, None, 51.0, None)
            + rows(2.0, 20, 10, "BBB", "AAA", "Bovada", 3.0, 1.0, 40.0, 41.0)
            + rows(3.0, 30, 10, "CCC", "AAA", "Bovada", 1.0, 1.0, 40.0, 41.0)
            + rows(4.0, 20, 40, "BBB", "DDD", "Bovada", 1.0, 1.0, 40.0, 41.0))
        t = cfb_lines.games_table(d)
        g = t.loc["1"]
        self.assertEqual(g["spread_open"], -3.0)
        self.assertEqual(g["spread_close"], -7.0)  # median of Bovado, Pinnacle, DraftKings
        self.assertEqual(g["spread_sharp"], -6.5)
        self.assertEqual(g["spread_move"], -4.0)  # moved toward the home team
        self.assertEqual(g["total_move"], 3.0)
        self.assertEqual(g["bov_home_odds"], -115)
        self.assertEqual(t.loc["2", "spread_close"], 3.0)  # BBB is home in game 2


if __name__ == "__main__":
    unittest.main()
