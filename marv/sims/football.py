"""Drive-based Monte Carlo football simulation (college and NFL).

The PDF calls for Poisson iterations. Pure Poisson touchdown counts are too noisy for
football (simulated margins come out with an SD of ~19-21 points versus ~16 in real
college games), so scoring is simulated per possession instead: each team gets a pace-driven
number of drives per quarter and each drive ends in a touchdown, field goal or nothing.
This is the binomial counterpart of the Poisson event model and keeps it calibrated.

  * Explosive scoring runs: every simulated game draws a gamma "form" multiplier per
    team, so some games run hot and others go cold.
  * Garbage-time dampener: a team leading by GARBAGE_MARGIN+ entering the 4th quarter
    pulls its starters (scoring rate x LEADER_DAMPEN) while the trailing team faces a
    prevent defense (x TRAILER_BOOST).
  * Pace: faster matchups get more drives (and higher expected points, see ratings.py).
"""

from dataclasses import dataclass

import numpy as np

from .base import SimResult

TD_POINTS = 6.95  # touchdown plus an average extra-point/two-point attempt
FG_POINTS = 3.0
MAX_SCORE_RATE = 0.92  # a drive can't score more often than this


@dataclass
class FootballParams:
    drives: float = 12.0  # possessions per team in an average-tempo game
    td_share: float = 0.78  # share of points that come from touchdowns (rest from field goals)
    form_shape: float = 20.0  # gamma shape for the per-game explosiveness multiplier
    garbage_margin: float = 21.0  # lead entering Q4 that triggers the dampener
    leader_dampen: float = 0.55
    trailer_boost: float = 1.10


CFB = FootballParams()
NFL = FootballParams(drives=11.0, td_share=0.70, form_shape=30.0, garbage_margin=17.0,
                     leader_dampen=0.75, trailer_boost=1.10)


def simulate_game(home_exp: float, away_exp: float, params: FootballParams = CFB, n: int = 20000,
                  pace: float = 1.0, rng: np.random.Generator | None = None) -> SimResult:
    rng = rng or np.random.default_rng()
    drives_per_q = params.drives * pace / 4
    form_h = rng.gamma(params.form_shape, 1 / params.form_shape, n)
    form_a = rng.gamma(params.form_shape, 1 / params.form_shape, n)

    def quarter(exp: float, form: np.ndarray, mult: np.ndarray | float = 1.0) -> np.ndarray:
        drives = np.floor(drives_per_q) + (rng.random(n) < drives_per_q % 1)
        pts_per_drive = max(exp, 1.0) * form * mult / (params.drives * pace)
        p_td = pts_per_drive * params.td_share / TD_POINTS
        p_fg = pts_per_drive * (1 - params.td_share) / FG_POINTS
        scale = np.minimum(1.0, MAX_SCORE_RATE / (p_td + p_fg))
        p_td, p_fg = p_td * scale, p_fg * scale
        tds = rng.binomial(drives.astype(int), p_td)
        fgs = rng.binomial((drives - tds).astype(int), np.minimum(1.0, p_fg / (1 - p_td)))
        return tds * TD_POINTS + fgs * FG_POINTS

    home = np.zeros(n)
    away = np.zeros(n)
    for _ in range(3):
        home += quarter(home_exp, form_h)
        away += quarter(away_exp, form_a)

    g = params.garbage_margin
    lead = home - away
    home_mult = np.where(lead >= g, params.leader_dampen, np.where(lead <= -g, params.trailer_boost, 1.0))
    away_mult = np.where(lead <= -g, params.leader_dampen, np.where(lead >= g, params.trailer_boost, 1.0))
    home += quarter(home_exp, form_h, home_mult)
    away += quarter(away_exp, form_a, away_mult)

    # Round touchdowns to whole points (6.95 -> mix of 6/7/8) and settle overtime.
    home = np.round(home)
    away = np.round(away)
    tied = home == away
    if tied.any():
        p_home = home_exp / (home_exp + away_exp)
        home_wins = rng.random(n) < p_home
        ot_points = np.where(rng.random(n) < 0.6, 6, 3)
        home = home + np.where(tied & home_wins, ot_points, 0)
        away = away + np.where(tied & ~home_wins, ot_points, 0)
    return SimResult(home=home, away=away)
