import tempfile
import unittest
from pathlib import Path

import pandas as pd

from marv.ratings import RatingParams
from marv.stats import backtest as B
from marv.stats.base import StatsModule
from marv.stats.experts import ExpertConfig
from tests.test_stats import toy_league


class ToyModule(StatsModule):
    def load(self, cache, seasons, current=None):
        return toy_league(days=70)


def make():
    m = ToyModule(key="toy", name="Toy", simulate=lambda h, a, n, rng: None,
                  rating_params=RatingParams(multiplicative=False, home_adv=1.0), halflife=5, chunk_days=7, first_season=2025,
                  experts=ExpertConfig(min_train_rows=20, refit_days=14))
    return m


class ResumeTest(unittest.TestCase):
    def test_budget_stop_then_resume_completes_without_duplicates(self):
        full = B.walk_forward(make(), Path("."), [2025], train_years=0)
        with tempfile.TemporaryDirectory() as d:
            ck = Path(d) / "ck.pkl"
            with self.assertRaises(B.BudgetExhausted):
                B.walk_forward(make(), Path("."), [2025], train_years=0, ckpt=ck, budget_s=0.0)
            import pickle
            real = B.walk_forward
            resumed = real(make(), Path("."), [2025], train_years=0, ckpt=ck)           # finishes and saves every chunk
            again = real(make(), Path("."), [2025], train_years=0, ckpt=ck)             # pure replay from the checkpoint
            saved = pickle.loads(ck.read_bytes())
        self.assertEqual(set(full["game_id"]), set(resumed["game_id"]))
        self.assertFalse(resumed["game_id"].duplicated().any())
        self.assertGreater(len(saved), 3)
        pd.testing.assert_frame_equal(resumed.sort_values("game_id").reset_index(drop=True),
                                      again.sort_values("game_id").reset_index(drop=True))


if __name__ == "__main__":
    unittest.main()
