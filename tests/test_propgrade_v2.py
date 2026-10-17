import unittest

from marv import propgrade as PG


class PropGradeV2Tests(unittest.TestCase):
    def test_model_file_and_flag_default_off(self):
        m = PG.grade_model()
        self.assertEqual(len(m["feats"]), len(m["coef"]))
        self.assertFalse(PG.GRADE_LIVE)          # shadow only unless MARV_PROPGRADE_LIVE=1

    def test_grade_values_and_ev(self):
        g = PG.prop_grade("under", -110, 0.5, pm_over=0.40, pf_over=0.42, def_ratio=0.9, usage_trend=-0.1, implied_pts=20)
        self.assertIn(g["grade"], ("up", "neutral", "down"))
        self.assertAlmostEqual(g["ev"], g["p"] * (1 + 100 / 110) - 1, places=3)
        bad = PG.prop_grade("over", -110, 0.5, pm_over=0.30, pf_over=0.30)
        self.assertEqual(bad["grade"], "down")

    def test_up_needs_player_level_support(self):
        # a great price alone (market prior) is not a thumbs-up when projection and form are both against the side
        g = PG.prop_grade("under", 160, 0.55, pm_over=0.5, pf_over=0.5)
        self.assertNotEqual(g["grade"], "up")
        self.assertIn("no player-level support", g["why"])

    def test_script_nudge_is_capped(self):
        base = PG.prop_grade("over", -110, 0.5, 0.55, 0.55)
        hot = PG.prop_grade("over", -110, 0.5, 0.55, 0.55, script={"grade": "High Shootout", "prop_shift_over": 0.5})
        self.assertAlmostEqual(hot["p"] - base["p"], PG.PROP_CAP, places=3)
        grind = PG.prop_grade("under", -110, 0.5, 0.45, 0.45, script={"grade": "Def Grind", "prop_shift_over": -0.03})
        self.assertGreater(grind["script_shift"], 0)   # grind leans under

    def test_same_game_over_cap(self):
        items = [{"game": "G", "side": "over", "grade": "up", "ev": e} for e in (0.1, 0.08, 0.05)] + \
                [{"game": "G", "side": "under", "grade": "up", "ev": 0.04}]
        PG.cap_same_game_overs(items)
        self.assertEqual(sum(i["grade"] == "up" and i["side"] == "over" for i in items), 2)
        self.assertEqual(items[2]["grade"], "neutral")
        self.assertEqual(items[3]["grade"], "up")


if __name__ == "__main__":
    unittest.main()
