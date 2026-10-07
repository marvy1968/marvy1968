"""Specialized collegiate veto logic: decide which edges are bettable and which to pass on."""

from dataclasses import dataclass, field

from .config import Settings

BREAK_EVEN = 110 / 210  # win rate needed at standard -110 juice (52.38%)
MIN_GAMES = 2  # each team needs this many games before we trust its rating
MAX_MODEL_GAP = 17.0  # model this far from the market usually means bad data, not an edge
ML_FAVORITE_LIMIT = -400
ML_DOG_LIMIT = 400


@dataclass
class Pick:
    market: str  # "spread", "total" or "ml"
    side: str  # team name, "Over" or "Under"
    line: float | None
    prob: float
    edge: float
    vetoes: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return not self.vetoes


def required_spread_edge(spread: float, s: Settings) -> float:
    if abs(spread) >= 28:
        return s.huge_spread_edge
    if abs(spread) >= 21:
        return s.big_spread_edge
    return s.spread_edge


def spread_pick(home: str, away: str, spread: float, spread_open: float | None, home_cover: float,
                model_margin: float, min_games: int, s: Settings) -> Pick:
    """spread is the home line (negative = home favored); model_margin is home minus away."""
    if home_cover >= 0.5:
        pick = Pick("spread", home, spread, home_cover, home_cover - BREAK_EVEN)
        moved_against = None if spread_open is None else spread - spread_open
    else:
        pick = Pick("spread", away, -spread, 1 - home_cover, (1 - home_cover) - BREAK_EVEN)
        moved_against = None if spread_open is None else spread_open - spread

    need = required_spread_edge(spread, s)
    if pick.edge < need:
        tier = " (blowout tier)" if need > s.spread_edge else ""
        pick.vetoes.append(f"edge {pick.edge:+.1%} below {need:.0%}{tier}")
    if moved_against is not None and moved_against >= s.trap_move:
        pick.vetoes.append(f"trap line: moved {moved_against:.1f} pts against this side")
    if abs(model_margin + spread) > MAX_MODEL_GAP:
        pick.vetoes.append(f"volatile mismatch: model {model_margin:+.1f} vs market {-spread:+.1f}")
    if min_games < MIN_GAMES:
        pick.vetoes.append("not enough games played")
    return pick


def total_pick(total: float, total_open: float | None, over: float, model_total: float,
               min_games: int, s: Settings) -> Pick:
    if over >= 0.5:
        pick = Pick("total", "Over", total, over, over - BREAK_EVEN)
        moved_against = None if total_open is None else total - total_open
    else:
        pick = Pick("total", "Under", total, 1 - over, (1 - over) - BREAK_EVEN)
        moved_against = None if total_open is None else total_open - total

    if pick.edge < s.total_edge:
        pick.vetoes.append(f"edge {pick.edge:+.1%} below {s.total_edge:.0%}")
    if moved_against is not None and moved_against >= s.trap_move:
        pick.vetoes.append(f"trap line: total moved {moved_against:.1f} pts against this side")
    if abs(model_total - total) > MAX_MODEL_GAP:
        pick.vetoes.append(f"pace volatility: model total {model_total:.1f} vs {total:.1f}")
    if min_games < MIN_GAMES:
        pick.vetoes.append("not enough games played")
    return pick


def american_to_prob(odds: float) -> float:
    return 100 / (odds + 100) if odds > 0 else -odds / (-odds + 100)


def ml_pick(home: str, away: str, home_ml: float, away_ml: float, home_win: float,
            min_games: int, s: Settings) -> Pick:
    ph, pa = american_to_prob(home_ml), american_to_prob(away_ml)
    fair_home = ph / (ph + pa)  # strip the bookmaker's vig
    home_edge = home_win - fair_home
    if home_edge >= 0:
        pick, odds = Pick("ml", home, home_ml, home_win, home_edge), home_ml
    else:
        pick, odds = Pick("ml", away, away_ml, 1 - home_win, -home_edge), away_ml

    if pick.edge < s.ml_edge:
        pick.vetoes.append(f"edge {pick.edge:+.1%} below {s.ml_edge:.0%}")
    if odds <= ML_FAVORITE_LIMIT or odds >= ML_DOG_LIMIT:
        pick.vetoes.append(f"price {odds:+.0f} outside {ML_FAVORITE_LIMIT}/+{ML_DOG_LIMIT}")
    if min_games < MIN_GAMES:
        pick.vetoes.append("not enough games played")
    return pick
