"""Run: python3 -m unittest test_blind_backtest -v   (synthetic seasons, no network)"""
import unittest
import numpy as np
import pandas as pd
import blind_backtest as bb
import test_power_ranks as tp


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        G, teams, champs, coaches, truth = tp.synth(seasons=(2023, 2024, 2025, 2026), confs=6, per=14, seed=21)
        cls.G, cls.teams = G, teams
        cls.o = bb.run(G, 2026, top=10, teams=teams)

    def test_runs_and_reports(self):
        self.assertNotIn("error", self.o)
        s = bb.format_report(self.o)
        self.assertIn("BLIND BACKTEST", s); self.assertIn("Every top-vs-top matchup", s)

    def test_top_vs_top_games_really_have_both_teams_in_pregame_top_ten(self):
        m = self.o["matchups"]
        self.assertTrue(((m["rank_h"] <= 10) & (m["rank_a"] <= 10)).all())
        self.assertGreater(len(m), 0)

    def test_blind_no_future_leakage(self):
        """Corrupt every 2026 result from a cutoff on: predictions for games on or before the cutoff do not move."""
        cut = pd.Timestamp(2026, 10, 15)
        bad = self.G.copy()
        late = pd.to_datetime(bad["date"]) >= cut
        bad.loc[late, ["hg", "ag"]] = [60, 0]
        a = bb.blind_predictions(self.G, 2026, self.teams)
        b = bb.blind_predictions(bad, 2026, self.teams)
        ea, eb = a[a["date"] <= cut].reset_index(drop=True), b[b["date"] <= cut].reset_index(drop=True)
        pd.testing.assert_frame_equal(ea[["margin_pred", "rank_h", "rank_a"]], eb[["margin_pred", "rank_h", "rank_a"]])

    def test_scale_is_fitted_only_on_the_prior_season(self):
        bad = self.G.copy()
        m = bad["season"] == 2026
        hg, ag = bad.loc[m, "hg"].copy(), bad.loc[m, "ag"].copy()
        bad.loc[m, "hg"], bad.loc[m, "ag"] = ag, hg  # flip 2026 outcomes
        self.assertAlmostEqual(bb.run(self.G, 2026, 10, self.teams)["scale"], bb.run(bad, 2026, 10, self.teams)["scale"])

    def test_predicted_close_to_actual_on_well_specified_data(self):
        s = self.o["all_games"]
        self.assertGreater(s["n"], 700)
        self.assertLess(abs(s["gap"]), 0.04)      # calibrated on thousands of games
        self.assertGreater(s["actual_fav_win"], 0.6)
        self.assertLess(s["brier"], 0.25)

    def test_wilson_interval_sane(self):
        lo, hi = bb.wilson(8, 10)
        self.assertLess(lo, 0.8); self.assertGreater(hi, 0.8); self.assertGreater(hi - lo, 0.3)

    def test_small_sample_warning(self):
        o = dict(self.o); o["top_vs_top"] = dict(o["top_vs_top"], n=12)
        self.assertIn("WARNING", bb.format_report(o))


if __name__ == "__main__":
    unittest.main()
