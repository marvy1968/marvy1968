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
    "ncaab": {"minutes": 40, "margin_sd": 11.5, "total_sd": 16.0},
    "ncaaw": {"minutes": 40, "margin_sd": 11.5, "total_sd": 15.0},
    "euroleague": {"minutes": 40, "margin_sd": 11.5, "total_sd": 15.5},
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


def live_state(rec: dict, sport: str, home_score: float | None = None, away_score: float | None = None,
               minutes_left: float | None = None) -> dict:
    """Projected final margin/total and win probability from Marv's pregame projection plus the
    current score and time left. Scoring for the rest of the game blends the pregame rate (75%)
    with this game's actual pace (25%); uncertainty shrinks with the square root of time left."""
    cfg = GAME.get(sport, GAME["nfl"])
    live = home_score is not None and away_score is not None and minutes_left is not None
    frac = min(max(minutes_left / cfg["minutes"], 0.0), 1.0) if live else 1.0
    cur_total = (home_score + away_score) if live else 0.0
    cur_margin = (home_score - away_score) if live else 0.0
    rate = rec["model_total"]
    if live and frac < 0.9:
        observed = cur_total / (1 - frac)
        rate = 0.75 * rec["model_total"] + 0.25 * observed
    exp_total = cur_total + rate * frac
    exp_margin = cur_margin + rec["model_margin"] * frac
    # Uncertainty shrinks a bit slower than sqrt(time): late-game variance (fouling, pace) stays high.
    sd_t = max(cfg["total_sd"] * max(frac, 1e-6) ** 0.4, 0.5)
    sd_m = max(cfg["margin_sd"] * max(frac, 1e-6) ** 0.4, 0.5)
    p_home = 1 - _norm_cdf(-exp_margin / sd_m) if live else rec["home_win"]
    return {"exp_total": exp_total, "sd_t": sd_t, "exp_margin": exp_margin, "sd_m": sd_m, "p_home": p_home}


def p_over(state: dict, line: float) -> float:
    return 1 - _norm_cdf((line - state["exp_total"]) / state["sd_t"])


def check(state_dir: Path, sport: str, team: str, market: str, side: str, price: float,
          line: float | None = None, other: str | None = None, home_score: float | None = None,
          away_score: float | None = None, minutes_left: float | None = None) -> Verdict:
    """Fair probability for one alert. market: 'total' (side over/under) or 'ml' (side = team name).

    With home_score/away_score/minutes_left it's a live check: Marv's projected scoring rate is
    applied only to the time remaining, on top of the current score."""
    rec = find_game(state_dir, sport, team, other)
    if rec is None:
        return Verdict(False, reason="no Marv projection for this game (run marv first)")
    live = home_score is not None and away_score is not None and minutes_left is not None
    st = live_state(rec, sport, home_score if live else None, away_score if live else None,
                    minutes_left if live else None)
    exp_total, sd_t, exp_margin = st["exp_total"], st["sd_t"], st["exp_margin"]

    if market == "total":
        if line is None:
            return Verdict(False, reason="total check needs --line")
        po = p_over(st, line)
        fair = po if side.lower().startswith("o") else 1 - po
        detail = f"projected final total {exp_total:.1f}"
    elif market == "ml":
        p_home = st["p_home"]
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


# ---------------------------------------------------------------- H2H overlay for March_edge alerts
# March_edge finds the bets; Marv only adds one line per alert: does the team head-to-head read (ratings
# margin, offense/defense category points, star matchup, O/U trend) back this bet? yes / no / mixed + why.
# A probability is printed only when the logic behind it passed the proven bar (marv/proven.py); otherwise
# the line says "unproven — no %". Marv never sends anything itself from here.

LEAGUE_TO_SPORT = {"ncaaf": "cfb", "cfb": "cfb", "americanfootball_ncaaf": "cfb", "nfl": "nfl",
                   "americanfootball_nfl": "nfl", "nba": "nba", "wnba": "wnba", "ncaab": "ncaab", "ncaaw": "ncaaw",
                   "mens-college-basketball": "ncaab", "womens-college-basketball": "ncaaw", "euroleague": "euroleague"}
H2H_DIR = "marv_predict"  # state/marv_predict/h2h_<sport>.json, written by tools/cfb_card.py (ratings-only card)


def _market_kind(market: str) -> str:
    m = (market or "").lower()
    if m in ("h2h", "ml", "moneyline"):
        return "ml"
    if m in ("spreads", "spread", "alternate_spreads"):
        return "spread"
    if m in ("totals", "total", "alternate_totals"):
        return "total"
    if "team_total" in m:
        return "team_total"
    if m.startswith(("player_", "batter_", "pitcher_")) or m == "prop":
        return "prop"
    return "other"


def _h2h_cards(state_dir: Path, sport: str) -> list[dict]:
    path = state_dir / H2H_DIR / f"h2h_{sport}.json"
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text())
        day = data.get("card_day")
        if day and (datetime.now(timezone.utc).date() - datetime.fromisoformat(day).date()).days > 1:
            return []  # stale card (last week's slate): no H2H read rather than a wrong one
        return list(data.get("games", []))
    except (ValueError, OSError):
        return []


def _in_text(name: str, text: str) -> float:
    """How well a team name shows up in free text (best match over word windows of the name's length)."""
    words = text.replace("@", " ").replace("|", " ").split()
    n = max(len(name.split()), 1)
    best = 0.0
    for k in (n, n + 1, n + 2):
        for i in range(len(words) - k + 1):
            best = max(best, similarity(name, " ".join(words[i:i + k])))
    return best


def find_game_in_text(state_dir: Path, sport: str, text: str) -> dict | None:
    """The Marv projection whose two teams both appear in an alert's text."""
    path = state_dir / "predictions.json"
    if not path.exists() or not text:
        return None
    best, score = None, 0.0
    for rec in json.loads(path.read_text()).values():
        if rec["sport"] != sport:
            continue
        s = min(_in_text(rec["home"], text), _in_text(rec["away"], text))
        if s > score:
            best, score = rec, s
    return best if score >= 0.75 else None


@dataclass
class Overlay:
    found: bool
    verdict: str = ""  # good | not | mixed | n/a
    why: str = ""
    prob: str = ""  # proven backtest text, or proven.UNPROVEN
    game: str = ""

    def line(self) -> str:
        if not self.found:
            return ""
        icon = {"good": "👍 looks good", "not": "👎 doesn't look good", "mixed": "➖ mixed", "n/a": "➖ no H2H read"}[self.verdict]
        return f"🧠 Marv H2H: {icon} — {self.why} · {self.prob}"


def _team_side(side: str, rec: dict) -> str | None:
    hs, as_ = similarity(side, rec["home"]), similarity(side, rec["away"])
    if max(hs, as_) < 0.5:
        return None
    return "home" if hs >= as_ else "away"


def overlay(state_dir: Path, sport: str, market: str, side: str, line: float | None = None, team: str | None = None,
            other: str | None = None, text: str | None = None, price: float | None = None, live: bool = False) -> Overlay:
    """Team H2H read on one March_edge alert. sport may be a March_edge league key (ncaaf, nfl, ...)."""
    from . import proven
    sport = LEAGUE_TO_SPORT.get((sport or "").lower(), (sport or "").lower())
    kind = _market_kind(market)
    rec = None
    if team:
        rec = find_game(state_dir, sport, team, other)
    if rec is None and text:
        rec = find_game_in_text(state_dir, sport, text)
    if rec is None and kind in ("ml", "spread") and side:
        rec = find_game(state_dir, sport, side)
    if rec is None:  # not in Marv's engine slate: fall back to the ratings-only H2H card (top-30 CFB)
        cards = _h2h_cards(state_dir, sport)
        names = [n for n in (team, other, side if kind in ("ml", "spread") else None) if n]
        best, score = None, 0.0
        for c in cards:
            if text:
                sc = min(_in_text(c["home"], text), _in_text(c["away"], text))
            elif names:
                sc = max(max(similarity(n, c["home"]), similarity(n, c["away"])) for n in names)
            else:
                sc = 0.0
            if sc > score:
                best, score = c, sc
        if best is not None and score >= 0.75:
            rec = {"home": best["home"], "away": best["away"], "model_margin": best.get("margin") or 0.0,
                   "model_total": best.get("rating_total") or 0.0, "notes": []}
    if rec is None:
        return Overlay(False)
    game = f"{rec['away']} @ {rec['home']}"
    card = next((c for c in _h2h_cards(state_dir, sport)
                 if min(max(similarity(c["home"], rec["home"]), similarity(c["home"], rec["away"])),
                        max(similarity(c["away"], rec["home"]), similarity(c["away"], rec["away"]))) >= 0.75), None)
    margin = card["margin"] if card and card.get("margin") is not None else rec["model_margin"]  # home view
    total = card["rating_total"] if card and card.get("rating_total") is not None else rec["model_total"]
    src = "ratings" if card else "Marv model"
    yes, no, why, signal = 0, 0, [], None
    tail = " (live: pregame matchup read only)" if live else ""

    if kind in ("ml", "spread"):
        t = _team_side(side, rec)
        if t is None:
            return Overlay(False)
        name, opp = (rec["home"], rec["away"]) if t == "home" else (rec["away"], rec["home"])
        m = margin if t == "home" else -margin  # projected margin for the bet's team
        if kind == "ml" or line is None or live:  # live lines move with the score: team-quality read only
            ok = m > 0
            why.append(f"{name if ok else opp} by {abs(m):.0f} ({src})")
        else:
            ok = m + line > 0
            why.append(f"{name} projected {'to win by' if m > 0 else 'to lose by'} {abs(m):.0f} vs {line:+g} ({src})")
        cover_gap = None if (kind == "ml" or line is None or live) else m + line
        w = 3 if cover_gap is not None and abs(cover_gap) >= 3 else 1  # spreads: the cover check decides unless close
        yes, no = (yes + w, no) if ok else (yes, no + w)
        if card:
            cat = card.get("cat_home"), card.get("cat_away")
            mine = cat[0] if t == "home" else cat[1]
            if mine is not None:
                if mine == 2:
                    yes += 1; why.append("better O and D ratings (2-0)")
                elif mine == 0:
                    no += 1; why.append(f"{opp} better O and D (0-2)")
                else:
                    why.append("O/D split 1-1")
            st = card.get("stars_home"), card.get("stars_away")
            if st[0] is not None and st[1] is not None:
                a, b = (st[0], st[1]) if t == "home" else (st[1], st[0])
                if a != b:
                    yes, no = (yes + 1, no) if a > b else (yes, no + 1)
                why.append(f"stars QB/RB/WR {a}-{b}")
    elif kind == "total" and live:
        why.append(f"live total: no pregame comparison; pregame projection {total:.0f} ({src})")
        return Overlay(True, "n/a", why[0], proven.UNPROVEN, game)
    elif kind == "total":
        if line is None:
            return Overlay(False)
        over = side.lower().startswith("o")
        proj_over = total > line
        why.append(f"projected total {total:.1f} vs {line:g} ({src})")
        yes, no = (yes + 1, no) if proj_over == over else (yes, no + 1)
        tags = [n for n in rec.get("notes", []) if n.startswith("O/U spots")]
        fade = (card or {}).get("fade")  # "UNDER" / "OVER" / ""
        if not fade and tags:
            fade = "UNDER" if "→Under" in tags[0] else "OVER" if "→Over" in tags[0] else ""
        if fade:
            agree = (fade == "OVER") == over
            yes, no = (yes + 1, no) if agree else (yes, no + 1)
            why.append(f"trend fade says {fade}")
            if agree and fade == "UNDER":
                signal = "OVER-FADE"
    elif kind in ("prop", "team_total"):
        fav = rec["home"] if margin > 0 else rec["away"]
        why.append(f"no proven prop logic; game read: {fav} by {abs(margin):.0f}, total {total:.0f} ({src})")
        return Overlay(True, "n/a", why[0] + tail, proven.UNPROVEN, game)
    else:
        return Overlay(False)

    verdict = "good" if yes > no else "not" if no > yes else "mixed"
    ev = proven.evidence(sport, kind, signal) if verdict == "good" and not live else None
    return Overlay(True, verdict, ", ".join(why) + tail, ev.label() if ev else proven.UNPROVEN, game)


def overlay_json(o: Overlay) -> str:
    return json.dumps({**asdict(o), "line": o.line()})
