"""Projection -> simulation -> market evaluation -> Max veto stack, for any sport.

Following the Marv specs, the projection is built without looking at prices; odds are only
loaded afterwards to compute edges, and the veto stack decides what qualifies.
"""

import math
from datetime import datetime

import numpy as np

from .markets import evaluate, no_vig, settle
from .models import Game, Odds, Pick, Prediction
from .ratings import fit_ratings
from .sims.base import SimResult
from .sports import Sport, VetoParams, sport_vetoes, team_filter


def _spread_pick(sim: SimResult, game: Game, odds: Odds, sport: Sport) -> Pick:
    v = sport.veto
    home = evaluate(settle(sim.margin, odds.spread), odds.home_spread_price)
    away = evaluate(settle(-sim.margin, -odds.spread), odds.away_spread_price)
    if home[1] >= away[1]:
        pick = Pick("spread", game.home, odds.spread, odds.home_spread_price, *home)
        moved = None if odds.spread_open is None else odds.spread - odds.spread_open
    else:
        pick = Pick("spread", game.away, -odds.spread, odds.away_spread_price, *away)
        moved = None if odds.spread_open is None else odds.spread_open - odds.spread
    need = v.spread_edge
    for threshold, edge in v.spread_tiers:
        if abs(odds.spread) >= threshold:
            need = max(need, edge)
    if pick.edge < need:
        tier = " (blowout tier)" if need > v.spread_edge else ""
        pick.vetoes.append(f"edge {pick.edge:+.1%} below {need:.0%}{tier}")
    if v.trap_spread is not None and moved is not None and moved >= v.trap_spread:
        pick.vetoes.append(f"trap line: moved {moved:.1f} against this side")
    if v.spread_gap is not None and abs(sim.margin.mean() + odds.spread) > v.spread_gap:
        pick.vetoes.append(f"model/market gap: model {sim.margin.mean():+.1f} vs market {-odds.spread:+.1f}")
    return pick


def _total_pick(sim: SimResult, odds: Odds, sport: Sport) -> Pick:
    v = sport.veto
    over = evaluate(settle(sim.total, -odds.total), odds.over_price)
    under = evaluate(settle(-sim.total, odds.total), odds.under_price)
    if over[1] >= under[1]:
        pick = Pick("total", "Over", odds.total, odds.over_price, *over)
        moved = None if odds.total_open is None else odds.total - odds.total_open
    else:
        pick = Pick("total", "Under", odds.total, odds.under_price, *under)
        moved = None if odds.total_open is None else odds.total_open - odds.total
    if pick.edge < v.total_edge:
        pick.vetoes.append(f"edge {pick.edge:+.1%} below {v.total_edge:.0%}")
    if v.trap_total is not None and moved is not None and moved >= v.trap_total:
        pick.vetoes.append(f"trap line: total moved {moved:.1f} against this side")
    if v.total_gap is not None and abs(sim.total.mean() - odds.total) > v.total_gap:
        pick.vetoes.append(f"pace/total gap: model {sim.total.mean():.1f} vs {odds.total:g}")
    return pick


def _ml_pick(sim: SimResult, game: Game, odds: Odds, sport: Sport) -> Pick:
    v = sport.veto
    m = sim.margin
    options = [(game.home, odds.home_ml, np.where(m > 0, 1.0, -1.0)),
               (game.away, odds.away_ml, np.where(m < 0, 1.0, -1.0))]
    prices = [odds.home_ml, odds.away_ml]
    if sport.three_way and odds.draw_ml:
        options.append(("Draw", odds.draw_ml, np.where(m == 0, 1.0, -1.0)))
        prices.append(odds.draw_ml)
    fair = dict(zip([o[0] for o in options], no_vig(*prices)))
    best = max(options, key=lambda o: evaluate(o[2], o[1])[1])
    side, price, outcome = best
    pick = Pick("draw" if side == "Draw" else "ml", side, price, price, *evaluate(outcome, price))
    model_p = float((outcome > 0).mean())
    if pick.edge < v.ml_edge:
        pick.vetoes.append(f"edge {pick.edge:+.1%} below {v.ml_edge:.0%}")
    if side != "Draw" and not (v.ml_min < price < v.ml_max):
        pick.vetoes.append(f"price {price:+.0f} outside {v.ml_min:+.0f}/{v.ml_max:+.0f}")
    if abs(model_p - fair[side]) > v.ml_prob_gap:
        pick.vetoes.append(f"suspicious edge: model {model_p:.0%} vs market {fair[side]:.0%}")
    return pick


def _correlation_veto(picks: list[Pick]) -> None:
    """Spread and moneyline on the same side are one bet twice: keep the stronger."""
    active = [p for p in picks if p.active and p.market in ("spread", "ml")]
    if len(active) == 2 and active[0].side == active[1].side:
        weaker = min(active, key=lambda p: p.edge)
        stronger = max(active, key=lambda p: p.edge)
        weaker.vetoes.append(f"correlated with {stronger.market} on {stronger.side}")


def _stability_veto(pick: Pick, n: int) -> None:
    se = math.sqrt(max(pick.prob * (1 - pick.prob), 1e-9) / n)
    if pick.active and pick.edge < 3 * se:
        pick.vetoes.append("simulation instability")


def market_implied(sport: Sport, odds: Odds) -> tuple[float, float] | None:
    """Expected (home, away) score implied by the market: total plus spread, or moneyline for low-scoring sports."""
    if odds.total is None:
        return None
    if odds.spread is not None and sport.key not in ("nhl", "mlb"):
        margin = -odds.spread
    elif odds.home_ml and odds.away_ml:
        p_home = no_vig(odds.home_ml, odds.away_ml)[0]
        p_home = min(max(p_home, 0.02), 0.98)
        margin = sport.margin_sd * float(np.sqrt(2)) * float(_erfinv(2 * p_home - 1))
    else:
        return None
    return (odds.total + margin) / 2, (odds.total - margin) / 2


def _erfinv(y: float) -> float:
    # Winitzki's approximation, refined with two Newton steps.
    a = 0.147
    ln = math.log(1 - y * y)
    t = 2 / (math.pi * a) + ln / 2
    x = math.copysign(math.sqrt(math.sqrt(t * t - ln / a) - t), y)
    for _ in range(2):
        x -= (math.erf(x) - y) / (2 / math.sqrt(math.pi) * math.exp(-x * x))
    return x


def predict(sport: Sport, history: list[Game], slate: list[Game], as_of: datetime, simulations: int = 20000,
            ctx: dict | None = None, rng: np.random.Generator | None = None,
            veto: VetoParams | None = None, model_weight: float | None = None) -> list[Prediction]:
    rng = rng or np.random.default_rng()
    ctx = ctx or {}
    if veto is not None:
        sport = Sport(**{**sport.__dict__, "veto": veto})
    pool = team_filter(sport, history + slate)
    ratings = fit_ratings([g for g in history if g.start < as_of], sport.rating, as_of=as_of, team_filter=pool)

    games = [g for g in slate if g.odds]
    if pool:  # college: FBS vs FBS only
        games = [g for g in games if pool(g.home) == g.home and pool(g.away) == g.away]
    games = [g for g in games if g.home in ratings.offense and g.away in ratings.offense]
    if sport.max_games:
        games.sort(key=lambda g: ratings.strength(g.home) + ratings.strength(g.away), reverse=True)
        games = games[: sport.max_games]

    out = []
    for game in games:
        # 1-6: frozen projection, no prices involved.
        home_exp, away_exp = ratings.expected(game.home, game.away, game.neutral)
        pure = sport.simulate(home_exp, away_exp, game, ctx, simulations, rng)
        pred = Prediction(game, home_exp, away_exp, model_margin=float(np.median(pure.margin)),
                          model_total=float(pure.total.mean()), home_win=pure.home_win(), draw=pure.draw())

        # Calibration: shrink the frozen projection toward the market's implied score by the
        # walk-forward-fitted weight, so ordinary model noise isn't mistaken for an edge.
        w = sport.model_weight if model_weight is None else model_weight
        implied = market_implied(sport, game.odds) if w < 1 else None
        sim = pure
        if implied:
            h = max(0.05, w * home_exp + (1 - w) * implied[0])
            a = max(0.05, w * away_exp + (1 - w) * implied[1])
            sim = sport.simulate(h, a, game, ctx, simulations, rng)
        if sport.key == "mlb" and game.info.get("home_pitcher"):
            pred.notes.append(f"SP: {game.info.get('away_pitcher')} vs {game.info.get('home_pitcher')}")
        if sim.reg_tie is not None and sport.key == "nhl":
            pred.notes.append(f"regulation tie {sim.reg_tie.mean():.0%}")

        # 7-9: only now look at the market, then run the veto stack.
        odds = game.odds
        if odds.spread is not None:
            pred.picks.append(_spread_pick(sim, game, odds, sport))
        if odds.total is not None:
            pred.picks.append(_total_pick(sim, odds, sport))
        if odds.home_ml and odds.away_ml:
            pred.picks.append(_ml_pick(sim, game, odds, sport))
        shared = sport_vetoes(sport, game, ratings)
        for pick in pred.picks:
            pick.vetoes.extend(shared)
            _stability_veto(pick, simulations)
        _correlation_veto(pred.picks)
        out.append(pred)
    return out


def grade(pick: Pick, game: Game) -> float:
    """Units won per unit staked (0 for a push; quarter lines can return halves)."""
    margin = game.home_score - game.away_score
    total = game.home_score + game.away_score
    if pick.market == "spread":
        diff = margin if pick.side == game.home else -margin
        result = float(settle(np.array([diff]), pick.line)[0])
    elif pick.market == "total":
        result = float(settle(np.array([total if pick.side == "Over" else -total]),
                              -pick.line if pick.side == "Over" else pick.line)[0])
    elif pick.market == "draw":
        result = 1.0 if margin == 0 else -1.0
    else:
        won = margin > 0 if pick.side == game.home else margin < 0
        result = 1.0 if won else -1.0
    if result > 0:
        b = (pick.price / 100) if pick.price > 0 else (100 / -pick.price)
        return result * b
    return result
