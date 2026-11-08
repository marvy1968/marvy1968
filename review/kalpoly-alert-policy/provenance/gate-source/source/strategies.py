"""Strategy A (favorite comeback) and Strategy B (soccer slow grind).

Each strategy has a cheap ESPN-only `is_candidate_*` prefilter (free) and an `evaluate_*`
step that needs Odds API data (costs credits)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import markets as mk
from . import mybook
from .espn import Game, Lines, soccer_minute, time_left_frac
from .models import (implied_goal_rate, norm_cdf, soccer_under_prob, spread_from_win_prob, stern_win_prob,
                     remaining_goal_rate)
from .oddsmath import (american_to_prob, devig_american, ml_move_points, prob_to_american,
                       stake_to_win)


@dataclass
class Signal:
    strategy: str
    market: str
    game: Game
    selection: str
    side: Optional[str]           # home/away for A, 'under' for B
    line: Optional[float]
    price: Optional[float]
    book: Optional[str]
    fair_p: float
    market_p: Optional[float]
    edge: Optional[float]
    value: bool
    triggers: List[str]
    stake: Optional[float]
    to_win: float
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        return f"{self.game.event_id}:{self.strategy}:{self.market}"


# ---------------------------------------------------------------- Strategy A

def strong_favorite(pre: Lines, cfg: Dict[str, Any]) -> Optional[str]:
    side = pre.fav_side()
    if side is None:
        return None
    ml, sp = pre.ml(side), pre.spread(side)
    if (ml is not None and ml <= cfg["strong_fav_ml"]) or (sp is not None and sp <= cfg["strong_fav_spread"]):
        return side
    return None


def fav_margin(game: Game, side: str) -> int:
    other = "away" if side == "home" else "home"
    return game.team(side).score - game.team(other).score


def is_candidate_fav(game: Game, cfg: Dict[str, Any], sport: Dict[str, Any]) -> Optional[str]:
    """ESPN-only prefilter: live game, strong pregame favorite trailing or tied."""
    if game.state != "in":
        return None
    side = strong_favorite(game.pregame, cfg)
    if side is None:
        return None
    if fav_margin(game, side) > cfg.get("candidate_max_fav_lead", 0):
        return None
    if time_left_frac(game, sport["periods"], sport["period_minutes"]) < cfg.get("min_time_left_frac", 0.0):
        return None
    if cfg.get("prefilter", False) and not prefilter_fav(game, side, cfg, sport):
        return None
    return side


def implied_moves(game: Game, side: str, sport: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Free estimate of how far the live line has moved, from ESPN data only.

    Uses ESPN's own live 'current' spread/ML when present, else the Stern model:
    implied live spread = -(margin + mu * frac_left), implied ML = fair price of the model prob."""
    pre_sp, pre_ml = game.pregame.spread(side), game.pregame.ml(side)
    fair = fair_fav_prob(game, side, sport)
    mu = -fair["pregame_spread_used"]
    live_sp = game.live.spread(side)
    if live_sp is None:
        live_sp = -(fair["margin"] + mu * fair["frac_left"])
    live_ml = game.live.ml(side)
    if live_ml is None:
        p = min(max(fair["fair_p"], 0.001), 0.999)
        live_ml = prob_to_american(p)
    return {"spread_move": None if pre_sp is None else live_sp - pre_sp,
            "ml_move": None if pre_ml is None else ml_move_points(pre_ml, live_ml),
            "ml_prob_drop": None if pre_ml is None else american_to_prob(pre_ml) - american_to_prob(live_ml),
            "live_spread": live_sp, "live_ml": live_ml}


def prefilter_fav(game: Game, side: str, cfg: Dict[str, Any], sport: Dict[str, Any]) -> bool:
    m = implied_moves(game, side, sport)
    if m["spread_move"] is not None and m["spread_move"] >= cfg["spread_move_trigger"] - cfg.get("prefilter_spread_slack", 3):
        return True
    thr = cfg.get("ml_prob_drop_trigger")
    if thr is not None:
        if m.get("ml_prob_drop") is not None and m["ml_prob_drop"] >= thr * cfg.get("prefilter_ml_frac", 0.8):
            return True
    elif m["ml_move"] is not None and cfg.get("ml_move_trigger") is not None and \
            m["ml_move"] >= cfg["ml_move_trigger"] * cfg.get("prefilter_ml_frac", 0.8):
        return True
    return False


FOOTBALL = {"nfl", "ncaaf"}


def stern_bumped(margin: float, spread: float, frac_left: float, sigma: float, bump: float = 0.0) -> float:
    """Stern with a constant bonus to the trailing favourite's expected final margin.

    Backtest (docs/backtest_results.md): favourites come back more often than Stern says; a
    +1.5..+2.5 pt bonus calibrates all four sports. FIT IN-SAMPLE, so treat as provisional."""
    if bump == 0.0 or frac_left <= 1e-9:
        return stern_win_prob(margin, spread, frac_left, sigma)
    mu = margin - spread * frac_left + bump
    return norm_cdf(mu / (sigma * frac_left ** 0.5))


def with_bump(sport: Dict[str, Any], cfg: Dict[str, Any], league: str) -> Dict[str, Any]:
    b = cfg.get("trailing_fav_bump", 0.0)
    b = b.get(league, b.get("default", 0.0)) if isinstance(b, dict) else b
    return dict(sport, _fav_bump=float(b or 0.0))


def fair_fav_prob(game: Game, side: str, sport: Dict[str, Any],
                  rating: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Stern win probability for the favourite.

    aj_edge v2: with ``rating`` (from the ratings model: {'margin_home', 'sigma'?, ...}) the
    pregame margin is the RATINGS margin (non-circular). Without it, the ESPN pregame spread /
    de-vigged ML is used; that prior is a sportsbook number, so the result is a 'price gap'
    view only (source = 'market_prior') and is never tagged as model value."""
    pre = game.pregame
    sigma = float(sport["sigma"])
    tl = time_left_frac(game, sport["periods"], sport["period_minutes"])
    m = fav_margin(game, side)
    try:            # Marvin 2026-10-03: credit the current drive's expected points (ESPN gamecast)
        from . import gamecast
        m = m + gamecast.margin_adj(game, side)
    except Exception:  # noqa: BLE001
        pass
    spread = pre.spread(side)
    if spread is None and pre.ml(side) is not None:
        other = "away" if side == "home" else "home"
        if pre.ml(other) is not None:
            p = devig_american([pre.ml(side), pre.ml(other)])[0]
            spread = spread_from_win_prob(p, sigma)
    bump = float(sport.get("_fav_bump", 0.0))
    out: Dict[str, Any] = {"frac_left": tl, "margin": m, "sigma": sigma, "market_prior_spread": spread,
                           "fav_bump": bump,
                           "market_prior_p": None if spread is None else stern_bumped(m, spread, tl, sigma, bump)}
    if rating is not None and rating.get("margin_home") is not None:
        mh = float(rating["margin_home"])
        fav_margin_pre = mh if side == "home" else -mh
        spread_used = -fav_margin_pre * float(rating.get("shrink", 1.0))
        out.update(fair_p=stern_bumped(m, spread_used, tl, sigma, bump), pregame_spread_used=spread_used,
                   prior_source="ratings", rating_margin_fav=fav_margin_pre,
                   rating_info={k: v for k, v in rating.items() if k != "margin_home"})
    else:
        out.update(fair_p=out["market_prior_p"] if out["market_prior_p"] is not None else 0.5,
                   pregame_spread_used=spread if spread is not None else 0.0, prior_source="market_prior")
    return out


def evaluate_fav(game: Game, side: str, odds_event: Dict[str, Any], cfg: Dict[str, Any],
                 sport: Dict[str, Any], to_win: float, aliases=None,
                 rating: Optional[Dict[str, Any]] = None, ratings_enabled: bool = False) -> Optional[Signal]:
    team = game.team(side)
    sel = mk.outcome_name_for(team.name, odds_event, aliases)
    if sel is None:
        return None
    h2h = mk.h2h_summary(odds_event, sel)
    spr = mk.spread_summary(odds_event, sel)
    pre_ml, pre_sp = game.pregame.ml(side), game.pregame.spread(side)
    triggers, d = [], {}
    if spr and pre_sp is not None:
        move = spr["median_point"] - pre_sp
        d.update(live_spread=spr["median_point"], spread_move=move, best_spread=spr)
        if move >= cfg["spread_move_trigger"]:
            triggers.append(f"spread moved {move:+.1f} pts toward dog ({pre_sp:+g} -> {spr['median_point']:+g})")
    if h2h and pre_ml is not None:
        pts = ml_move_points(pre_ml, h2h["median_price"])
        dp = american_to_prob(pre_ml) - american_to_prob(h2h["median_price"])
        d.update(live_ml=h2h["median_price"], ml_move=pts, ml_prob_move=dp)
        thr = cfg.get("ml_prob_drop_trigger")
        if thr is not None:
            if dp >= thr:
                triggers.append(f"ML implied prob dropped {dp * 100:.0f} pp ({pre_ml:+.0f} -> {h2h['median_price']:+.0f}; "
                                f"implied {american_to_prob(pre_ml):.0%} -> {american_to_prob(h2h['median_price']):.0%})")
        elif cfg.get("ml_move_trigger") is not None and pts >= cfg["ml_move_trigger"]:   # legacy rule
            triggers.append(f"ML improved {pts:.0f} pts ({pre_ml:+.0f} -> {h2h['median_price']:+.0f}; "
                            f"implied {american_to_prob(pre_ml):.0%} -> {american_to_prob(h2h['median_price']):.0%})")
    if not triggers or not h2h:
        return None
    sport = with_bump(sport, cfg, game.league)
    fair = fair_fav_prob(game, side, sport, rating)
    edge = fair["fair_p"] - h2h["market_p"]
    d.update(fair)
    d.update(pre_ml=pre_ml, pre_spread=pre_sp, books=h2h["books"], odds_event_id=odds_event.get("id"),
             model_version="aj_edge_live_v2" if fair["prior_source"] == "ratings" else "aj_edge_live_v1_market_prior")
    value = edge >= cfg["value_edge"]
    notes = []
    if fair["prior_source"] != "ratings":
        if not ratings_enabled or game.league in cfg.get("market_prior_value_leagues", ["nba", "ncaab"]):
            notes.append("market-prior price gap (no ratings model for this game)")
        else:
            value = False
            notes.append("no independent ratings for this game: market-prior Stern shown, not tagged value")
    elif game.league in FOOTBALL and fair["frac_left"] < cfg.get("football_min_frac_left", 0.25):
        value = False
        notes.append("football with < 25% left: Stern is weakest late (no possession/field-position terms)")
    if value and game.season_type == 3:
        notes.append("low confidence (playoffs)")
    d["notes"] = notes
    d["my_book"], d["my_price"] = mybook.key(), h2h.get("my_price")
    return Signal(
        strategy="fav_comeback", market="h2h", game=game, selection=team.name, side=side, line=None,
        price=h2h["best_price"], book=h2h["best_book"], fair_p=fair["fair_p"], market_p=h2h["market_p"],
        edge=edge, value=value, triggers=triggers,
        stake=stake_to_win(h2h["best_price"], to_win), to_win=to_win, details=d)


# ---------------------------------------------------------------- Strategy B

def half_line(x: float) -> float:
    """Round up to the nearest x.5 line (7 -> 7.5, 7.5 -> 7.5, 7.6 -> 8.5)."""
    f = math.floor(x)
    return f + 0.5 if x - f <= 0.5 else f + 1.5


def suggested_under(live_total: float, goals: int, cfg: Dict[str, Any]) -> float:
    return half_line(max(live_total + cfg["buffer_over_live_total"], goals + cfg["buffer_over_goals"]))


def soccer_minutes_left(game: Game, cfg: Dict[str, Any]) -> float:
    return max(cfg["match_minutes"] - soccer_minute(game), 0.0) + cfg.get("stoppage_minutes", 0)


def implied_live_total(game: Game, cfg: Dict[str, Any]) -> float:
    """Free estimate of the live total: goals + pregame-rate x time left (or ESPN 'current')."""
    if game.live.total is not None:
        return game.live.total
    return game.total_goals + remaining_goal_rate(game.pregame.total, soccer_minutes_left(game, cfg),
                                                  cfg["match_minutes"])


def is_candidate_soccer(game: Game, cfg: Dict[str, Any]) -> bool:
    """ESPN-only prefilter: early goals, or ESPN's own live total already inflated."""
    if game.state != "in" or game.pregame.total is None:
        return False
    early = game.total_goals >= cfg["early_goals_min"] and soccer_minute(game) <= cfg["max_minute"]
    inflated = (game.live.total is not None and
                game.live.total >= game.pregame.total + cfg["total_inflation_trigger"])
    if not (early or inflated):
        return False
    if cfg.get("prefilter", False):
        need = game.pregame.total + cfg["total_inflation_trigger"] - cfg.get("prefilter_slack", 0.5)
        return implied_live_total(game, cfg) >= need
    return True


def soccer_trigger(game: Game, odds_event: Optional[Dict[str, Any]], cfg: Dict[str, Any]) -> Optional[float]:
    """Return the live total line if the inflation trigger fires (Odds API, ESPN fallback)."""
    live_total = None
    if odds_event:
        t = mk.totals_summary(odds_event)
        live_total = t["median_point"] if t else None
    if live_total is None:
        live_total = game.live.total
    if live_total is None or game.pregame.total is None:
        return None
    return live_total if live_total >= game.pregame.total + cfg["total_inflation_trigger"] else None


def evaluate_soccer(game: Game, live_total: float, alt_event: Optional[Dict[str, Any]],
                    cfg: Dict[str, Any], to_win: float, main_event: Optional[Dict[str, Any]] = None) -> Signal:
    goals = game.total_goals
    target = suggested_under(live_total, goals, cfg)
    mins_left = soccer_minutes_left(game, cfg)
    offer = mk.best_under(alt_event, target) if alt_event else None
    if offer is None and main_event:        # books often skip alt totals live: try the main totals line
        offer = mk.best_under(main_event, target, market="totals")
    line = offer["line"] if offer else target
    rate, rate_src = (implied_goal_rate(game.pregame.total, game.pregame.total_over_price,
                                        game.pregame.total_under_price)
                      if cfg.get("rate_from_price", True) else (game.pregame.total, "line"))
    fair_p = soccer_under_prob(line, goals, rate, mins_left, cfg["match_minutes"])
    market_p = edge = None
    if offer:
        if offer.get("paired_over") is not None:
            market_p = devig_american([offer["price"], offer["paired_over"]])[0]
        else:
            market_p = american_to_prob(offer["price"])  # one-sided: vig not removable
        edge = fair_p - market_p
    lam = remaining_goal_rate(rate, mins_left, cfg["match_minutes"])
    fair_odds = prob_to_american(min(fair_p, 1 - 1e-6))
    d = dict(pregame_total=game.pregame.total, pregame_rate=rate, rate_source=rate_src,
             pregame_over_price=game.pregame.total_over_price, pregame_under_price=game.pregame.total_under_price,
             live_total=live_total, goals=goals, target_line=target,
             minutes_left=mins_left, lam=lam, fair_odds=fair_odds, offered=bool(offer),
             paired_over=offer.get("paired_over") if offer else None,
             stake_to_win_20=stake_to_win(offer["price"], 20) if offer else None,
             my_book=mybook.key(), my_price=offer.get("my_price") if offer else None)
    trig = [f"live total {live_total:g} >= pregame {game.pregame.total:g} + {cfg['total_inflation_trigger']:g} "
            f"({goals} goals by {soccer_minute(game):.0f}')"]
    return Signal(
        strategy="soccer_grind", market="alternate_totals", game=game, selection=f"Under {line:g}",
        side="under", line=line, price=offer["price"] if offer else None,
        book=offer["book"] if offer else None, fair_p=fair_p, market_p=market_p, edge=edge,
        value=bool(edge is not None and edge >= cfg["value_edge"]), triggers=trig,
        stake=stake_to_win(offer["price"], to_win) if offer else None, to_win=to_win, details=d)
