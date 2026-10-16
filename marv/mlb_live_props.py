"""Marv Predict MLB: in-game (live) run-total pricer — PAPER / SHADOW ONLY (Marvin 2026-10-16).

Prices live MLB totals / team totals from Marv pregame home_exp/away_exp (predictions.json) plus live
inning and score:

  remaining runs per team ~ Poisson(exp_team * rem_frac(inning))

  rem_frac(i)  share of a game's runs still to come from the start of inning i (1..9 linear;
               extras keep a small tail). Lean default for shadow tracking — no unproven % shown.

Markets priced: match total over/under, team total over/under. Player in-game props are NOT priced
here (no per-player remaining model); March_edge only shadows Bovada quotes that this endpoint can
price. No network, no Telegram, no odds: March_edge passes Bovada prices and does EV maths
(ajbet/mlb_live_props.py).
"""

from __future__ import annotations

import math
from pathlib import Path

from . import bridge
from .data.teams import similarity

VERSION = "mlb-live-props-1.0"
LINES_TOTAL = [x / 2 for x in range(5, 31)]   # 2.5 .. 15.0
LINES_TEAM = [x / 2 for x in range(1, 21)]    # 0.5 .. 10.0
END_INNING = 9.0
EXTRA_FRAC = 0.06


def rem_frac(inning: float, end: float = END_INNING) -> float:
    """Share of regulation runs still to come at the start of `inning` (ESPN period = inning)."""
    inn = max(1.0, float(inning))
    if inn > end:
        return EXTRA_FRAC
    return max(0.0, (end - inn + 1.0) / end)


def remaining_lambdas(home_exp: float, away_exp: float, hg: int, ag: int, inning: float) -> tuple[float, float]:
    """Remaining runs; level-shift lightly if scoring so far is far from expectation-so-far."""
    fr = rem_frac(inning)
    exp_so_far = max((home_exp + away_exp) * (1.0 - fr), 1e-6)
    have = max(hg + ag, 0)
    # mild Bayesian nudge (k large => stay near pregame); keeps shadow stable
    k = 12.0
    lvl = (k + have) / (k + exp_so_far)
    lvl = max(0.5, min(1.8, lvl))
    return max(0.05, home_exp * fr * lvl), max(0.05, away_exp * fr * lvl)


def _cdf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    return sum(math.exp(-lam + i * math.log(max(lam, 1e-12)) - math.lgamma(i + 1)) for i in range(k + 1))


def over_under(have: int, lam: float, line: float) -> dict:
    need = line - have
    if need < 0:
        return {"over": 1.0, "push": 0.0, "under": 0.0}
    if float(need).is_integer():
        u = _cdf(int(need) - 1, lam)
        pu = _cdf(int(need), lam) - u
        return {"over": 1 - u - pu, "push": pu, "under": u}
    u = _cdf(int(math.floor(need)), lam)
    return {"over": 1 - u, "push": 0.0, "under": u}


def price_state(home_exp: float, away_exp: float, hg: int, ag: int, inning: float) -> dict:
    lh, la = remaining_lambdas(home_exp, away_exp, hg, ag, inning)
    r4 = lambda d: {k: round(v, 5) for k, v in d.items()}
    tot = {f"{ln:g}": r4(over_under(hg + ag, lh + la, ln)) for ln in LINES_TOTAL}
    team = {"home": {f"{ln:g}": r4(over_under(hg, lh, ln)) for ln in LINES_TEAM},
            "away": {f"{ln:g}": r4(over_under(ag, la, ln)) for ln in LINES_TEAM}}
    return {"rem_home": round(lh, 4), "rem_away": round(la, 4), "totals": tot, "team_totals": team}


def live_prices(state_dir: Path, team: str | None, other: str | None, hg: int, ag: int,
                inning: float, text: str | None = None) -> dict:
    """Bridge entry: March_edge passes home (team) / away (other), score and inning."""
    rec = bridge.find_game(state_dir, "mlb", team or "", other)
    if rec is None and text:
        # last-ditch: scan predictions for both names in free text
        path = state_dir / "predictions.json"
        if path.exists():
            import json
            data = json.loads(path.read_text())
            best, sc = None, 0.0
            for r in data.values():
                if r.get("sport") != "mlb":
                    continue
                s = max(similarity(r["home"], text), similarity(r["away"], text))
                if s > sc:
                    best, sc = r, s
            rec = best if sc >= 0.6 else None
    if not rec:
        return {"found": False, "why": f"no Marv MLB projection for {other} @ {team}"}
    # caller's team/other may be flipped vs Marv home/away
    flip = similarity(team or "", rec["away"]) > similarity(team or "", rec["home"])
    home_exp = float(rec.get("away_exp") if flip else rec.get("home_exp") or 0.0)
    away_exp = float(rec.get("home_exp") if flip else rec.get("away_exp") or 0.0)
    if home_exp <= 0 or away_exp <= 0:
        tot = float(rec.get("model_total") or 8.0)
        home_exp = away_exp = tot / 2.0
    out = price_state(home_exp, away_exp, int(hg), int(ag), float(inning))
    out.update({"found": True, "sport": "mlb", "home": rec["home"], "away": rec["away"],
                "home_exp": round(home_exp, 3), "away_exp": round(away_exp, 3),
                "model_total": rec.get("model_total"), "inning": float(inning),
                "score": [int(hg), int(ag)], "version": VERSION, "mode": "shadow",
                "flip": flip})
    return out
