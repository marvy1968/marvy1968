"""In-game prop engine — pure math, no network.
Pace projection + fair probability + edge vs Bovada.
Fixes vs old NFL pacer: shrinkage, exact-line only (enforced by caller),
no lookahead (caller must pass as-of snapshots only).
"""
from __future__ import annotations
import math
from dataclasses import dataclass


def american_to_decimal(odds: int) -> float:
    if odds > 0:
        return 1 + odds / 100.0
    return 1 + 100.0 / abs(odds)


def american_to_implied(odds: int) -> float:
    return 1.0 / american_to_decimal(odds)


def remove_vig(imp_over: float, imp_under: float) -> tuple[float, float]:
    total = imp_over + imp_under
    if total <= 0:
        raise ValueError("bad implied probs")
    return imp_over / total, imp_under / total


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


@dataclass
class PaceInput:
    current: float          # stat so far
    elapsed_min: float      # minutes elapsed in game
    total_min: float = 60.0 # NFL regulation
    pregame_mean: float = 0.0
    pregame_sd: float = 0.0
    pregame_per_play: float = 0.0  # expected stat per team play
    plays_so_far: int = 0
    season_plays_per_min: float = 1.05  # ~63 plays / 60 min
    script_adj: float = 0.0  # e.g. +0.12 trailing pass boost, -0.15 leading rush
    sd_inflation: float = 1.15


def project_final(p: PaceInput) -> tuple[float, float]:
    """Return (projected_final_mean, live_sd). Shrinkage-weighted pace.
    Caps are deliberate: a hot 5 minutes must not double-count —
    once via plays/min and again via yards/play. Live pace is capped
    to 1.3x season pace, live per-play to 1.5x pregame. Found by test:
    80 yds in 5 min (vs 240 pregame) projected 371 before caps.
    """
    if p.elapsed_min <= 0:
        return p.pregame_mean, p.pregame_sd * p.sd_inflation
    w = min(0.85, max(0.10, p.elapsed_min / p.total_min))
    live_pace_raw = p.plays_so_far / p.elapsed_min if p.elapsed_min > 0 else p.season_plays_per_min
    live_pace = min(max(live_pace_raw, p.season_plays_per_min * 0.7),
                    p.season_plays_per_min * 1.3)
    blended_pace = w * live_pace + (1 - w) * p.season_plays_per_min
    remaining_min = max(0.0, p.total_min - p.elapsed_min)
    projected_plays = blended_pace * remaining_min

    if p.plays_so_far > 0 and p.pregame_per_play > 0:
        live_per_play_raw = p.current / p.plays_so_far
        live_per_play = min(max(live_per_play_raw, p.pregame_per_play * 0.5),
                            p.pregame_per_play * 1.5)
    elif p.plays_so_far > 0:
        live_per_play = p.current / p.plays_so_far
    else:
        live_per_play = p.pregame_per_play
    per_play = w * live_per_play + (1 - w) * p.pregame_per_play
    per_play *= (1.0 + p.script_adj)

    projected_final = p.current + projected_plays * per_play
    remaining_frac = remaining_min / p.total_min if p.total_min else 0
    sd_live = p.pregame_sd * math.sqrt(max(0.05, remaining_frac)) * p.sd_inflation
    # Early in game most variance is still ahead; floor SD so we don't over-trust a hot start
    sd_live = max(sd_live, p.pregame_sd * 0.35)
    return projected_final, sd_live


@dataclass
class BasketballPaceInput:
    current: float          # stat so far (points, rebounds, etc.)
    minutes_played: float   # player minutes so far
    elapsed_min: float      # game clock elapsed, 40 min total
    total_min: float = 40.0
    pregame_mean: float = 0.0
    pregame_sd: float = 0.0
    pregame_per_min: float = 0.0  # stat per minute played
    pregame_minutes: float = 30.0  # expected minutes for this player
    fouls: int = 0
    lead: float = 0.0       # player's team lead (+) / trail (-)
    season_poss_per_min: float = 1.75
    poss_so_far: int = 0
    sd_inflation: float = 1.25


def foul_minutes_factor(fouls: int, elapsed_min: float) -> float:
    """Foul trouble shrinks projected minutes. 4 fouls = skip Over (caller gates)."""
    if fouls >= 4:
        return 0.5
    if fouls == 3:
        return 0.60
    if fouls == 2 and elapsed_min < 20:
        return 0.75
    return 1.0


def project_basketball_final(p: BasketballPaceInput) -> tuple[float, float]:
    """Return (projected_final_mean, live_sd) for NCAAB / NCAAW.
    Projects minutes the player will actually play, not just game pace.
    """
    if p.elapsed_min <= 0 or p.minutes_played <= 0:
        return p.pregame_mean, p.pregame_sd * p.sd_inflation
    w = min(0.85, max(0.10, p.elapsed_min / p.total_min))
    # Minutes rate: how much of elapsed time the player has played
    live_min_rate = p.minutes_played / p.elapsed_min
    pregame_min_rate = p.pregame_minutes / p.total_min if p.total_min else 0.75
    blended_min_rate = w * live_min_rate + (1 - w) * pregame_min_rate
    remaining_min = max(0.0, p.total_min - p.elapsed_min)
    projected_minutes = blended_min_rate * remaining_min
    projected_minutes *= foul_minutes_factor(p.fouls, p.elapsed_min)
    # Blowout: starters sit
    if abs(p.lead) > 20 and remaining_min < 10:
        projected_minutes *= 0.70
    # Per-minute rate, capped like football per-play
    if p.pregame_per_min > 0:
        live_per_min_raw = p.current / p.minutes_played if p.minutes_played > 0 else p.pregame_per_min
        live_per_min = min(max(live_per_min_raw, p.pregame_per_min * 0.5),
                           p.pregame_per_min * 1.5)
    else:
        live_per_min = p.current / p.minutes_played if p.minutes_played > 0 else 0
    per_min = w * live_per_min + (1 - w) * p.pregame_per_min
    # Pace adjustment: faster game = more possessions = more chances
    if p.elapsed_min > 0 and p.poss_so_far > 0:
        live_poss = p.poss_so_far / p.elapsed_min
        pace_ratio = min(max(live_poss / p.season_poss_per_min, 0.85), 1.15)
        per_min *= pace_ratio
    projected_final = p.current + projected_minutes * per_min
    remaining_frac = remaining_min / p.total_min if p.total_min else 0
    sd_live = p.pregame_sd * math.sqrt(max(0.05, remaining_frac)) * p.sd_inflation
    sd_live = max(sd_live, p.pregame_sd * 0.35)
    return projected_final, sd_live


# Sport registry — single source for total minutes, gates, SD inflation
SPORTS = {
    "nfl":   {"total_min": 60, "min_elapsed": 12, "sd_inflation": 1.15, "propline_key": "football_nfl", "espn_path": "football/nfl"},
    "ncaaf": {"total_min": 60, "min_elapsed": 10, "sd_inflation": 1.35, "propline_key": "football_ncaaf", "espn_path": "football/college-football"},
    "ncaab": {"total_min": 40, "min_elapsed": 8,  "sd_inflation": 1.25, "propline_key": "basketball_ncaab", "espn_path": "basketball/mens-college-basketball"},
    # NCAAW: PropLine key NOT confirmed — verify via /v1/sports before use
    "ncaaw": {"total_min": 40, "min_elapsed": 8,  "sd_inflation": 1.35, "propline_key": "basketball_ncaaw", "espn_path": "basketball/womens-college-basketball"},
}


def prob_over_yards(projected_final: float, sd: float, line: float) -> float:
    # continuity correction: line is x.5, need > line
    if sd <= 0:
        return 1.0 if projected_final > line else 0.0
    return 1.0 - norm_cdf((line + 0.5 - projected_final) / sd)


def prob_over_td(projected_tds: float, line: float = 0.5) -> float:
    # Anytime TD / over 0.5 TD: P(X >= 1) for Poisson
    if line == 0.5:
        return 1.0 - poisson_pmf(0, max(0.0, projected_tds))
    # Over 1.5 TDs: P(X >= 2)
    if line == 1.5:
        return 1.0 - poisson_pmf(0, projected_tds) - poisson_pmf(1, projected_tds)
    raise ValueError("only 0.5 / 1.5 TD lines supported in v1")


def evaluate_pick(model_p: float, bovada_odds: int, side: str = "over") -> dict:
    """EV at Bovada's actual price. Caller must supply the no-vig fair prob separately for edge."""
    dec = american_to_decimal(bovada_odds)
    ev = model_p * (dec - 1) - (1 - model_p)
    return {"model_p": model_p, "decimal": dec, "ev": ev, "side": side}


def passes_gates(model_p: float, no_vig_p: float, ev: float,
                 min_edge_pp: float = 0.07, min_ev: float = 0.05) -> tuple[bool, str]:
    edge = model_p - no_vig_p
    if edge < min_edge_pp:
        return False, f"edge {edge:.1%} < {min_edge_pp:.0%}"
    if ev < min_ev:
        return False, f"EV {ev:.1%} < {min_ev:.0%}"
    return True, f"edge {edge:.1%}, EV {ev:.1%}"
