"""Sport registry: ratings form, simulator, veto thresholds, data source and schedule for each sport."""

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .models import Game
from .ratings import RatingParams, Ratings
from .sims import baseball, basketball, football, hockey, soccer
from .sims.base import SimResult

FCS = "FCS (pooled)"
DAILY = {d: 26 for d in range(7)}  # every day, games in the next 26 hours


@dataclass
class VetoParams:
    spread_edge: float = 0.04  # probability points over break-even
    total_edge: float = 0.04
    ml_edge: float = 0.05
    spread_tiers: list[tuple[float, float]] = field(default_factory=list)  # (|spread| >=, required edge)
    trap_spread: float | None = 2.0  # line moved this far against our side since open
    trap_total: float | None = 2.5
    spread_gap: float | None = None  # model margin this far from the market = probably bad data
    total_gap: float | None = None
    ml_prob_gap: float = 0.22  # model win prob this far from the no-vig market = suspicious
    min_games: int = 3
    ml_min: float = -400
    ml_max: float = 250  # longshots are where books overcharge most (favorite-longshot bias)


@dataclass
class Sport:
    key: str
    name: str
    emoji: str
    source: str  # espn | nflverse | cfbd
    rating: RatingParams
    veto: VetoParams
    simulate: Callable[..., SimResult]
    spread_label: str = "ATS"
    schedule: dict[int, int] = field(default_factory=lambda: dict(DAILY))  # weekday (Mon=0) -> hours ahead
    history_days: int = 400
    max_games: int | None = None  # keep only the strongest N matchups
    espn_paths: list[tuple[str, str]] = field(default_factory=list)  # (ESPN path, league label)
    odds_api_keys: list[str] = field(default_factory=list)
    three_way: bool = False  # soccer 1X2 moneyline
    margin_sd: float = 13.0  # used to turn a moneyline into an implied margin
    model_weight: float = 0.5  # calibration: share of the projection vs the market's implied score


def _football(params):
    def run(h, a, game: Game, ctx: dict, n: int, rng: np.random.Generator):
        pace = ctx.get("pace", {})
        game_pace = (pace.get(game.home, 1.0) + pace.get(game.away, 1.0)) / 2
        return football.simulate_game(h, a, params, n=n, pace=game_pace, rng=rng)
    return run


def _basketball(params):
    return lambda h, a, game, ctx, n, rng: basketball.simulate_game(h, a, params, n=n, rng=rng)


def _hockey(h, a, game, ctx, n, rng):
    params = hockey.HockeyParams(shootout=not game.info.get("postseason"))
    return hockey.simulate_game(h, a, params, n=n, rng=rng)


LEAGUE_ERA = 4.2


def pitcher_factor(era: float | None) -> float:
    """Runs-allowed multiplier for a starter, with his ERA regressed halfway to league average."""
    if era is None:
        return 1.0
    return float(np.clip((0.5 * era + 0.5 * LEAGUE_ERA) / LEAGUE_ERA, 0.75, 1.3))


def _baseball(h, a, game, ctx, n, rng):
    return baseball.simulate_game(h, a, n=n, rng=rng,
                                  home_sp=pitcher_factor(game.info.get("home_pitcher_era")),
                                  away_sp=pitcher_factor(game.info.get("away_pitcher_era")))


def _soccer(h, a, game, ctx, n, rng):
    return soccer.simulate_game(h, a, n=n, rng=rng)


SPORTS: dict[str, Sport] = {s.key: s for s in [
    Sport("cfb", "College Football", "🏈", "cfbd",
          RatingParams(multiplicative=False, home_adv=2.5, shrink=3),
          VetoParams(spread_edge=0.04, total_edge=0.05, spread_tiers=[(21, 0.08), (28, 0.10)],
                     trap_spread=2.5, trap_total=2.5, spread_gap=17, total_gap=17, min_games=2),
          _football(football.CFB), schedule={1: 132, 4: 60, 5: 20}, max_games=30,
          odds_api_keys=["americanfootball_ncaaf"]),
    Sport("nfl", "NFL", "🏟️", "nflverse",
          RatingParams(multiplicative=False, home_adv=1.8, shrink=4, half_life_days=180),
          VetoParams(spread_edge=0.04, total_edge=0.04, spread_tiers=[(13.5, 0.06)],
                     trap_spread=2.0, trap_total=2.5, spread_gap=9, total_gap=10, min_games=3,
                     ml_max=150),
          _football(football.NFL), schedule={1: 168, 3: 14, 6: 34},
          odds_api_keys=["americanfootball_nfl"]),
    Sport("nba", "NBA", "🏀", "espn",
          RatingParams(multiplicative=False, home_adv=2.5, shrink=5, half_life_days=60),
          VetoParams(trap_spread=2.0, trap_total=3.0, spread_gap=10, total_gap=14, min_games=8),
          _basketball(basketball.NBA), espn_paths=[("basketball/nba", "NBA")],
          odds_api_keys=["basketball_nba"]),
    Sport("wnba", "WNBA", "🏀", "espn",
          RatingParams(multiplicative=False, home_adv=2.5, shrink=5, half_life_days=60),
          VetoParams(trap_spread=2.0, trap_total=3.0, spread_gap=10, total_gap=12, min_games=6),
          _basketball(basketball.WNBA), espn_paths=[("basketball/wnba", "WNBA")],
          odds_api_keys=["basketball_wnba"]),
    Sport("nhl", "NHL", "🏒", "espn",
          RatingParams(multiplicative=True, home_adv=1.08, shrink=8, half_life_days=90),
          VetoParams(spread_edge=0.05, total_edge=0.05, ml_edge=0.04, trap_spread=None, trap_total=0.5,
                     total_gap=1.5, ml_prob_gap=0.18, min_games=8),
          _hockey, spread_label="Puck line", margin_sd=2.4, espn_paths=[("hockey/nhl", "NHL")],
          odds_api_keys=["icehockey_nhl"]),
    Sport("mlb", "MLB", "⚾", "espn",
          RatingParams(multiplicative=True, home_adv=1.06, shrink=10, half_life_days=90),
          VetoParams(spread_edge=0.05, total_edge=0.05, ml_edge=0.04, trap_spread=None, trap_total=1.0,
                     total_gap=2.5, ml_prob_gap=0.18, min_games=10),
          _baseball, spread_label="Run line", margin_sd=4.2, espn_paths=[("baseball/mlb", "MLB")],
          odds_api_keys=["baseball_mlb"]),
    Sport("soccer", "Soccer", "⚽", "espn",
          RatingParams(multiplicative=True, home_adv=1.25, shrink=6, half_life_days=180),
          VetoParams(spread_edge=0.05, total_edge=0.05, ml_edge=0.05, trap_spread=0.5, trap_total=0.5,
                     spread_gap=1.5, total_gap=1.5, ml_prob_gap=0.20, min_games=5),
          _soccer, spread_label="Asian handicap", margin_sd=1.6, max_games=30, history_days=450, three_way=True,
          espn_paths=[("soccer/eng.1", "Premier League"), ("soccer/esp.1", "La Liga"),
                      ("soccer/ita.1", "Serie A"), ("soccer/ger.1", "Bundesliga"),
                      ("soccer/fra.1", "Ligue 1"), ("soccer/usa.1", "MLS"),
                      ("soccer/uefa.champions", "Champions League")],
          odds_api_keys=["soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a",
                         "soccer_germany_bundesliga", "soccer_france_ligue_one", "soccer_usa_mls",
                         "soccer_uefa_champs_league"]),
]}


def team_filter(sport: Sport, games: list[Game]):
    """College football pools every non-FBS opponent into one rating."""
    if sport.key != "cfb":
        return None
    fbs = {gm.home for gm in games if gm.info.get("home_fbs", True)}
    fbs |= {gm.away for gm in games if gm.info.get("away_fbs", True)}
    return lambda team: team if team in fbs else FCS


def sport_vetoes(sport: Sport, game: Game, ratings: Ratings) -> list[str]:
    """Game-level vetoes that apply to every market."""
    out = []
    if game.info.get("postponed"):
        out.append("game postponed/suspended")
    if sport.key == "mlb" and not (game.info.get("home_pitcher") and game.info.get("away_pitcher")):
        out.append("starting pitcher unconfirmed")
    played = min(ratings.games_played.get(game.home, 0), ratings.games_played.get(game.away, 0))
    if played < sport.veto.min_games:
        out.append(f"insufficient history ({played} games)")
    return out
