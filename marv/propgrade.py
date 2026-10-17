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


# =====================================================================================================================
# Prop grade v2 (Marvin 2026-10-16): a true per-prop 👍 / neutral / 👎 from player/prop-level inputs.
# SHADOW ONLY by default: graded + logged by marv/propgrade_shadow.py (state/propgrade/), never a Telegram signal and
# never a Kalshi filter unless MARV_PROPGRADE_LIVE=1 (default OFF; the Kalshi demo prop_feed is NOT wired to it yet).
#
# Inputs per prop: market no-vig probability for the side (price), Marv's gradient-boosted projection P(over) (usage,
# opponent defense vs position, team implied points, home/roof/wind), the player's recent-form P(over) (p_over above),
# opponent defense ratio, usage trend (last 3 vs weighted), team implied points (market pace/script), and the Marv
# game-script read from /opt/marv-feed/game_script.read (Grind/Shootout + PPE pace). The script read is applied as a
# probability nudge capped at +-PROP_CAP (same cap as game_script.PROP_CAP), so props can't double-count the total.
# Logistic weights are fit on real 2025 NFL lines (research/propgrade_v2); a 👍 needs EV >= +3% at the actual price
# AND Marv's projection or the player's form on that side. Same-game cap: <= 2 over 👍 per game (cap_same_game_overs).
# Backtest (unseen 2025-10-16..11-10, 3,216 real lines): 👍 49.3% ROI -5.3% (n=288), so it is NOT proven on its own;
# see ANALYSIS.md "Prop grade v2". NFL only (pass/rush/rec yds, receptions); other sports/markets return neutral.
# =====================================================================================================================
import json as _json
import os as _os

GRADE_MODEL_PATH = Path(__file__).with_name("propgrade_model.json")
GRADE_LIVE = _os.environ.get("MARV_PROPGRADE_LIVE", "0").lower() in ("1", "on", "true", "yes")
PROP_CAP = 0.03          # = /opt/marv-feed/game_script.PROP_CAP
GRADE_MARKETS = {"player_pass_yds": "pass", "player_rush_yds": "rush", "player_reception_yds": "rec",
                 "player_receptions": "receptions"}
THUMBS = {"up": "👍", "neutral": "➖", "down": "👎"}
_GM = None


def grade_model() -> dict:
    global _GM
    if _GM is None:
        _GM = _json.loads(GRADE_MODEL_PATH.read_text())
    return _GM


def _lg(p: float) -> float:
    p = min(0.98, max(0.02, float(p)))
    return math.log(p / (1 - p))


def _clip(x: float, a: float = 1.0) -> float:
    return max(-a, min(a, float(x)))


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def grade_features(side: str, nv_side: float, pm_over: float, pf_over: float | None = None, def_ratio: float | None = None,
                   usage_trend: float | None = None, implied_pts: float | None = None) -> dict:
    """Signed features for the side (positive = favours this side). Same definitions as research/propgrade_v2."""
    s = 1.0 if side.lower() == "over" else -1.0
    pm = pm_over if s > 0 else 1 - pm_over
    pf = pm if _nan(pf_over) else (pf_over if s > 0 else 1 - pf_over)
    return {"f_model": _clip(_lg(pm) - _lg(nv_side), 1.5), "f_form": _clip(_lg(pf) - _lg(nv_side), 1.5),
            "f_def": s * _clip((1.0 if _nan(def_ratio) else def_ratio) - 1.0, 0.5),
            "f_usage": s * _clip(0.0 if _nan(usage_trend) else usage_trend, 0.5),
            "f_team": s * _clip(((22.0 if _nan(implied_pts) else implied_pts) - 22.0) / 7.0, 1.5),
            "under": 1.0 if s < 0 else 0.0, "f_mkt": _lg(nv_side)}


def prop_grade(side: str, price: float, nv_side: float, pm_over: float, pf_over: float | None = None,
               def_ratio: float | None = None, usage_trend: float | None = None, implied_pts: float | None = None,
               script: dict | None = None, model: dict | None = None) -> dict:
    """👍 / neutral / 👎 for one prop side at an American `price`; nv_side = market no-vig P(side).
    script = game_script.read(...) dict (uses prop_shift_over, already capped at PROP_CAP) or None."""
    m = model or grade_model()
    f = grade_features(side, nv_side, pm_over, pf_over, def_ratio, usage_trend, implied_pts)
    z = m["intercept"] + sum(c * f[k] for k, c in zip(m["feats"], m["coef"]))
    p = 1 / (1 + math.exp(-z))
    shift = 0.0
    if script and script.get("prop_shift_over") is not None:
        shift = (1 if side.lower() == "over" else -1) * _clip(script["prop_shift_over"], PROP_CAP)
    p = min(0.99, max(0.01, p + shift))
    pay = price / 100 if price > 0 else 100 / -price
    ev = p * (1 + pay) - 1
    g = "up" if ev >= m["t_up"] else ("down" if ev <= m["t_dn"] else "neutral")
    why = []
    if g == "up" and f["f_form"] <= 0 and f["f_model"] <= 0:
        g = "neutral"; why.append("no player-level support (form and projection both at/against the price)")
    why.append(f"proj {'for' if f['f_model'] > 0 else 'against'}, form {'for' if f['f_form'] > 0 else 'against'}, "
               f"matchup {'for' if f['f_def'] > 0 else 'against' if f['f_def'] < 0 else 'flat'}")
    if script and script.get("grade"):
        why.append(f"script {script['grade']} ({shift:+.3f})")
    return {"grade": g, "thumb": THUMBS[g], "p": round(p, 4), "ev": round(ev, 4), "nv_side": round(float(nv_side), 4),
            "script_shift": round(shift, 4), "features": {k: round(v, 4) for k, v in f.items()}, "why": "; ".join(why),
            "live": GRADE_LIVE}


def cap_same_game_overs(items: list[dict], max_overs: int = 2) -> list[dict]:
    """items: dicts with game, side, grade, ev. Keeps the best `max_overs` over 👍 per game; the rest -> neutral."""
    seen: dict = {}
    for it in sorted(items, key=lambda x: -(x.get("ev") or 0)):
        if it.get("grade") == "up" and str(it.get("side", "")).lower() == "over":
            seen[it.get("game")] = seen.get(it.get("game"), 0) + 1
            if seen[it["game"]] > max_overs:
                it["grade"], it["thumb"] = "neutral", THUMBS["neutral"]
                it["why"] = (it.get("why") or "") + f"; same-game over cap ({max_overs})"
    return items
