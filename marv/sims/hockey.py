"""Hockey simulation: Poisson goals, late-game empty net, 3-on-3 overtime and shootout.

Goals follow a bivariate Poisson (a shared component adds the extra regulation ties real
hockey shows). Regulation is split into the first 57 minutes (even play) and the final 3 minutes, where a
team trailing by one or two pulls its goalie: the leader gains an empty-net scoring rate and
the trailer an extra-attacker boost. Regulation ties go to a 5-minute overtime, then a
shootout; the winner is credited one extra goal, matching sportsbook settlement.
"""

from dataclasses import dataclass

import numpy as np

from .base import SimResult


@dataclass
class HockeyParams:
    form_shape: float = 40.0  # slight over-dispersion of scoring rates game to game
    shared_rate: float = 0.6  # bivariate-Poisson common component: score effects make ties likelier
    late_goal_share: float = 0.06  # share of fitted goals that are empty-net/shootout (removed from base rate)
    pull_minutes: float = 3.0
    empty_net_rate: float = 0.35  # expected empty-net goals for the leader during the pull window
    extra_attacker_rate: float = 0.12  # expected extra goals for the trailing team
    ot_goal_prob: float = 0.62  # chance a 3-on-3 overtime produces a goal
    shootout: bool = True  # False for playoffs (sudden-death OT until a goal)


NHL = HockeyParams()


def simulate_game(home_exp: float, away_exp: float, params: HockeyParams = NHL, n: int = 20000,
                  rng: np.random.Generator | None = None) -> SimResult:
    rng = rng or np.random.default_rng()
    base = 1 - params.late_goal_share
    c = min(params.shared_rate, 0.8 * min(home_exp, away_exp) * base)
    lam_h = (home_exp * base - c) * rng.gamma(params.form_shape, 1 / params.form_shape, n)
    lam_a = (away_exp * base - c) * rng.gamma(params.form_shape, 1 / params.form_shape, n)
    early = (60 - params.pull_minutes) / 60
    late = params.pull_minutes / 60
    common = rng.poisson(c * early, n)
    home = (rng.poisson(lam_h * early) + common).astype(float)
    away = (rng.poisson(lam_a * early) + common).astype(float)

    diff = home - away
    home_trails = (diff < 0) & (diff >= -2)
    away_trails = (diff > 0) & (diff <= 2)
    home += rng.poisson(lam_h * late + np.where(away_trails, params.empty_net_rate, 0)
                        + np.where(home_trails, params.extra_attacker_rate, 0))
    away += rng.poisson(lam_a * late + np.where(home_trails, params.empty_net_rate, 0)
                        + np.where(away_trails, params.extra_attacker_rate, 0))

    reg_tie = home == away
    share = lam_h / (lam_h + lam_a)
    if params.shootout:
        decided_ot = rng.random(n) < params.ot_goal_prob
        so_share = 0.5 + 0.2 * (share - 0.5)  # shootouts are close to coin flips
        home_wins = np.where(decided_ot, rng.random(n) < share, rng.random(n) < so_share)
    else:
        home_wins = rng.random(n) < share
    home = home + (reg_tie & home_wins)
    away = away + (reg_tie & ~home_wins)
    return SimResult(home=home, away=away, reg_tie=reg_tie)
