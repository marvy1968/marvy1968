import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location("dog_lanes", Path(__file__).resolve().parents[1] / "tools/dog_lanes/dog_lanes.py")
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


def lane(name, n, rng, home_edge=0.0, away_edge=0.0, model_bias=0.08, start="2026-01-01"):
    """Dog bets: price = true prob - edge (edge > 0 = the dog is priced too cheap); model overrates by model_bias."""
    rows = []
    t0 = pd.Timestamp(start, tz="UTC")
    for i in range(n):
        home = i % 2
        price = rng.uniform(0.25, 0.45)
        true = np.clip(price + (home_edge if home else away_edge), 0.02, 0.98)
        rows.append({"lane": name, "venue": "polymarket" if i % 3 else "kalshi", "sport": "nba", "game": f"{name}-{i}",
                     "side": "dog", "start": (t0 + pd.Timedelta(hours=12 * i)).isoformat(), "price": round(price, 3),
                     "model_p": min(true + model_bias, 0.95), "is_home": home, "spread": rng.uniform(1, 12),
                     "result": int(rng.random() < true), "stake": 10.0})
    return rows


class DogLaneTests(unittest.TestCase):
    def test_clean_regrade_dedupes_and_charges_fees(self):
        rows = lane("a", 4, np.random.default_rng(0))
        rows.append(dict(rows[0], start=rows[0]["start"]))  # same bet logged twice
        rows.append(dict(rows[1], game="a-void", result=None))  # a void bet on another game
        d = D.clean(pd.DataFrame(rows).pipe(lambda x: D.load_frame(x)), D.FEES)
        self.assertEqual(len(d), 4)  # 4 real bets: the duplicate and the void are gone
        self.assertEqual(d.attrs["dupes_removed"], 2)
        one = d.iloc[0]
        x = D.pnl(d.iloc[[0]])
        fee = one["fee_rate"] * one["price"] * (1 - one["price"])
        self.assertAlmostEqual(x["cost"].iloc[0], (one["price"] + fee) * one["contracts"])
        self.assertEqual(D.FEES, {"kalshi": 0.07, "polymarket": 0.095})

    def test_longshot_bias_shows_in_calibration(self):
        d = D.clean(D.load_frame(pd.DataFrame(lane("b", 600, np.random.default_rng(1)))), D.FEES)
        cal = D.calibration(d)
        self.assertTrue(all(c["model_minus_won"] > 0.03 for c in cal if c["n"] > 50))

    def test_fixable_lane_is_repaired_by_home_dogs_and_unfixable_stays_off(self):
        rng = np.random.default_rng(2)
        rows = lane("fixable", 800, rng, home_edge=0.12, away_edge=-0.08) + lane("hopeless", 800, rng)
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "bets.csv"
            pd.DataFrame(rows).to_csv(p, index=False)
            rep = D.run(str(p), t)
            fx = rep["lanes"]["fixable"]
            self.assertIn("REPAIRED", fx["verdict"])
            chosen = next(r for r in fx["fixes"] if r["fix"] == fx["chosen"])
            d = D.clean(D.load_frame(pd.DataFrame([r for r in rows if r["lane"] == "fixable"])), D.FEES)
            picked = D.apply(d, chosen["rule"])
            self.assertGreater(picked["is_home"].mean(), 0.8)  # whatever the rule is called, it finds the home dogs
            self.assertTrue(rep["lanes"]["hopeless"]["verdict"].startswith("OFF"))
            self.assertLess(rep["lanes"]["hopeless"]["clean"]["pnl"], 0)
            self.assertTrue((Path(t) / "dog_lanes.md").exists())

    def test_thin_lane_is_never_repaired(self):
        rows = lane("thin", 40, np.random.default_rng(3), home_edge=0.3, away_edge=0.3)
        r = D.evaluate_lane(D.clean(D.load_frame(pd.DataFrame(rows)), D.FEES), 0.5)
        self.assertTrue(r["verdict"].startswith("OFF"))

    def test_favourite_comparison_flips_side(self):
        rows = lane("f", 10, np.random.default_rng(4))
        for r in rows:
            r["fav_price"] = round(1 - r["price"] + 0.03, 3)
        d = D.clean(D.load_frame(pd.DataFrame(rows)), D.FEES)
        fav = D.apply(d, {"fav": True})
        self.assertTrue(((fav["result"] + d.loc[fav.index, "result"]) == 1).all())
        self.assertTrue((fav["price"] > 0.5).all())


if __name__ == "__main__":
    unittest.main()
