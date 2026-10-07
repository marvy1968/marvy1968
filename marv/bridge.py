"""Bridge between Marv (predictions) and an odds-alert bot (e.g. March_edge).

Marv publishes every game it projects to state/predictions.json. The odds bot can then ask:
  * pregame: does Marv agree with this side, and what's its fair price?
  * live:    given the score and time left, what is the real chance this total / moneyline
             wins? (the honest replacement for "edge vs open", which ignores the score)

Use it three ways:
  python -m marv check --sport nfl --team Lions --market total --side under --line 67.5 \
      --price -110 --home-score 29 --away-score 19 --minutes-left 17.8
  python -m marv serve            # http://127.0.0.1:8787/check?... (same parameters)
  from marv.bridge import check   # inside the odds bot's own Python code
"""

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .data.teams import similarity
from .markets import american_to_prob

# Regulation length and full-game standard deviations (from the calibrated simulators).
GAME = {
    "nfl": {"minutes": 60, "margin_sd": 13.5, "total_sd": 14.0},
    "cfb": {"minutes": 60, "margin_sd": 16.0, "total_sd": 16.5},
    "nba": {"minutes": 48, "margin_sd": 13.0, "total_sd": 19.0},
    "wnba": {"minutes": 40, "margin_sd": 11.8, "total_sd": 17.0},
    "nhl": {"minutes": 60, "margin_sd": 2.4, "total_sd": 2.5},
    "mlb": {"minutes": 9, "margin_sd": 4.2, "total_sd": 4.3},  # "minutes" = innings for baseball
    "soccer": {"minutes": 90, "margin_sd": 1.6, "total_sd": 1.6},
}
MIN_EDGE = 0.04  # fair probability must beat the price's implied probability by this much


def export(state_dir: Path, sport: str, preds) -> None:
    """Merge this run's projections into state/predictions.json (kept for 3 days)."""
    path = state_dir / "predictions.json"
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except json.JSONDecodeError:
        data = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    data = {k: v for k, v in data.items() if v.get("start", "") >= cutoff}
    for p in preds:
        g = p.game
        qualified = [f"{pk.market}:{pk.side}" for pk in p.picks if pk.active]
        data[f"{sport}:{g.id}"] = {
            "sport": sport, "game_id": g.id, "start": g.start.isoformat(), "home": g.home, "away": g.away,
            "home_exp": round(p.home_exp, 2), "away_exp": round(p.away_exp, 2),
            "model_total": round(p.model_total, 2), "model_margin": round(p.model_margin, 2),
            "home_win": round(p.home_win, 4), "qualified": qualified, "notes": p.notes,
            "updated": datetime.now(timezone.utc).isoformat(),
        }
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def find_game(state_dir: Path, sport: str, team: str, other: str | None = None) -> dict | None:
    path = state_dir / "predictions.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    best, best_score = None, 0.0
    for rec in data.values():
        if rec["sport"] != sport:
            continue
        score = max(similarity(team, rec["home"]), similarity(team, rec["away"]))
        if other:
            score = min(score, max(similarity(other, rec["home"]), similarity(other, rec["away"])))
        if score > best_score:
            best, best_score = rec, score
    return best if best_score >= 0.6 else None


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


@dataclass
class Verdict:
    found: bool
    fair_prob: float | None = None
    implied_prob: float | None = None
    edge: float | None = None
    agrees: bool = False
    reason: str = ""
    game: str = ""

    def line(self) -> str:
        if not self.found:
            return f"Marv: {self.reason}"
        mark = "✅ agrees" if self.agrees else "🚫 disagrees"
        return (f"Marv {mark}: fair {self.fair_prob:.0%} vs price {self.implied_prob:.0%} "
                f"(edge {self.edge:+.0%}) · {self.reason}")


def check(state_dir: Path, sport: str, team: str, market: str, side: str, price: float,
          line: float | None = None, other: str | None = None, home_score: float | None = None,
          away_score: float | None = None, minutes_left: float | None = None) -> Verdict:
    """Fair probability for one alert. market: 'total' (side over/under) or 'ml' (side = team name).

    With home_score/away_score/minutes_left it's a live check: Marv's projected scoring rate is
    applied only to the time remaining, on top of the current score."""
    rec = find_game(state_dir, sport, team, other)
    if rec is None:
        return Verdict(False, reason="no Marv projection for this game (run marv first)")
    cfg = GAME.get(sport, GAME["nfl"])
    live = home_score is not None and away_score is not None and minutes_left is not None
    frac = min(max(minutes_left / cfg["minutes"], 0.0), 1.0) if live else 1.0
    cur_total = (home_score + away_score) if live else 0.0
    cur_margin = (home_score - away_score) if live else 0.0
    rate = rec["model_total"]
    if live and frac < 0.9:  # blend in how fast this game is actually being scored (25% weight)
        observed = cur_total / (1 - frac)
        rate = 0.75 * rec["model_total"] + 0.25 * observed
    exp_total = cur_total + rate * frac
    exp_margin = cur_margin + rec["model_margin"] * frac
    sd_t = max(cfg["total_sd"] * math.sqrt(max(frac, 1e-6)), 0.5)
    sd_m = max(cfg["margin_sd"] * math.sqrt(max(frac, 1e-6)), 0.5)

    if market == "total":
        if line is None:
            return Verdict(False, reason="total check needs --line")
        p_over = 1 - _norm_cdf((line - exp_total) / sd_t)
        fair = p_over if side.lower().startswith("o") else 1 - p_over
        detail = f"projected final total {exp_total:.1f}"
    elif market == "ml":
        p_home = 1 - _norm_cdf(-exp_margin / sd_m) if live else rec["home_win"]
        is_home = similarity(side, rec["home"]) >= similarity(side, rec["away"])
        fair = p_home if is_home else 1 - p_home
        leader = rec["home"] if exp_margin > 0 else rec["away"]
        detail = f"projected {leader} by {abs(exp_margin):.1f}"
    else:
        return Verdict(False, reason=f"market '{market}' not supported (use total or ml)")

    implied = american_to_prob(price)
    edge = fair - implied
    agrees = edge >= MIN_EDGE
    when = "live" if live else "pregame"
    return Verdict(True, fair, implied, edge, agrees, f"{when}, {detail}", f"{rec['away']} @ {rec['home']}")


def verdict_json(v: Verdict) -> str:
    return json.dumps(asdict(v))
