import unittest

import numpy as np
import pandas as pd

from marv import propgrade


def players(values, name="Test Receiver"):
    n = len(values)
    return pd.DataFrame({"player_display_name": name, "position": "WR", "season": 2026, "week": np.arange(n, 0, -1),
                         "targets": 8, "attempts": 0, "carries": 0, "receptions": 5,
                         "receiving_yards": values, "passing_yards": 0, "rushing_yards": 0})


class PropGradeTests(unittest.TestCase):
    def test_over_and_under_are_complementary_and_consistent(self):
        df = players([100, 95, 90, 110, 85, 105, 98, 92, 88, 101])
        over = propgrade.grade_prop(df, "Test Receiver", "rec", "over", 71.5, -170, 130)
        under = propgrade.grade_prop(df, "Test Receiver", "rec", "under", 71.5, 140, -170)
        self.assertTrue(over["ok"])
        self.assertAlmostEqual(over["p"] + under["p"], 1.0, places=3)
        self.assertGreater(over["p"], 0.9)  # he never goes under 85
        self.assertEqual(over["grade"], "B")  # price -170 (63%) vs Marv 95%+: capped at B
        self.assertEqual(under["grade"], "F")
        # a higher line can't be more likely to go over
        hi = propgrade.grade_prop(df, "Test Receiver", "rec", "over", 120.5, 150)
        self.assertLess(hi["p"], over["p"])

    def test_whole_number_line_and_small_samples(self):
        df = players([10, 10, 10, 10, 10, 10])
        self.assertLess(propgrade.grade_prop(df, "Test Receiver", "rec", "over", 10, -110)["p"], 0.5)  # 10 is a push/not over
        few = propgrade.grade_prop(players([50, 60, 70]), "Test Receiver", "rec", "over", 55.5, -110)
        self.assertFalse(few["ok"])
        self.assertFalse(propgrade.grade_prop(df, "Nobody Here", "rec", "over", 5.5, -110)["ok"])
        self.assertFalse(propgrade.grade_prop(df, "Test Receiver", "bogus", "over", 5.5, -110)["ok"])

    def test_parse_command(self):
        kw = propgrade.parse_command("CeeDee Lamb rec over 71.5 -170 130".split())
        self.assertEqual(kw, {"player": "CeeDee Lamb", "market": "rec", "side": "over", "line": 71.5, "price": -170.0,
                              "other_price": 130.0})
        self.assertIsNone(propgrade.parse_command("Lamb nothing".split()))
        self.assertIn("Grade", propgrade.text(propgrade.grade_prop(players([90] * 8), "Test Receiver", "rec", "under", 80.5, -110)))


if __name__ == "__main__":
    unittest.main()
