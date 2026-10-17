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
            "home_win_old": round(getattr(p, "home_win_old", p.home_win), 4),
            "uf": getattr(p, "uf", 1), "uf_reason": getattr(p, "uf_reason", ""),
            **({k: getattr(p, k) for k in ("master_home_win", "master_home_win_tci", "master_uf", "master_uf_reason",
                                           "master_tot_shift", "master_picks", "master_uf_old",
                                           "master_uf_stake_side", "master_uf_stake_total", "tf_shift", "tci_ppe_shift") if hasattr(p, k)}
               or {k: v for k, v in data.get(f"{sport}:{g.id}", {}).items() if k.startswith("master_")}),
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
H2H_DIR = "marv_predict"  # state/marv_predict/h2h_<sport>.json, written by tools/cfb_card.py / tools/nfl_card.py
CLOSE_MARGIN = 3.0  # |ratings margin| < 3 = close (~50/50) game: H2H pick shown, never a %
CLOSE_TEXT = "close game ~50/50 — no %"
STAR_KEYS = {"nfl": "QB/RB/WR/K"}


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

    pick: str = ""  # game-level H2H pick (game_h2h) / close-game pick note

    def line(self) -> str:
        if not self.found:
            return ""
        if self.verdict == "pick":
            return f"🧠 Marv H2H: {self.pick} {self.why} · {self.prob}"
        if self.verdict == "paper":  # soccer node (marv/soccer_max): paper read, never a %, never a veto
            return f"🧠 Marv H2H: {self.why} · {self.prob}"
        icon = {"good": "👍 good", "not": "👎 bad", "mixed": "➖ mixed", "n/a": "➖ no H2H read"}[self.verdict]
        return f"🧠 Marv H2H: {icon} — {self.why} · {self.prob}"


def _team_side(side: str, rec: dict) -> str | None:
    hs, as_ = similarity(side, rec["home"]), similarity(side, rec["away"])
    if max(hs, as_) < 0.5:
        return None
    return "home" if hs >= as_ else "away"


def _card_for(state_dir: Path, sport: str, rec: dict) -> dict | None:
    return next((c for c in _h2h_cards(state_dir, sport)
                 if min(max(similarity(c["home"], rec["home"]), similarity(c["home"], rec["away"])),
                        max(similarity(c["away"], rec["home"]), similarity(c["away"], rec["away"]))) >= 0.75), None)


def _engine(rec: dict | None) -> dict | None:
    """Mark a predictions.json record as Marv's engine projection (a second opinion next to the ratings card)."""
    return {**rec, "_engine": True} if rec else None


def _from_card(c: dict) -> dict:
    return {"home": c["home"], "away": c["away"], "game_id": c.get("game_id"), "model_margin": c.get("margin") or 0.0,
            "model_total": c.get("rating_total") or 0.0, "notes": []}


def _pick_engine(state_dir: Path, sport: str, rec: dict, kind: str | None = None, t: str | None = None,
                 line: float | None = None, over: bool | None = None, live: bool = False) -> str:
    """' · Pick engine ...' (power rating + trend analyzer + Monte Carlo, marv/pick_engine.py) for CFB / NFL / WNBA,
    with agree / disagree vs the alert's bet. Never a % (the backtest has not passed marv/proven.py). '' if no read."""
    from . import pick_engine as PE
    from . import insight as I
    if sport not in PE.SPORTS or live:
        return ""
    try:
        ln = I.lines(state_dir, sport, rec.get("game_id"), rec["home"], rec["away"]) or {}
        hs = float(ln["spread_last"]) if ln.get("spread_last") is not None else None
        tl = float(ln["total_last"]) if ln.get("total_last") is not None else None
        if kind == "spread" and line is not None and t:
            hs = float(line) if t == "home" else -float(line)
        if kind == "total" and line is not None:
            tl = float(line)
        r = PE.game(state_dir, sport, rec["home"], rec["away"], hs, tl, bool(rec.get("neutral")), block=False)
        if not r:
            return ""
        tag = ""
        if kind in ("ml", "spread") and t:
            mk = "spread" if kind == "spread" and "spread" in r["picks"] else "ml"
            tag = " agrees" if r["picks"][mk] == t else " disagrees"
        elif kind == "total" and over is not None and "total" in r["picks"]:
            tag = " agrees" if (r["picks"]["total"] == "over") == over else " disagrees"
        return " · " + PE.text(r, sport).replace("Pick engine:", f"Pick engine{tag}:", 1) + " (paper, no %)"
    except Exception:  # noqa: BLE001 - the engine line is optional
        return ""


LIVE_TOTAL_EDGE = {"nfl": 2.0, "cfb": 2.5, "wnba": 3.0}  # pts of projected-vs-line gap for a 👍/👎 live read


def _live_score(sport: str, rec: dict, text: str | None):
    """(home_score, away_score, minutes_left) parsed from a live March_edge card line such as
    '🏈 Seahawks 3–14 Broncos · 7:46 3rd'; None when the text has no readable score/clock."""
    import re
    if not text:
        return None
    pat = re.compile(r"^\W*\s*(.+?)\s+(\d+)\s*[–-]\s*(\d+)\s+(.+?)\s*·\s*(\d{1,2}):(\d{2})\s+(1st|2nd|3rd|4th|OT)\b")
    for ln in str(text).splitlines():
        m = pat.match(ln.strip())
        if not m:
            continue
        t1, s1, s2, t2, mm, ss, per = m.groups()
        cfg = GAME.get(sport, GAME["nfl"])
        q = cfg["minutes"] / 4
        n = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4}.get(per)
        clock = int(mm) + int(ss) / 60
        left = clock if n is None else (4 - n) * q + clock
        home_first = similarity(t1, rec["home"]) > similarity(t1, rec["away"])
        hs, as_ = (float(s1), float(s2)) if home_first else (float(s2), float(s1))
        return hs, as_, left
    return None


def _live_total_read(sport: str, rec: dict, text: str | None, side: str, line):
    """2026-10-16 (Marvin): live total H2H = Marv's in-game projected final total (live_state: pregame rate blended
    with this game's pace, applied to the time left) vs the live line. Returns (verdict, why) or None.
    Unproven: shows the projected number only, never a %; March_edge logs a live 👎 but does not block on it."""
    try:
        if line is None or not side:
            return None
        sc = _live_score(sport, rec, text)
        if sc is None:
            return None
        hs, as_, left = sc
        st = live_state(rec, sport, hs, as_, left)
        proj, ln = st["exp_total"], float(line)
        over = side.lower().startswith("o")
        gap = (proj - ln) if over else (ln - proj)
        e = LIVE_TOTAL_EDGE.get(sport, 2.5)
        verdict = "good" if gap >= e else ("not" if gap <= -e else "mixed")
        return verdict, (f"live: Marv projects final total {proj:.1f} vs line {ln:g} "
                         f"({int(hs + as_)} scored, {left:.0f} min left) → {'supports' if gap >= e else 'against' if gap <= -e else 'too close for'} "
                         f"the {'Over' if over else 'Under'}")
    except Exception:  # noqa: BLE001 - never break the alert
        return None


LIVE_SIDE_EDGE = 0.04  # Marv live win/cover prob must beat the price's implied prob by this for a 👍 (👎 if below by it)
LIVE_TT_EDGE = {"nfl": 2.0, "cfb": 2.5, "wnba": 3.0}  # pts projected-team-score vs team-total line for 👍/👎


def _abbr_sim(t: str, name: str) -> float:
    """similarity() that also understands ESPN abbreviations on live cards (WASH ~ Washington, TULN ~ Tulane)."""
    sc = similarity(t, name)
    a = "".join(ch for ch in t.lower() if ch.isalpha())
    if 2 <= len(a) <= 5:
        words = [w for w in name.lower().replace("-", " ").split() if w]
        if any(w.startswith(a) for w in words) or (words and a[0] == words[0][0] and
                                                   all(c in words[0] for c in a) and a[:2] == words[0][:2]):
            sc = max(sc, 0.95)
    return sc


def _live_score2(sport: str, rec: dict, text: str | None):
    """Like _live_score but abbreviation-aware; on a tie assumes the ESPN 'AWAY x – y HOME' order."""
    import re
    if not text:
        return None
    pat = re.compile(r"^\W*\s*(.+?)\s+(\d+)\s*[–-]\s*(\d+)\s+(.+?)\s*·\s*(\d{1,2}):(\d{2})\s+(1st|2nd|3rd|4th|OT)\b")
    for ln in str(text).splitlines():
        m = pat.match(ln.strip())
        if not m:
            continue
        t1, s1, s2, t2, mm, ss, per = m.groups()
        cfg = GAME.get(sport, GAME["nfl"])
        q = cfg["minutes"] / 4
        n = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4}.get(per)
        clock = int(mm) + int(ss) / 60
        left = clock if n is None else (4 - n) * q + clock
        h1 = _abbr_sim(t1, rec["home"]) + _abbr_sim(t2, rec["away"])
        a1 = _abbr_sim(t1, rec["away"]) + _abbr_sim(t2, rec["home"])
        home_first = h1 > a1
        hs, as_ = (float(s1), float(s2)) if home_first else (float(s2), float(s1))
        return hs, as_, left
    return None


def _implied(price) -> float | None:
    try:
        a = float(price)
    except (TypeError, ValueError):
        return None
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def _live_side_read(sport: str, rec: dict, text: str | None, t: str, line, price, spread: bool):
    """2026-10-16 (Marvin): live ML/spread H2H = Marv in-game projection (live_state) -> win (or cover) prob for the
    alerted side vs the price. (verdict, why) or None. Information only: never a veto (MARV_VETO_LIVE stays off)."""
    try:
        sc = _live_score2(sport, rec, text)
        if sc is None:
            return None
        hs, as_, left = sc
        st = live_state(rec, sport, hs, as_, left)
        name = rec["home"] if t == "home" else rec["away"]
        sgn = 1 if t == "home" else -1
        if spread and line is not None:
            m = sgn * st["exp_margin"]
            p = 1 - _norm_cdf((-float(line) - m) / st["sd_m"])
            what = f"cover {float(line):+g}"
        else:
            p = st["p_home"] if t == "home" else 1 - st["p_home"]
            what = "win"
        imp = _implied(price)
        if imp is None:
            verdict = "mixed"
        else:
            verdict = "good" if p - imp >= LIVE_SIDE_EDGE else ("not" if p - imp <= -LIVE_SIDE_EDGE else "mixed")
        sc_txt = f"{rec['away']} {as_:.0f}–{hs:.0f} {rec['home']}, {left:.0f} min left"
        return verdict, (f"live: Marv {name} {what} {p:.0%}" + (f" vs price {imp:.0%}" if imp is not None else "")
                         + f" (projected margin {name} {sgn * st['exp_margin']:+.1f}; {sc_txt})")
    except Exception:  # noqa: BLE001 - never break the alert
        return None


def _live_tt_read(sport: str, rec: dict, text: str | None, side: str, line, team: str | None):
    """Live team-total H2H: Marv projected final team score (same split as state/live_tt_shadow.json:
    (exp_total ± exp_margin)/2) vs the team-total line. (verdict, why) or None. Information only."""
    import re
    try:
        if line is None:
            return None
        nm, sd = team, side
        m = re.search(r"VALUE\W+(.+?)\s+(OVER|UNDER)\s+([\d.]+)", str(text or ""), re.I)
        if m:
            nm, sd = m.group(1).strip(), m.group(2)
        if not nm or not sd:
            return None
        t = "home" if _abbr_sim(nm, rec["home"]) >= _abbr_sim(nm, rec["away"]) else "away"
        if max(_abbr_sim(nm, rec["home"]), _abbr_sim(nm, rec["away"])) < 0.5:
            return None
        sc = _live_score2(sport, rec, text)
        if sc is None:
            return None
        hs, as_, left = sc
        st = live_state(rec, sport, hs, as_, left)
        proj = (st["exp_total"] + st["exp_margin"]) / 2 if t == "home" else (st["exp_total"] - st["exp_margin"]) / 2
        over = sd.lower().startswith("o")
        gap = (proj - float(line)) if over else (float(line) - proj)
        e = LIVE_TT_EDGE.get(sport, 2.5)
        verdict = "good" if gap >= e else ("not" if gap <= -e else "mixed")
        name = rec[t]
        return verdict, (f"live: Marv projects {name} {proj:.1f} vs team total {float(line):g} "
                         f"({int(hs if t == 'home' else as_)} so far, {left:.0f} min left) → "
                         f"{'supports' if gap >= e else 'against' if gap <= -e else 'too close for'} the {'Over' if over else 'Under'}")
    except Exception:  # noqa: BLE001
        return None


def _track_live_read(state_dir: Path, sport: str, market: str, side: str, line, price, o) -> None:
    """Log each live H2H read once (sport+game+market+side+line) to state/live_h2h_reads.json; grade_live_reads() grades."""
    try:
        import os as _os
        from datetime import datetime, timezone
        path = state_dir / "live_h2h_reads.json"
        book = json.loads(path.read_text()) if path.exists() else []
        k = [sport, o.game, market, side, None if line in (None, "") else float(line)]
        if any(r.get("key") == k for r in book):
            return
        book.append({"key": k, "at": datetime.now(timezone.utc).isoformat(), "sport": sport, "game": o.game,
                     "market": market, "side": side, "line": k[4], "price": None if price in (None, "") else float(price),
                     "verdict": o.verdict, "why": o.why, "status": "pending"})
        tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(book, indent=1)); _os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        pass


def overlay(state_dir: Path, sport: str, market: str, side: str, line: float | None = None, team: str | None = None,
            other: str | None = None, text: str | None = None, price: float | None = None, live: bool = False) -> Overlay:
    o = _overlay(state_dir, sport, market, side, line, team, other, text, price, live)
    if live and o.found and str(o.prob).startswith("live"):
        _track_live_read(state_dir, LEAGUE_TO_SPORT.get((sport or "").lower(), (sport or "").lower()), market, side, line, price, o)
    return o


def _cfb_prop_read(market, side, line, text, live):
    """2026-10-17 (Marvin): CFB pregame prop projection (marv/cfb_props_shadow ledger) vs the alerted prop."""
    import html as _h
    import re as _re
    try:
        from . import cfb_props_shadow as CPS
        t = _re.sub(r"<[^>]+>", "", _h.unescape(text))
        m = _re.search(r"[—-]\s*(.+?)\s+(OVER|UNDER)\s+([\d.]+)", t)
        if not m or line is None:
            return None
        b = CPS.lookup(m.group(1).strip(), market)
        if not b:
            return ("n/a", f"no Marv CFB projection for {m.group(1).strip()}", "")
        over = str(side).lower().startswith("o")
        gap = (b["proj"] - float(line)) * (1 if over else -1)
        e = max(2.0, 0.05 * float(line))
        v = "good" if gap >= e else ("not" if gap <= -e else "mixed")
        why = f"{'pregame ' if live else ''}Marv proj {b['proj']:g} vs {float(line):g}" + (f" · {b['news']}" if b.get("news") else "")
        return (v, why, b.get("game", ""))
    except Exception:  # noqa: BLE001
        return None


def _overlay(state_dir: Path, sport: str, market: str, side: str, line: float | None = None, team: str | None = None,
            other: str | None = None, text: str | None = None, price: float | None = None, live: bool = False) -> Overlay:
    """Marv Predict read on one March_edge alert: every metric Marv has on the game (ratings margin, O/D category
    points, stars, Marv model, injuries/QB, line moves, Bovada vs Pinnacle, O/U trends) compared FOR / AGAINST the
    bet -> good / bad / mixed + short why (marv/insight.py). A % prints only for a proven rule (marv/proven.py).
    sport may be a March_edge league key (ncaaf, nfl, eng.1, soccer_epl ...)."""
    from .soccer_max import node as SN
    if SN.is_soccer(sport):  # Marv Soccer Predict Max node: paper read only (verdict 'paper' cannot veto)
        try:
            o = SN.overlay(state_dir, sport, market, side, line, team, other, text, price, live)
        except Exception:  # noqa: BLE001 - never break the March_edge alert
            o = None
        return Overlay(True, o["verdict"], o["why"], o["prob"], o["game"]) if o else Overlay(False)
    from . import insight as I
    from . import proven
    sport = LEAGUE_TO_SPORT.get((sport or "").lower(), (sport or "").lower())
    kind = _market_kind(market)
    if kind == "prop" and sport == "cfb" and text:
        cp = _cfb_prop_read(market, side, line, text, live)
        if cp:
            return Overlay(True, cp[0], cp[1], "CFB prop projection, unproven (info only, no veto)", cp[2])
    rec = None
    if team:
        rec = find_game(state_dir, sport, team, other)
    if rec is None and text:
        rec = find_game_in_text(state_dir, sport, text)
    if rec is None and kind in ("ml", "spread") and side and not (team and other):
        # side-only fuzzy lookup only when the alert didn't name both teams ("Florida State" ~ "Ohio State" otherwise)
        rec = find_game(state_dir, sport, side)
    rec = _engine(rec)
    if rec is None:  # not in Marv's engine slate: fall back to the ratings-only H2H card
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
            rec = _from_card(best)
    if rec is None:
        if live and kind in ("ml", "spread", "team_total") and sport in ("nfl", "cfb", "wnba"):
            return Overlay(True, "n/a", "live: no Marv pregame model for this game", "information only", "")
        return Overlay(False)
    game = f"{rec['away']} @ {rec['home']}"
    card = _card_for(state_dir, sport, rec)
    if card and not rec.get("game_id"):
        rec["game_id"] = card.get("game_id")
    margin = card["margin"] if card and card.get("margin") is not None else rec["model_margin"]  # home view
    total = card["rating_total"] if card and card.get("rating_total") is not None else rec["model_total"]
    src = "ratings" if card else "Marv model"
    tail = " (live: pregame matchup read only)" if live else ""

    if kind in ("ml", "spread"):
        t = _team_side(side, rec)
        if t is None:
            return Overlay(False)
        if live:
            lv = _live_side_read(sport, rec, text, t, line, price, kind == "spread")
            if lv:
                return Overlay(True, lv[0], lv[1], "live projection, unproven (info only, no veto)", game)
        ins = I.side_insight(state_dir, sport, rec, card, t, line, price, live, spread=(kind == "spread"))
    elif kind == "team_total" and live:
        lv = _live_tt_read(sport, rec, text, side, line, team)
        if lv:
            return Overlay(True, lv[0], lv[1], "live projection, unproven (info only, no veto)", game)
        return Overlay(True, "n/a", "live team total: no live score/line to compare", "information only", game)
    elif kind == "total" and live:
        lv = _live_total_read(sport, rec, text, side, line)
        if lv:
            return Overlay(True, lv[0], lv[1], "live projection, unproven (log only, no veto)", game)
        return Overlay(True, "n/a", f"live total: no pregame comparison; pregame projection {total:.0f} ({src})",
                       proven.UNPROVEN, game)
    elif kind == "total":
        if line is None:
            return Overlay(False)
        ins = I.total_insight(state_dir, sport, rec, card, side.lower().startswith("o"), float(line))
    elif kind in ("prop", "team_total"):
        fav = rec["home"] if margin > 0 else rec["away"]
        return Overlay(True, "n/a", f"no proven prop logic; game read: {fav} by {abs(margin):.0f}, total {total:.0f} ({src})"
                       + tail, proven.UNPROVEN, game)
    else:
        return Overlay(False)

    verdict, why = ins.verdict, ins.why()
    why += _pick_engine(state_dir, sport, rec, kind, t if kind in ("ml", "spread") else None, line,
                        side.lower().startswith("o") if kind == "total" else None, live)
    if kind in ("ml", "spread") and abs(margin) < CLOSE_MARGIN:
        # Close (~50/50) game: say who the H2H tie-break picks, never print a % (no honest edge on a coin flip).
        hp = _h2h_pick(card, rec, margin)
        return Overlay(True, verdict, why + f"; close game: H2H pick {hp[0]} ({hp[1]})" + tail, CLOSE_TEXT, game, hp[0])
    signal = ins.signal
    if kind == "ml" and not live and verdict == "good":
        signal = _ml_signal(card, rec, margin, t)
    ev = proven.evidence(sport, kind, signal) if verdict == "good" and not live else None
    return Overlay(True, verdict, why + tail, ev.label() if ev else proven.UNPROVEN, game)


def _pts(m: float) -> str:
    return f"{abs(m):.1f}" if abs(m) < CLOSE_MARGIN else f"{abs(m):.0f}"


def _h2h_pick(card: dict | None, rec: dict, margin: float) -> tuple[str, str]:
    """(team, method): the card's H2H pick (sweep / star tie-break / margin), else the ratings margin side."""
    if card and card.get("pick"):
        return card["pick"], card.get("method") or "H2H"
    return (rec["home"] if margin > 0 else rec["away"]), "ratings margin"


def _ml_signal(card: dict | None, rec: dict, margin: float, side: str | None) -> str | None:
    """Named ML rule for the proven registry (marv/proven.py), or None. Only rules listed there can print a %."""
    if not card or side is None:
        return None
    team = rec["home"] if side == "home" else rec["away"]
    if similarity(card.get("pick", ""), team) < 0.75:
        return None
    m = card.get("method", "")
    return "H2H-SWEEP" if "sweep" in m else "H2H-STAR" if "star" in m else "H2H-MARGIN"


def game_h2h(state_dir: Path, sport: str, team: str | None = None, other: str | None = None,
             text: str | None = None) -> Overlay:
    """Game-level Marv Predict read for queries (/game, /board, March_edge /game): the H2H pick, every metric compared
    for / against it (good / bad / mixed + why) and the O/U lean. A % only if that rule passed the proven bar;
    close games never get a %."""
    from .soccer_max import node as SN
    if SN.is_soccer(sport):  # soccer node: game-level paper read, never a %
        try:
            o = SN.game_read(state_dir, sport, team, other, text)
        except Exception:  # noqa: BLE001
            o = None
        return Overlay(True, "pick", o["why"], o["prob"], o["game"], o["pick"]) if o else Overlay(False)
    from . import insight as I
    from . import proven
    sport = LEAGUE_TO_SPORT.get((sport or "").lower(), (sport or "").lower())
    rec = find_game(state_dir, sport, team, other) if team else None
    if rec is None and text:
        rec = find_game_in_text(state_dir, sport, text)
    rec = _engine(rec)
    cards = _h2h_cards(state_dir, sport)
    best, score = None, 0.0
    for c in cards:
        if rec is not None:
            sc = min(max(similarity(c["home"], rec["home"]), similarity(c["home"], rec["away"])),
                     max(similarity(c["away"], rec["home"]), similarity(c["away"], rec["away"])))
        elif text:
            sc = min(_in_text(c["home"], text), _in_text(c["away"], text))
        elif team:
            sc = max(similarity(team, c["home"]), similarity(team, c["away"]))
            if other:
                sc = min(sc, max(similarity(other, c["home"]), similarity(other, c["away"])))
        else:
            sc = 0.0
        if sc > score:
            best, score = c, sc
    card = best if score >= 0.75 else None
    if rec is None and card is None:
        return Overlay(False)
    if rec is None:
        rec = _from_card(card)
    elif card and not rec.get("game_id"):
        rec["game_id"] = card.get("game_id")
    margin = card["margin"] if card and card.get("margin") is not None else rec["model_margin"]
    pick, method = _h2h_pick(card, rec, margin)
    side = "home" if similarity(pick, rec["home"]) >= similarity(pick, rec["away"]) else "away"
    ins = I.side_insight(state_dir, sport, rec, card, side)
    icon = {"good": "👍 good", "not": "👎 bad", "mixed": "➖ mixed"}[ins.verdict]
    why = f"{icon} ({method}) — {ins.why()}"
    close = abs(margin) < CLOSE_MARGIN
    if close:
        prob = CLOSE_TEXT
    else:
        ev = proven.evidence(sport, "ml", _ml_signal(card, rec, margin, side)) if ins.verdict == "good" else None
        prob = ev.label() if ev else proven.UNPROVEN
    tl = I.best_total_line(state_dir, sport, rec, card)
    if tl is not None:
        o = I.total_insight(state_dir, sport, rec, card, True, tl)
        if o.verdict == "mixed":
            why += f" · O/U {tl:g}: no clear lean ({o.tally[0]}-{o.tally[1]})"
        else:
            over = o.verdict == "good"
            u = o if over else I.total_insight(state_dir, sport, rec, card, False, tl)
            ev = proven.evidence(sport, "total", u.signal)
            why += (f" · O/U: {'Over' if over else 'Under'} {tl:g} ({u.tally[0]}-{u.tally[1]}: "
                    f"{', '.join(f.label for f in u.factors if f.side > 0)[:120]})"
                    + (f" {ev.label()}" if ev else ""))
    try:
        from . import hybrid as HY
        hr = I.hybrid_read(state_dir, sport, rec, total=tl)
        if hr:
            why += " · " + HY.text(hr, rec["home"], rec["away"], sport) + " (paper, no %)"
    except Exception:  # noqa: BLE001 - the hybrid line is optional
        pass
    why += _pick_engine(state_dir, sport, rec)
    return Overlay(True, "pick", why, prob, f"{rec['away']} @ {rec['home']}", pick)


def overlay_json(o: Overlay) -> str:
    return json.dumps({**asdict(o), "line": o.line()})
