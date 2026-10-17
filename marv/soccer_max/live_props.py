"""Marv Predict soccer: in-game (live) prop pricer — PAPER / SHADOW ONLY.

Prices live soccer markets from Marv's pregame expected goals (engine.expected_goals on the saved, point-in-time
ratings) plus the live score and minute:

  remaining goals per team ~ Poisson(xG_team * rem_frac(minute) * state_mult(team lead/trail))

  rem_frac(m)   share of a match's goals still to come after minute m. Goal intensity is flat within each half;
                the 2nd half (45'..93' incl. stoppage) carries F2 of the goals (fit on HT/FT results, see
                research/soccer_live_props/calib.py; Marvin 2026-10-16).
  level         gamma-Poisson update (bayes_k pseudo-goals) from goals so far vs Marv's expectation so far.
  state_mult    exp(beta_lead) for the team ahead, exp(beta_trail) for the team behind (fit on the same HT data).

Markets priced (lines at 0.5 steps; quarter lines = two half-stakes): match total over/under, team total
over/under, both teams to score. Anytime scorer is NOT priced (no player-share input and Bovada does not quote it
live through The Odds API). No network, no Telegram, no odds here: March_edge passes Bovada's prices and does the
EV maths itself on these probabilities (ajbet/soccer_live_props.py).
"""

import json
import math
from pathlib import Path

from . import engine as E
from . import node as NODE

VERSION = "soccer-live-props-1.0"
# Defaults = fit on 2019/20 .. Aug 2026 HT/FT (walk-forward Marv xG), overwritten by live_props_calib.json if present.
DEFAULTS = {"f2": 0.55, "beta_lead": 0.0, "beta_trail": 0.0, "end_minute": 93.0, "bayes_k": 1e9}
LINES_TOTAL = [x / 2 for x in range(1, 17)]       # 0.5 .. 8.0
LINES_TEAM = [x / 2 for x in range(1, 11)]        # 0.5 .. 5.0


def calib(state_dir: Path | None) -> dict:
    p = dict(DEFAULTS)
    if state_dir is not None:
        f = NODE.out_dir(state_dir) / "live_props_calib.json"
        try:
            p.update({k: float(v) for k, v in json.loads(f.read_text()).get("params", {}).items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
    return p


def rem_frac(minute: float, f2: float, end: float = 93.0, ht: bool = False) -> float:
    """Share of the match's goals still to come after `minute` (first half 0-45 carries 1-f2, second 45-end f2)."""
    if ht:
        return f2
    m = max(0.0, float(minute))
    if m < 45.0:
        return (1 - f2) * (45.0 - m) / 45.0 + f2
    return f2 * max(0.0, end - m) / max(end - 45.0, 1e-9)


def remaining_lambdas(home_xg: float, away_xg: float, hg: int, ag: int, minute: float, prm: dict,
                      ht: bool = False) -> tuple[float, float]:
    fr = rem_frac(minute, prm["f2"], prm.get("end_minute", 93.0), ht)
    k = float(prm.get("bayes_k", 1e9))
    if k < 1e8:  # gamma-Poisson: goals so far vs Marv's expectation so far move the game's scoring level
        exp_so_far = (home_xg + away_xg) * (1 - fr)
        lvl = (k + hg + ag) / (k + exp_so_far)
        home_xg, away_xg = home_xg * lvl, away_xg * lvl
    mh = prm["beta_lead"] if hg > ag else prm["beta_trail"] if hg < ag else 0.0
    ma = prm["beta_lead"] if ag > hg else prm["beta_trail"] if ag < hg else 0.0
    return home_xg * fr * math.exp(mh), away_xg * fr * math.exp(ma)


def _cdf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    return sum(math.exp(-lam + i * math.log(max(lam, 1e-12)) - math.lgamma(i + 1)) for i in range(k + 1))


def over_under(have: int, lam: float, line: float) -> dict:
    """Final count = have + Poisson(lam) vs a half/whole line -> {over, push, under}."""
    need = line - have
    if need < 0:
        return {"over": 1.0, "push": 0.0, "under": 0.0}
    if float(need).is_integer():
        u = _cdf(int(need) - 1, lam)
        pu = _cdf(int(need), lam) - u
        return {"over": 1 - u - pu, "push": pu, "under": u}
    u = _cdf(int(math.floor(need)), lam)
    return {"over": 1 - u, "push": 0.0, "under": u}


def price_state(home_xg: float, away_xg: float, hg: int, ag: int, minute: float, prm: dict, ht: bool = False) -> dict:
    lh, la = remaining_lambdas(home_xg, away_xg, hg, ag, minute, prm, ht)
    r4 = lambda d: {k: round(v, 5) for k, v in d.items()}
    tot = {f"{ln:g}": r4(over_under(hg + ag, lh + la, ln)) for ln in LINES_TOTAL}
    team = {"home": {f"{ln:g}": r4(over_under(hg, lh, ln)) for ln in LINES_TEAM},
            "away": {f"{ln:g}": r4(over_under(ag, la, ln)) for ln in LINES_TEAM}}
    ph = 1.0 if hg > 0 else 1 - math.exp(-lh)
    pa = 1.0 if ag > 0 else 1 - math.exp(-la)
    return {"rem_home": round(lh, 4), "rem_away": round(la, 4), "totals": tot, "team_totals": team,
            "btts": {"yes": round(ph * pa, 5), "no": round(1 - ph * pa, 5)}}


def live_prices(state_dir: Path, league: str, team: str | None, other: str | None, hg: int, ag: int,
                minute: float, ht: bool = False, text: str | None = None) -> dict:
    """Bridge entry: March_edge passes league key, home (team) / away (other), score and minute."""
    key, r, home, away = NODE.locate(state_dir, league, team, other, text)
    if not home:
        return {"found": False, "why": f"no Marv soccer ratings for {league} {other} @ {team}"}
    xg = E.expected_goals(r, home, away)
    prm = calib(state_dir)
    out = price_state(xg["home_xg"], xg["away_xg"], int(hg), int(ag), float(minute), prm, ht)
    out.update({"found": True, "league": key, "home": home, "away": away, "asof": r.get("asof"),
                "home_xg": round(xg["home_xg"], 3), "away_xg": round(xg["away_xg"], 3), "minute": float(minute),
                "score": [int(hg), int(ag)], "params": prm, "version": VERSION, "model_version": E.MODEL_VERSION,
                "mode": "shadow"})
    return out
