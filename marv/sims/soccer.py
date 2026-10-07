"""Soccer: Dixon-Coles adjusted Poisson scoreline matrix, sampled for market settlement.

The full scoreline probability matrix is computed exactly (independent Poissons with the
Dixon-Coles low-score correction for 0-0, 1-0, 0-1, 1-1), then sampled so every market
(1X2, Asian handicap including quarter lines, totals, BTTS) settles from one distribution.
"""

from dataclasses import dataclass
from math import exp, factorial

import numpy as np

from .base import SimResult


@dataclass
class SoccerParams:
    rho: float = -0.08  # Dixon-Coles dependence for low scores (negative = more 0-0 / 1-1)
    max_goals: int = 10


SOCCER = SoccerParams()


def score_matrix(home_exp: float, away_exp: float, params: SoccerParams = SOCCER) -> np.ndarray:
    k = np.arange(params.max_goals + 1)
    ph = np.array([exp(-home_exp) * home_exp ** i / factorial(i) for i in k])
    pa = np.array([exp(-away_exp) * away_exp ** i / factorial(i) for i in k])
    m = np.outer(ph, pa)
    rho = params.rho
    m[0, 0] *= 1 - home_exp * away_exp * rho
    m[0, 1] *= 1 + home_exp * rho
    m[1, 0] *= 1 + away_exp * rho
    m[1, 1] *= 1 - rho
    return m / m.sum()


def simulate_game(home_exp: float, away_exp: float, params: SoccerParams = SOCCER, n: int = 50000,
                  rng: np.random.Generator | None = None) -> SimResult:
    rng = rng or np.random.default_rng()
    m = score_matrix(home_exp, away_exp, params)
    idx = rng.choice(m.size, size=n, p=m.ravel())
    home, away = np.divmod(idx, m.shape[1])
    home, away = home.astype(float), away.astype(float)
    return SimResult(home=home, away=away, reg_tie=home == away)
