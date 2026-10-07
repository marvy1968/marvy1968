"""Build a slate: pick marquee games, simulate them, and run every edge through the veto."""

from dataclasses import dataclass, field

import numpy as np

from .config import Settings
from .ratings import Ratings, g
from .simulate import simulate_game
from .veto import Pick, ml_pick, spread_pick, total_pick

PREFERRED_PROVIDERS = ("consensus", "DraftKings", "ESPN Bet", "Bovada")


@dataclass
class GamePrediction:
    game_id: int | None
    start: str
    home: str
    away: str
    neutral: bool
    home_exp: float
    away_exp: float
    model_margin: float
    model_total: float
    home_win: float
    spread: float | None
    total: float | None
    picks: list[Pick] = field(default_factory=list)


def choose_line(line_entry: dict) -> dict | None:
    """Pick the most trusted sportsbook line that has a spread."""
    lines = [ln for ln in line_entry.get("lines", []) if ln.get("spread") is not None]
    if not lines:
        return None
    for provider in PREFERRED_PROVIDERS:
        for ln in lines:
            if (ln.get("provider") or "").lower() == provider.lower():
                return ln
    return lines[0]


def _num(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def predict_slate(games: list[dict], lines: list[dict], ratings: Ratings, s: Settings,
                  rng: np.random.Generator | None = None) -> list[GamePrediction]:
    rng = rng or np.random.default_rng()
    by_id = {entry["id"]: entry for entry in lines if "id" in entry}
    by_teams = {(g(e, "homeTeam", "home_team"), g(e, "awayTeam", "away_team")): e for e in lines}

    candidates = []
    for game in games:
        home, away = g(game, "homeTeam", "home_team"), g(game, "awayTeam", "away_team")
        if home not in ratings.offense or away not in ratings.offense:
            continue  # FBS vs FBS only
        entry = by_id.get(game.get("id")) or by_teams.get((home, away))
        line = choose_line(entry) if entry else None
        if line is None:
            continue
        candidates.append((ratings.strength(home) + ratings.strength(away), game, line))

    # Marquee games: the strongest combined matchups on the slate.
    candidates.sort(key=lambda c: c[0], reverse=True)
    out = []
    for _, game, line in candidates[: s.max_games]:
        home, away = g(game, "homeTeam", "home_team"), g(game, "awayTeam", "away_team")
        neutral = bool(g(game, "neutralSite", "neutral_site", False))
        home_exp, away_exp = ratings.expected_points(home, away, neutral)
        sim = simulate_game(home_exp, away_exp, n=s.simulations, garbage_margin=s.garbage_margin,
                            pace=ratings.game_pace(home, away), rng=rng)
        min_games = min(ratings.games_played.get(home, 0), ratings.games_played.get(away, 0))

        spread = _num(line.get("spread"))
        total = _num(g(line, "overUnder", "over_under"))
        pred = GamePrediction(
            game_id=game.get("id"), start=g(game, "startDate", "start_date", "") or "",
            home=home, away=away, neutral=neutral, home_exp=home_exp, away_exp=away_exp,
            model_margin=float(np.median(sim.margin)), model_total=float(np.median(sim.total)),
            home_win=sim.home_win_prob(), spread=spread, total=total,
        )
        if spread is not None:
            pred.picks.append(spread_pick(home, away, spread, _num(g(line, "spreadOpen", "spread_open")),
                                          sim.home_cover_prob(spread), float(sim.margin.mean()), min_games, s))
        if total is not None:
            pred.picks.append(total_pick(total, _num(g(line, "overUnderOpen", "over_under_open")),
                                         sim.over_prob(total), float(sim.total.mean()), min_games, s))
        home_ml, away_ml = _num(g(line, "homeMoneyline", "home_moneyline")), _num(g(line, "awayMoneyline", "away_moneyline"))
        if home_ml and away_ml:
            pred.picks.append(ml_pick(home, away, home_ml, away_ml, pred.home_win, min_games, s))
        out.append(pred)
    return out


def grade(pick: Pick, pred: GamePrediction, home_pts: float, away_pts: float) -> str:
    """Return 'win', 'loss' or 'push' for a pick given the final score."""
    if pick.market == "spread":
        margin = (home_pts - away_pts) if pick.side == pred.home else (away_pts - home_pts)
        result = margin + pick.line
    elif pick.market == "total":
        diff = (home_pts + away_pts) - pick.line
        result = diff if pick.side == "Over" else -diff
    else:
        margin = (home_pts - away_pts) if pick.side == pred.home else (away_pts - home_pts)
        result = margin
    return "win" if result > 0 else "loss" if result < 0 else "push"
