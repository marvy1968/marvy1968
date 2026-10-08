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
    endgame: bool = False  # NFL: last possessions play the score (FG when tied/down <=3, TD when down 4-8)
    end_fg: float = 0.40  # field-goal rate on a last drive that only needs a FG
    end_td: float = 0.28  # touchdown rate on a last drive that needs a TD
    key3_lo: float = 0.0  # NFL key numbers: share of 1-2 pt finishes that real games turn into 3
    key3_hi: float = 0.0  # ... of 4-5 pt finishes that end at 3
    key7: float = 0.0  # ... of 6/8/9 pt finishes that end at 7


CFB = FootballParams()
# NFL calibrated on every 2016-26 game simulated at its closing spread/total (ANALYSIS.md "NFL simulator
# calibration"): form_shape 120 matches the real spread of totals (13.2) and margins (12.7) around the lines;
# the endgame + key-number transfer bring 3-pt finishes from 8.0% to ~12.8% (real 14.7%) and 7 to ~8.3% (8.5%).
NFL = FootballParams(drives=11.0, td_share=0.70, form_shape=120.0, garbage_margin=17.0,
                     leader_dampen=0.75, trailer_boost=1.10, endgame=True, key3_lo=0.15, key3_hi=0.20, key7=0.10)


def _key_numbers(home: np.ndarray, away: np.ndarray, p: "FootballParams", rng: np.random.Generator):
    """Move a calibrated share of near-miss finishes onto 3 and 7 (late field goals, kneel-downs, PAT/2pt
    choices the drive model can't see). The losing side's score moves, so totals barely change."""
    margin = home - away
    a = np.abs(margin)
    u = rng.random(len(a))
    target = np.where(((a >= 1) & (a <= 2) & (u < p.key3_lo)) | ((a >= 4) & (a <= 5) & (u < p.key3_hi)), 3,
                      np.where(((a == 6) | (a == 8) | (a == 9)) & (u < p.key7), 7, a))
    move = target != a
    win_home = margin > 0
    loser = np.where(win_home, away, home)
    winner = np.where(win_home, home, away)
    new_loser = winner - target
    short = new_loser < 0  # can't go below zero: lift the winner instead
    new_winner = np.where(short, winner - new_loser, winner)
    new_loser = np.maximum(new_loser, 0)
    home = np.where(move, np.where(win_home, new_winner, new_loser), home)
    away = np.where(move, np.where(win_home, new_loser, new_winner), away)
    return home, away


def _td_points(tds: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Points from touchdowns with real extra-point outcomes: 7 (93%), 6 (4%, missed PAT/failed 2pt), 8 (3%)."""
    extra = rng.binomial(tds, 0.03) - rng.binomial(tds, 0.04)
    return tds * 7 + extra


def _final_drive(diff: np.ndarray, rng: np.random.Generator, p: "FootballParams", base_td: np.ndarray,
                 base_fg: np.ndarray) -> np.ndarray:
    """Points on a team's last possession given its current margin (own - opponent)."""
    need_fg = (diff >= -3) & (diff <= 0)
    need_td = (diff >= -8) & (diff <= -4)
    ahead = diff >= 1
    p_td = np.select([need_fg, need_td, ahead], [p.end_td * 0.45, p.end_td, base_td * 0.5], base_td)
    p_fg = np.select([need_fg, need_td, ahead], [p.end_fg, 0.03, base_fg * 1.2], base_fg)
    u = rng.random(len(diff))
    td = u < p_td
    fg = (~td) & (u < p_td + p_fg)
    return np.where(td, _td_points(td.astype(int), rng), 0) + np.where(fg, FG_POINTS, 0)


def simulate_game(home_exp: float, away_exp: float, params: FootballParams = CFB, n: int = 20000,
                  pace: float = 1.0, rng: np.random.Generator | None = None) -> SimResult:
    rng = rng or np.random.default_rng()
    drives_per_q = params.drives * pace / 4
    form_h = rng.gamma(params.form_shape, 1 / params.form_shape, n)
    form_a = rng.gamma(params.form_shape, 1 / params.form_shape, n)

    def quarter(exp: float, form: np.ndarray, mult: np.ndarray | float = 1.0, n_drives: float | None = None) -> np.ndarray:
        d = drives_per_q if n_drives is None else max(n_drives, 0.0)
        drives = np.floor(d) + (rng.random(n) < d % 1)
        pts_per_drive = max(exp, 1.0) * form * mult / (params.drives * pace)
        p_td = pts_per_drive * params.td_share / TD_POINTS
        p_fg = pts_per_drive * (1 - params.td_share) / FG_POINTS
        scale = np.minimum(1.0, MAX_SCORE_RATE / (p_td + p_fg))
        p_td, p_fg = p_td * scale, p_fg * scale
        tds = rng.binomial(drives.astype(int), p_td)
        fgs = rng.binomial((drives - tds).astype(int), np.minimum(1.0, p_fg / (1 - p_td)))
        if params.endgame:
            return _td_points(tds, rng) + fgs * FG_POINTS
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
    if not params.endgame:
        home += quarter(home_exp, form_h, home_mult)
        away += quarter(away_exp, form_a, away_mult)
    else:
        # 4th quarter minus each team's last possession, then the last possessions play the score.
        q4 = drives_per_q - 1
        home += quarter(home_exp, form_h, home_mult, q4)
        away += quarter(away_exp, form_a, away_mult, q4)
        home, away = np.round(home), np.round(away)
        rate = lambda exp, form: max(exp, 1.0) * form / (params.drives * pace)  # noqa: E731
        h_rate, a_rate = rate(home_exp, form_h), rate(away_exp, form_a)
        h_td, h_fg = h_rate * params.td_share / 7, h_rate * (1 - params.td_share) / FG_POINTS
        a_td, a_fg = a_rate * params.td_share / 7, a_rate * (1 - params.td_share) / FG_POINTS
        home_first = rng.random(n) < 0.5
        # whoever has the ball first, then the other team answers
        h1 = np.where(home_first, _final_drive(home - away, rng, params, h_td, h_fg), 0)
        a_mid = _final_drive(away - (home + h1), rng, params, a_td, a_fg)
        a1 = np.where(~home_first, _final_drive(away - home, rng, params, a_td, a_fg), 0)
        home = home + h1
        away = away + np.where(home_first, a_mid, a1)
        h_last = np.where(~home_first, _final_drive(home - away, rng, params, h_td, h_fg), 0)
        home = home + h_last

    # Round touchdowns to whole points (6.95 -> mix of 6/7/8) and settle overtime.
    home = np.round(home)
    away = np.round(away)
    tied = home == away
    if params.endgame:
        tied &= rng.random(n) >= 0.06  # ~6% of NFL overtimes end tied
    if tied.any():
        p_home = home_exp / (home_exp + away_exp)
        home_wins = rng.random(n) < p_home
        ot_points = np.where(rng.random(n) < (0.61 if params.endgame else 0.4), 3, 6)
        home = home + np.where(tied & home_wins, ot_points, 0)
        away = away + np.where(tied & ~home_wins, ot_points, 0)
    if params.key3_lo or params.key3_hi or params.key7:
        home, away = _key_numbers(home, away, params, rng)
    return SimResult(home=home, away=away)
