"""Drive-based Monte Carlo game simulation with collegiate adjustments.

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

TD_SHARE = 0.78  # share of points that come from touchdowns (rest from field goals)
TD_POINTS = 6.95  # touchdown plus an average extra-point/two-point attempt
FG_POINTS = 3.0
BASE_DRIVES = 12.0  # possessions per team in an average-tempo FBS game
MAX_SCORE_RATE = 0.92  # a drive can't score more often than this
FORM_SHAPE = 20.0  # gamma shape for the explosiveness multiplier (mean 1, sd ~0.22)
LEADER_DAMPEN = 0.55
TRAILER_BOOST = 1.10


@dataclass
class SimResult:
    home_points: np.ndarray
    away_points: np.ndarray

    @property
    def margin(self) -> np.ndarray:  # home minus away
        return self.home_points - self.away_points

    @property
    def total(self) -> np.ndarray:
        return self.home_points + self.away_points

    def home_cover_prob(self, home_spread: float) -> float:
        """P(home covers) with pushes removed; home_spread is negative when home is favored."""
        adj = self.margin + home_spread
        decided = adj != 0
        return float((adj[decided] > 0).mean()) if decided.any() else 0.5

    def over_prob(self, total_line: float) -> float:
        decided = self.total != total_line
        return float((self.total[decided] > total_line).mean()) if decided.any() else 0.5

    def home_win_prob(self) -> float:
        return float((self.margin > 0).mean())


def simulate_game(
    home_exp: float,
    away_exp: float,
    n: int = 20000,
    garbage_margin: float = 21.0,
    pace: float = 1.0,
    rng: np.random.Generator | None = None,
) -> SimResult:
    rng = rng or np.random.default_rng()
    drives_per_q = BASE_DRIVES * pace / 4
    form_h = rng.gamma(FORM_SHAPE, 1 / FORM_SHAPE, n)
    form_a = rng.gamma(FORM_SHAPE, 1 / FORM_SHAPE, n)

    def quarter(exp: float, form: np.ndarray, mult: np.ndarray | float = 1.0) -> np.ndarray:
        drives = np.floor(drives_per_q) + (rng.random(n) < drives_per_q % 1)
        pts_per_drive = exp * form * mult / (BASE_DRIVES * pace)
        p_td = pts_per_drive * TD_SHARE / TD_POINTS
        p_fg = pts_per_drive * (1 - TD_SHARE) / FG_POINTS
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

    lead = home - away
    home_mult = np.where(lead >= garbage_margin, LEADER_DAMPEN, np.where(lead <= -garbage_margin, TRAILER_BOOST, 1.0))
    away_mult = np.where(lead <= -garbage_margin, LEADER_DAMPEN, np.where(lead >= garbage_margin, TRAILER_BOOST, 1.0))
    home += quarter(home_exp, form_h, home_mult)
    away += quarter(away_exp, form_a, away_mult)

    # Round touchdowns to whole points (6.95 -> mix of 6/7/8) and settle overtime.
    home = np.round(home)
    away = np.round(away)
    tied = home == away
    if tied.any():
        p_home = home_exp / (home_exp + away_exp)
        home_wins = rng.random(n) < p_home
        ot_points = np.where(rng.random(n) < 0.7, 7, 3)
        home = home + np.where(tied & home_wins, ot_points, 0)
        away = away + np.where(tied & ~home_wins, ot_points, 0)
    return SimResult(home_points=home, away_points=away)
