"""Quick second opinion on any NFL player prop: Marv's probability, fair price, edge and a letter grade.

Marv's own pregame prop model did not beat real sportsbook lines (2025: 49% over 3,040 bets, ROI -5%, see
ANALYSIS.md), so this is a sanity check, not a signal. It uses only the player's recent games (this and last
season, most recent weighted most, half-life 4 games): the weighted spread of his actual results gives the
chance of clearing the line, with a smoothing kernel so a line between his usual numbers isn't a cliff.
Opponent, game script and injuries are NOT in it, so the grade says when those could matter.

Grades are capped at B for that reason:
  B  Marv's chance beats the price by 4+ points      C  within -2..+4 of the price (fair)
  D  price is 2-6 points worse than Marv's chance    F  more than 6 worse (the price is poor)
"""

import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import edges as E
from .props import nfl as P
from .props.odds import norm_name

MARKETS = {
    "pass": ("passing_yards", "attempts", 10, "passing yards"),
    "rush": ("rushing_yards", "carries", 3, "rushing yards"),
    "rec": ("receiving_yards", "targets", 1, "receiving yards"),
    "receptions": ("receptions", "targets", 1, "receptions"),
}
ALIAS = {"pass": "pass", "pass_yds": "pass", "passing": "pass", "qb": "pass", "rush": "rush", "rush_yds": "rush",
         "rushing": "rush", "rec": "rec", "rec_yds": "rec", "receiving": "rec", "yards": "rec", "receptions": "receptions",
         "reception": "receptions", "catches": "receptions", "rec_receptions": "receptions"}
HALF_LIFE = 4.0
MIN_GAMES = 6


def _cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def p_over(values: np.ndarray, weights: np.ndarray, line: float) -> float:
    """Weighted, smoothed P(result > line); a whole-number line pushes, so it's judged at line + 0.5."""
    t = line + 0.5 if float(line).is_integer() else line
    mu = np.average(values, weights=weights)
    sd = math.sqrt(np.average((values - mu) ** 2, weights=weights))
    h = max(0.25 * sd, 0.75)
    return float(np.sum(weights * (1 - np.array([_cdf((t - v) / h) for v in values]))) / weights.sum())


def history(players: pd.DataFrame, player: str, market: str) -> pd.DataFrame | None:
    stat, usage, min_usage, _ = MARKETS[market]
    p = players[players["player_display_name"].map(norm_name) == norm_name(player)]
    if p.empty:  # last-name / partial match
        want = norm_name(player)
        cand = players[players["player_display_name"].map(lambda n: want in norm_name(n) or norm_name(n) in want)]
        if cand["player_display_name"].nunique() == 1:
            p = cand
    if p.empty:
        return None
    p = p[p[usage].fillna(0) >= min_usage].sort_values(["season", "week"], ascending=False)
    return p.head(16)


def grade_prop(players: pd.DataFrame, player: str, market: str, side: str, line: float, price: float,
               other_price: float | None = None) -> dict:
    market = ALIAS.get(market.lower().replace(" ", "_"), market.lower())
    if market not in MARKETS:
        return {"ok": False, "reason": f"unknown market '{market}' (use pass, rush, rec or receptions)"}
    side = side.capitalize()
    if side not in ("Over", "Under"):
        return {"ok": False, "reason": "side must be over or under"}
    h = history(players, player, market)
    stat, _, _, label = MARKETS[market]
    if h is None or len(h) < MIN_GAMES:
        n = 0 if h is None else len(h)
        return {"ok": False, "reason": f"only {n} recent games for {player} in the data (need {MIN_GAMES})"}
    values = h[stat].fillna(0).to_numpy(float)
    weights = 0.5 ** (np.arange(len(values)) / HALF_LIFE)
    po = p_over(values, weights, float(line))
    p = po if side == "Over" else 1 - po
    implied = E.implied(price)
    novig = None
    if other_price is not None:
        a, b = E.devig([price, other_price])[:2]
        novig = a
    ref = novig if novig is not None else implied
    edge = p - implied
    if edge >= 0.04:
        letter = "B"
    elif edge >= -0.02:
        letter = "C"
    elif edge >= -0.06:
        letter = "D"
    else:
        letter = "F"
    notes = []
    if abs(p - ref) > 0.10:
        notes.append("Marv and the market are more than 10 points apart: that usually means the market knows "
                     "something this form-only view doesn't (matchup, injuries, role, game script)")
    if len(h) < 10:
        notes.append(f"only {len(h)} recent games, treat the number as rough")
    last5 = float(np.mean(values[:5]))
    return {"ok": True, "player": h["player_display_name"].iloc[0], "market": market, "label": label, "side": side,
            "line": float(line), "price": float(price), "p": round(p, 4), "fair": round(E.to_american(p)),
            "implied": round(implied, 4), "novig": None if novig is None else round(novig, 4), "edge": round(edge, 4),
            "grade": letter, "games": len(h), "last5": round(last5, 1), "weighted": round(float(np.average(values, weights=weights)), 1),
            "median": float(np.median(values)), "notes": notes}


def text(g: dict) -> str:
    if not g["ok"]:
        return f"👽 Marv can't grade that prop: {g['reason']}"
    lines = [f"<b>{g['player']} {g['side'].upper()} {g['line']:g} {g['label']} ({g['price']:+.0f})</b>",
             f"Marv: {g['p']:.0%} chance (fair {g['fair']:+d}) vs price {g['implied']:.0%}"
             + (f", market no-vig {g['novig']:.0%}" if g["novig"] else "") + f" → edge {g['edge']:+.1%}",
             f"Form: weighted avg {g['weighted']:g}, last 5 avg {g['last5']:g}, median {g['median']:g} over {g['games']} games",
             f"<b>Grade {g['grade']}</b>: " + {"B": "price beats Marv's number", "C": "fair price, no real edge",
                                               "D": "price is worse than Marv's number", "F": "poor price"}[g["grade"]]]
    lines += [f"⚠️ {n}" for n in g["notes"]]
    lines.append("<i>Form-only second opinion: Marv's prop model went 49% against real 2025 lines (ROI -5%), so use "
                 "this to catch bad prices and contradictory alerts, not as a pick. Grades stop at B.</i>")
    return "\n".join(lines)


def load(cache: Path, season: int) -> pd.DataFrame:
    return P.load_players(cache, [season - 1, season], season)


def parse_command(parts: list[str]) -> dict | None:
    """/grade Josh Allen pass over 245.5 -115 [-105]  ->  kwargs for grade_prop (or None if it doesn't parse)."""
    low = [p.lower() for p in parts]
    idx = next((i for i, p in enumerate(low) if p in ("over", "under", "o", "u")), None)
    if idx is None or idx < 2 or len(parts) < idx + 3:
        return None
    try:
        return {"player": " ".join(parts[:idx - 1]), "market": parts[idx - 1], "side": "over" if low[idx][0] == "o" else "under",
                "line": float(parts[idx + 1]), "price": float(parts[idx + 2]),
                "other_price": float(parts[idx + 3]) if len(parts) > idx + 3 else None}
    except ValueError:
        return None
