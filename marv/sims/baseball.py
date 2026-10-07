"""Baseball simulation: inning-by-inning negative-binomial runs with starter/bullpen split.

Each half inning draws runs from a gamma-Poisson (negative binomial) mix, which matches the
heavy right tail of real innings (most score 0, a few blow up). Starting pitchers cover the
first STARTER_INNINGS innings and adjust the opposing lineup's scoring rate; bullpens cover
the rest. The home team skips the bottom of the 9th when leading, and extra innings use
the automatic runner (higher scoring rate) until decided.
"""

from dataclasses import dataclass

import numpy as np

from .base import SimResult


@dataclass
class BaseballParams:
    inning_dispersion: float = 0.55  # NB shape: smaller = more big innings
    form_shape: float = 25.0  # game-level hot/cold lineup multiplier
    starter_innings: int = 6
    extra_inning_mult: float = 1.9  # automatic runner on second
    max_innings: int = 20


MLB = BaseballParams()


def simulate_game(home_exp: float, away_exp: float, params: BaseballParams = MLB, n: int = 20000,
                  home_sp: float = 1.0, away_sp: float = 1.0,
                  rng: np.random.Generator | None = None) -> SimResult:
    """home_sp/away_sp scale the runs the OPPOSING lineup scores while that starter pitches."""
    rng = rng or np.random.default_rng()
    form_h = rng.gamma(params.form_shape, 1 / params.form_shape, n)
    form_a = rng.gamma(params.form_shape, 1 / params.form_shape, n)
    r = params.inning_dispersion

    def half(mean_runs: np.ndarray) -> np.ndarray:
        return rng.poisson(rng.gamma(r, mean_runs / r))

    home = np.zeros(n)
    away = np.zeros(n)
    for inning in range(1, 10):
        starter = inning <= params.starter_innings
        away += half(away_exp / 9 * form_a * (home_sp if starter else 1.0))
        bottom = half(home_exp / 9 * form_h * (away_sp if starter else 1.0))
        if inning == 9:
            bottom = np.where(home > away, 0, bottom)  # home team doesn't bat when already ahead
        home += bottom

    m = params.extra_inning_mult
    for _ in range(10, params.max_innings + 1):
        tied = home == away
        if not tied.any():
            break
        away += np.where(tied, half(away_exp / 9 * form_a * m), 0)
        home += np.where(tied & (home <= away), half(home_exp / 9 * form_h * m), 0)
    tied = home == away
    coin = rng.random(n) < 0.5
    home = home + (tied & coin)
    away = away + (tied & ~coin)
    return SimResult(home=home, away=away)
