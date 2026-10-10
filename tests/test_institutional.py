"""Institutional hybrid simulation (NCAAF): tiered BCR, velocity-decayed steam, skew-normal MC, pick-engine wiring."""
import json
import tempfile
import unittest
from pathlib import Path

from marv import institutional as IN

P = {"B": 1.5, "S": 1.0, "ST": 2.0, "skew_m": [0.5, -6.0, 17.0], "skew_t": [1.5, -15.0, 22.0]}


class InstitutionalTest(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual([IN.bcr_tier(x) for x in (0.0, 0.2, 0.5, 0.7, 0.9)], [0, 1, 2, 3, 3])
        self.assertIsNone(IN.bcr_tier(None))
        self.assertIsNone(IN.bcr_tier(float("nan")))

    def test_steam_velocity_decay(self):
        self.assertEqual(IN.steam(-3.0, -3.0), 0.0)
        self.assertAlmostEqual(IN.steam(-3.0, -6.0), -1.5)          # 3-pt move counts 1.5
        self.assertLess(abs(IN.steam(-3.0, -13.0)), 10 / 2)        # big moves saturate
        self.assertEqual(IN.steam(0.0, 20.0), 0.0)                 # beyond cap = data error
        self.assertEqual(IN.steam(None, -3.0), 0.0)

    def test_adjustments_direction(self):
        base = IN.institutional_hybrid_simulation(3.0, 50.0, 0.5, 0.5, -3.0, 50.0, -3.0, 50.0, P, n=4000)
        steam_home = IN.institutional_hybrid_simulation(3.0, 50.0, 0.5, 0.5, -6.0, 52.0, -3.0, 50.0, P, n=4000)
        bcr_home = IN.institutional_hybrid_simulation(3.0, 50.0, 0.8, 0.1, -3.0, 50.0, -3.0, 50.0, P, n=4000)
        self.assertEqual(base["margin"], 3.0)
        self.assertGreater(steam_home["margin"], base["margin"])
        self.assertGreater(steam_home["total"], base["total"])
        self.assertAlmostEqual(bcr_home["margin"], 3.0 + 1.5 * 3, places=1)
        self.assertGreater(bcr_home["sim"]["p_home"], base["sim"]["p_home"])
        for k in ("p_home", "p_home_cover", "p_over"):
            self.assertTrue(0 <= base["sim"][k] <= 1)

    def test_skew_oriented_to_favourite(self):
        a = IN.simulate(10.0, 50.0, P, n=20000, seed=1)["p_home"]
        b = IN.simulate(-10.0, 50.0, P, n=20000, seed=1)["p_home"]
        self.assertAlmostEqual(a, 1 - b, delta=0.02)

    def test_bcr_guard(self):
        self.assertEqual(IN.bcr_guard(-14.0, 3, 1), "home")
        self.assertEqual(IN.bcr_guard(10.0, 0, 3), "away")
        self.assertIsNone(IN.bcr_guard(-3.0, 3, 0))
        self.assertIsNone(IN.bcr_guard(-14.0, 2, 0))

    def test_opener_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            st = Path(d)
            (st / "lines.json").write_text(json.dumps({"cfb:1": {"home": "Texas", "away": "Oklahoma", "spread": -4.0,
                                                                  "spread_open_src": -3.5, "total": 55.0}}))
            self.assertEqual(IN.opener(st, "Texas", "Oklahoma"), (-3.5, 55.0))
            self.assertEqual(IN.opener(st, "Ohio State", "Michigan"), (None, None))
            (st / "reports").mkdir()
            (st / "reports" / "institutional_backtest.json").write_text(json.dumps({"latest_fit": P}))
            IN._LOADED["t"] = 0.0
            self.assertEqual(IN.load(st)["B"], 1.5)

    def test_text_has_no_percent(self):
        r = IN.institutional_hybrid_simulation(7.0, 50.0, 0.8, 0.1, -14.0, 50.0, -12.0, 49.0, P, n=2000)
        t = IN.text(r, "Georgia", "Kent State")
        self.assertNotIn("%", t)
        self.assertIn("paper", t)


    def test_pick_engine_text_and_fallback(self):
        from marv import pick_engine as PE
        r0 = IN.institutional_hybrid_simulation(7.0, 50.0, 0.8, 0.1, -14.0, 50.0, -12.0, 49.0, P, n=2000)
        d = PE.decide(r0["sim"], r0["margin"], r0["total"], -14.0, 50.0)
        r = {"home": "Georgia", "away": "Kent State", "power_margin": 6.0, "power_total": 49.0, "trend_margin": 8.0,
             "trend_total": 51.0, "margin": r0["margin"], "total": r0["total"], "spread": -14.0, "line": 50.0,
             "sim": r0["sim"], **d, "ml_agree": {"power": True, "trend": True, "mc": True}, "institutional": r0}
        t = PE.text(r, "cfb")
        self.assertIn("institutional skew-MC", t)
        self.assertIn("BCR guard Georgia", t)
        self.assertNotIn("%", t)
        with tempfile.TemporaryDirectory() as d:
            # no lines / BCR files: still a read (no tiers, no steam), never raises
            x = PE._institutional(Path(d), "A", "B", 3.0, 50.0, -3.0, 50.0, None)
            self.assertIsNotNone(x)
            self.assertEqual(x["tiers"], (None, None))


if __name__ == "__main__":
    unittest.main()
