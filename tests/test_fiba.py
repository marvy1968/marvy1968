import unittest

from marv.data import fiba

SAMPLE = {"period": 4, "clock": "00:00", "tm": {
    "1": {"name": "Fenerbahce", "score": 81, "tot_sPoints": 81, "tot_sFieldGoalsMade": 30, "tot_sFieldGoalsAttempted": 64,
          "tot_sThreePointersMade": 9, "tot_sThreePointersAttempted": 24, "tot_sFreeThrowsMade": 12,
          "tot_sFreeThrowsAttempted": 15, "tot_sReboundsOffensive": 10, "tot_sReboundsDefensive": 27,
          "tot_sTurnovers": 13, "tot_sAssists": 20},
    "2": {"name": "Valencia Basket", "score": 74, "tot_sPoints": 74, "tot_sFieldGoalsMade": 27, "tot_sFieldGoalsAttempted": 66,
          "tot_sThreePointersMade": 8, "tot_sThreePointersAttempted": 28, "tot_sFreeThrowsMade": 12,
          "tot_sFreeThrowsAttempted": 16, "tot_sReboundsOffensive": 11, "tot_sReboundsDefensive": 25,
          "tot_sTurnovers": 15, "tot_sAssists": 17}}}


class FibaTest(unittest.TestCase):
    def test_parse_and_final(self):
        rows = fiba.parse_totals(SAMPLE, 2411111)
        self.assertEqual([r["team_home_away"] for r in rows], ["home", "away"])
        self.assertEqual(rows[0]["field_goals_attempted"], 64)
        self.assertEqual(rows[1]["opponent_team_display_name"], "Fenerbahce")
        self.assertTrue(fiba.is_final(SAMPLE))
        self.assertFalse(fiba.is_final({**SAMPLE, "clock": "03:12"}))

    def test_match_ids_from_page(self):
        html = '<a href="https://fibalivestats.dcd.shared.geniussports.com/u/FIBASP/2411111/bs.html">x</a> {"gameId": "2411112"}'
        self.assertEqual(fiba.match_ids(html), [2411111, 2411112])


if __name__ == "__main__":
    unittest.main()
