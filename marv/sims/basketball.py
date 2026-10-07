"""Basketball simulation (NBA / WNBA): correlated normal team scores with overtime.

Team scores share a pace component (rho), so totals vary more than margins, as they do in
real games. Ties go to 5-minute overtime periods until decided.
"""

from dataclasses import dataclass

import numpy as np

from .base import SimResult


@dataclass
class BasketballParams:
    team_sd: float  # SD of one team's score around expectation
    rho: float  # correlation of the two teams' scores (shared pace/officiating)
    ot_points: float  # average points per team in one overtime


NBA = BasketballParams(team_sd=11.5, rho=0.35, ot_points=11.0)
WNBA = BasketballParams(team_sd=10.0, rho=0.30, ot_points=9.0)


def simulate_game(home_exp: float, away_exp: float, params: BasketballParams = NBA, n: int = 20000,
                  rng: np.random.Generator | None = None) -> SimResult:
    rng = rng or np.random.default_rng()
    shared = rng.standard_normal(n)
    a, b = np.sqrt(params.rho), np.sqrt(1 - params.rho)
    home = np.round(home_exp + params.team_sd * (a * shared + b * rng.standard_normal(n)))
    away = np.round(away_exp + params.team_sd * (a * shared + b * rng.standard_normal(n)))
    share = home_exp / (home_exp + away_exp)
    for _ in range(6):
        tied = home == away
        if not tied.any():
            break
        ot_sd = params.ot_points * 0.35
        home = home + np.where(tied, np.round(rng.normal(2 * params.ot_points * share, ot_sd)), 0)
        away = away + np.where(tied, np.round(rng.normal(2 * params.ot_points * (1 - share), ot_sd)), 0)
    still = home == away
    home = home + (still & (rng.random(n) < share))
    away = away + (still & ~(home > away))
    return SimResult(home=home, away=away)
