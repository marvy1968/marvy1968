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


def team_possessions(fga: float, oreb: float, tov: float, fta: float) -> float:
    """Dean Oliver possession estimate for one team.
    poss = FGA - OREB + TOV + 0.44 * FTA.
    OREB here is the boxscore-consistent count (player + team offensive
    rebounds, dead-ball rebounds excluded) — validated 2026-11-08: this rule
    gives the closest cross-team possession agreement on college games
    (mean |poss_home - poss_away| 1.70 NCAAB / 1.52 NCAAW over 40 games
    each, vs 2.63 / 2.27 for player-only OREB)."""
    return fga - oreb + tov + 0.44 * fta


@dataclass
class TeamPaceInput:
    current: float          # team total so far (points, rebounds, assists, threes)
    elapsed_min: float      # game clock elapsed (40 min college, 48 min NBA)
    total_min: float = 40.0
    pregame_mean: float = 0.0   # expected final team total (season average)
    pregame_sd: float = 0.0     # SD of the team's final totals
    poss_so_far: float = 0.0    # THIS team's possessions so far
    game_poss_per_min_live: float = 0.0  # live game pace (poss/min, mean of both teams)
    season_poss_per_min: float = 1.75
    sd_inflation: float = 1.25  # 1.25 NCAAB / 1.35 NCAAW (SPORTS registry)
    use_live_pace: bool = True  # False = season pace only (rate-only rework,
    # pace-off-test-spec-2026-11-09). Default True: all existing callers unchanged.


def project_team_total(p: TeamPaceInput) -> tuple[float, float]:
    """Return (projected_final_mean, live_sd) for a TEAM total (points,
    rebounds, assists, threes). Same shrinkage and caps as the player
    projection: w from elapsed fraction, live pace capped 0.85-1.15x season,
    live per-possession rate capped 0.5-1.5x pregame. No foul/blowout
    adjustments — team totals don't sit. See pace-props-test-spec-2026-11-08.
    """
    if p.elapsed_min <= 0:
        return p.pregame_mean, p.pregame_sd * p.sd_inflation
    w = min(0.85, max(0.10, p.elapsed_min / p.total_min))
    if not p.use_live_pace:
        # Rate-only rework: remaining possessions at season pace exactly.
        blended_pace = p.season_poss_per_min
    else:
        live_pace = min(max(p.game_poss_per_min_live, p.season_poss_per_min * 0.85),
                        p.season_poss_per_min * 1.15)
        blended_pace = w * live_pace + (1 - w) * p.season_poss_per_min
    remaining_min = max(0.0, p.total_min - p.elapsed_min)
    remaining_poss = blended_pace * remaining_min
    pregame_per_poss = p.pregame_mean / (p.total_min * p.season_poss_per_min) \
        if p.total_min * p.season_poss_per_min > 0 else 0.0
    if p.poss_so_far > 0 and pregame_per_poss > 0:
        live_per_poss_raw = p.current / p.poss_so_far
        live_per_poss = min(max(live_per_poss_raw, pregame_per_poss * 0.5),
                            pregame_per_poss * 1.5)
    elif p.poss_so_far > 0:
        live_per_poss = p.current / p.poss_so_far
    else:
        live_per_poss = pregame_per_poss
    per_poss = w * live_per_poss + (1 - w) * pregame_per_poss
    projected_final = p.current + remaining_poss * per_poss
    remaining_frac = remaining_min / p.total_min if p.total_min else 0
    sd_live = p.pregame_sd * math.sqrt(max(0.05, remaining_frac)) * p.sd_inflation
    sd_live = max(sd_live, p.pregame_sd * 0.35)
    return projected_final, sd_live


def evaluate_team_pick(projected: float, sd: float, line: float,
                       over_price: int, under_price: int,
                       low_count: bool = False,
                       min_edge_pp: float = 0.07,
                       min_ev: float = 0.05) -> dict:
    """Gate a team-total line through the same edge/EV bars as player picks.
    Normal(mean=proj, sd=sd) for points/rebounds/assists; Poisson(lambda=proj)
    for threes (low_count=True, same as the runner does for player_threes).
    Returns the better side's model_p/fair_p/ev/ok/why. Pure evaluation —
    no network, no odds fetch. Live wiring waits on a verified PropLine
    team-lines payload shape (see bovada.TEAM_MARKETS)."""
    if low_count:
        lam = max(0.0, projected)
        k = int(line)  # line 1.5 -> need >= 2; line 0.5 -> need >= 1
        p_over = 1.0 - sum(poisson_pmf(i, lam) for i in range(k + 1))
    else:
        p_over = prob_over_yards(projected, sd, line)
    imp_over = american_to_implied(over_price)
    imp_under = american_to_implied(under_price)
    fair_over, fair_under = remove_vig(imp_over, imp_under)
    candidates = []
    for side, price, model_p, fair in (
            ("over", over_price, p_over, fair_over),
            ("under", under_price, 1 - p_over, fair_under)):
        ev = evaluate_pick(model_p, price, side)["ev"]
        ok, why = passes_gates(model_p, fair, ev, min_edge_pp, min_ev)
        candidates.append({"side": side, "price": price, "model_p": model_p,
                           "fair_p": fair, "ev": ev, "ok": ok, "why": why})
    return max(candidates, key=lambda c: c["model_p"] - c["fair_p"])


@dataclass
class BasketballPaceInput:
    current: float          # stat so far (points, rebounds, etc.)
    minutes_played: float   # player minutes so far
    elapsed_min: float      # game clock elapsed (40 min college, 48 min NBA)
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
    use_live_rate: bool = True  # False = force the pregame per-minute rate
    # (pace-vs-avg decomposition test, pace-vs-avg-test-spec-2026-11-08).
    # Default True: all existing callers unchanged.
    use_live_pace: bool = True  # False = no pace-ratio multiplication
    # (rate-only rework, pace-off-test-spec-2026-11-09). Default True:
    # all existing callers unchanged.


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
    """Return (projected_final_mean, live_sd) for NCAAB / NCAAW / NBA.
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
    if not p.use_live_rate:
        # Decomposition arm (pace-vs-avg test): force the pregame rate, so
        # per_min below equals pregame_per_min exactly. The pace multiplier
        # still applies afterwards (arm (b): pregame rate x live pace).
        live_per_min = p.pregame_per_min
    elif p.pregame_per_min > 0:
        live_per_min_raw = p.current / p.minutes_played if p.minutes_played > 0 else p.pregame_per_min
        live_per_min = min(max(live_per_min_raw, p.pregame_per_min * 0.5),
                           p.pregame_per_min * 1.5)
    else:
        live_per_min = p.current / p.minutes_played if p.minutes_played > 0 else 0
    per_min = w * live_per_min + (1 - w) * p.pregame_per_min
    # Pace adjustment: faster game = more possessions = more chances.
    # Skipped when use_live_pace is False (rate-only rework: pace ratio 1.0).
    if p.use_live_pace and p.elapsed_min > 0 and p.poss_so_far > 0:
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
    # NBA: added 2026-11-08 for the pace test (pace-props-nba-test-spec-2026-11-08).
    # PropLine key NOT confirmed — verify via /v1/sports before use. The runner
    # does not poll NBA (config.yaml does not list it) until config adds it.
    # sd_inflation 1.25 = the NCAAB/basketball default (see the NBA spec — it only
    # scales sd_live, which the MAE test does not score).
    "nba": {"total_min": 48, "min_elapsed": 8,  "sd_inflation": 1.25, "propline_key": "basketball_nba", "espn_path": "basketball/nba"},
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
