"""Live (in-game) player-prop engine: price Bovada's live prop lines from the game's pace and the player's usage.

At every quarter / half break (the live monitor calls `on_period`), and on demand (`python -m marv live-props scan`):

  1. Box score (ESPN summary): each player's stat so far and usage (targets, carries, pass attempts, minutes,
     fouls), each team's plays (NFL / CFB) or possessions (basketball) so far, and the score.
  2. Anchor: Marv's pregame projection for the player (state/props_priced_<sport>.json, written by the daily props
     run), else the pregame Bovada line captured before kickoff (state/live_props/pregame_lines.json).
  3. Projection for the rest of the game:
       production per team play  = blend(pregame per-play rate, in-game per-play rate), weighted by plays seen
       remaining team plays      = minutes left x team pace (this game's pace shrunk towards the league norm)
       script                    = trailing teams pass more / run less (football); blowouts and foul trouble cut
                                   minutes (basketball); starters sit in a late rout
       remaining mean            = per-play rate x remaining plays x script
  4. Distribution: compound Poisson-gamma Monte Carlo (seeded): number of events (catches, carries, completions,
     baskets, rebounds...) is overdispersed Poisson, each event's size is gamma (yards) or 1 (counts).
     P(final > line) = P(remaining > line - so far).
  5. Price: Bovada's two-sided live price -> no-vig probability. A prop is RECOMMENDED only if
        edge (Marv - no-vig) >= LIVE_PROP_MIN_EDGE, EV at the real price >= LIVE_PROP_MIN_EV,
        the projection clears the line by a minimum gap, the price is between -200 and +180,
        the Bovada market was updated in the last LIVE_PROP_MAX_AGE seconds (stale line = phantom edge),
        enough game is left, the player is still being used (no usage in a full period = possibly hurt),
        and it was not already sent for this game.
     At most LIVE_PROP_MAX_PER_GAME picks per break, one per player.
  6. Every priced prop goes to state/live_props/shadow.jsonl (the dataset to tune thresholds), every recommendation
     to state/live_props/recs.json; at the final whistle they are graded against the box score and the game's
     W-L is sent.

Honesty: there is no live-prop history to backtest yet (pregame props lost money vs real lines), so alerts are
labelled PAPER, show no hit-rate %, and stay paper until 100+ graded recs beat 52.4% and positive ROI.
`LIVE_PROPS_SEND=off` keeps everything silent (logged and graded only).
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .markets import american_to_prob, no_vig

log = logging.getLogger(__name__)

ODDS_KEYS = {"nfl": "americanfootball_nfl", "cfb": "americanfootball_ncaaf", "nba": "basketball_nba",
             "wnba": "basketball_wnba", "ncaab": "basketball_ncaab", "ncaaw": "basketball_wncaab"}
FOOTBALL = ("nfl", "cfb")


@dataclass(frozen=True)
class Spec:
    key: str           # Odds API market key
    label: str
    group: str         # ESPN box-score group ("" for basketball)
    stat: str          # ESPN label for the stat
    usage: str         # ESPN label for the usage (opportunity) stat
    unit: float        # average size of one event (yards per catch, points per basket, 1 for counts)
    cv: float          # coefficient of variation of one event's size (0 = every event counts 1)
    disp: float        # gamma-Poisson shape for the event count (lower = more overdispersed)
    min_gap: float     # projection must clear the line by this much
    script: float      # football: change in production per point of deficit (+ = trailing helps)


FOOTBALL_SPECS = {
    "player_pass_yds": Spec("player_pass_yds", "Passing yards", "passing", "YDS", "ATT", 11.5, 0.85, 8, 12.0, 0.010),
    "player_rush_yds": Spec("player_rush_yds", "Rushing yards", "rushing", "YDS", "CAR", 4.3, 1.4, 8, 6.0, -0.012),
    "player_reception_yds": Spec("player_reception_yds", "Receiving yards", "receiving", "YDS", "TGTS", 11.0, 0.9, 8, 7.0, 0.008),
    "player_receptions": Spec("player_receptions", "Receptions", "receiving", "REC", "TGTS", 1.0, 0.0, 10, 1.0, 0.008),
}
BASKETBALL_SPECS = {
    "player_points": Spec("player_points", "Points", "", "PTS", "MIN", 2.1, 0.35, 15, 2.0, 0.0),
    "player_rebounds": Spec("player_rebounds", "Rebounds", "", "REB", "MIN", 1.0, 0.0, 15, 1.0, 0.0),
    "player_assists": Spec("player_assists", "Assists", "", "AST", "MIN", 1.0, 0.0, 12, 1.0, 0.0),
    "player_threes": Spec("player_threes", "Made threes", "", "3PT", "MIN", 1.0, 0.0, 10, 0.7, 0.0),
}

# minutes in regulation, league team plays (football) / possessions (basketball) per game, foul-out count
GAME = {"nfl": (60, 62.0, None), "cfb": (60, 70.0, None), "nba": (48, 99.0, 6), "wnba": (40, 80.0, 6),
        "ncaab": (40, 68.0, 5), "ncaaw": (40, 70.0, 5)}
PRIOR_PLAYS = {"nfl": 30.0, "cfb": 30.0, "nba": 40.0, "wnba": 35.0, "ncaab": 30.0, "ncaaw": 30.0}  # weight of the pregame rate
PACE_PRIOR = {"nfl": 40.0, "cfb": 40.0, "nba": 60.0, "wnba": 50.0, "ncaab": 40.0, "ncaaw": 40.0}  # weight of the league pace


def specs(sport: str) -> dict[str, Spec]:
    return FOOTBALL_SPECS if sport in FOOTBALL else BASKETBALL_SPECS


def _env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


# ---------------------------------------------------------------- box score


def _num(raw, part: int = 0) -> float | None:
    """'12/17' -> 12 (part 0) or 17 (part 1); '2-5' the same; '35' -> 35."""
    s = str(raw).strip()
    for sep in ("/", "-"):
        if sep in s[1:]:
            bits = s.split(sep)
            s = bits[part] if part < len(bits) else bits[0]
            break
    try:
        return float(s)
    except ValueError:
        return None


def parse_box(summary: dict) -> dict:
    """{'players': {name: {'team': str, 'stats': {group: {label: value}}}}, 'teams': {team: {...}}}.
    Made-attempt cells ('12/17', '2-5') give the made count under the label and attempts under '<label>_ATT';
    passing C/ATT also gives 'ATT'."""
    players: dict[str, dict] = {}
    box = summary.get("boxscore") or {}
    for team in box.get("players", []):
        tname = (team.get("team") or {}).get("displayName", "")
        for grp in team.get("statistics", []):
            gname = str(grp.get("name") or "").lower()
            labels = [str(x).upper() for x in grp.get("labels", [])]
            for a in grp.get("athletes", []):
                who = (a.get("athlete") or {}).get("displayName")
                if not who or a.get("didNotPlay"):
                    continue
                vals: dict[str, float] = {}
                for label, raw in zip(labels, a.get("stats", [])):
                    made, att = _num(raw, 0), _num(raw, 1)
                    if made is None:
                        continue
                    vals[label] = made
                    if ("/" in str(raw) or "-" in str(raw)[1:]) and att is not None:
                        vals[f"{label}_ATT"] = att
                if "C/ATT" in vals:
                    vals["ATT"] = vals.get("C/ATT_ATT", 0.0)
                rec = players.setdefault(who, {"team": tname, "stats": {}})
                rec["stats"].setdefault(gname, {}).update(vals)
    teams: dict[str, dict[str, float]] = {}
    for t in box.get("teams", []):
        tname = (t.get("team") or {}).get("displayName", "")
        vals = {}
        for s in t.get("statistics", []):
            name, raw = s.get("name", ""), s.get("displayValue", "")
            if "-" in name:  # 'fieldGoalsMade-fieldGoalsAttempted'
                a, b = name.split("-", 1)
                if _num(raw, 0) is not None:
                    vals[a], vals[b] = _num(raw, 0), _num(raw, 1)
            elif name == "completionAttempts":
                vals["completions"], vals["passAttempts"] = _num(raw, 0), _num(raw, 1)
            elif name == "sacksYardsLost":
                vals["sacks"] = _num(raw, 0)
            elif _num(raw) is not None:
                vals[name] = _num(raw)
        teams[tname] = vals
    return {"players": players, "teams": teams}


def team_plays(sport: str, t: dict) -> float:
    """Offensive plays (football) or possessions (basketball) so far for one team."""
    if sport in FOOTBALL:
        if t.get("totalOffensivePlays"):
            return float(t["totalOffensivePlays"])
        return float((t.get("rushingAttempts") or 0) + (t.get("passAttempts") or 0) + (t.get("sacks") or 0))
    fga, fta = t.get("fieldGoalsAttempted") or 0, t.get("freeThrowsAttempted") or 0
    orb = t.get("offensiveRebounds") or 0
    tov = t.get("totalTurnovers") or t.get("turnovers") or 0
    return float(max(fga + 0.44 * fta - orb + tov, 0))


def stat_value(sport: str, spec: Spec, player: dict) -> tuple[float | None, float]:
    """(stat so far, usage so far) for one player from parse_box output."""
    st = player.get("stats", {})
    if sport in FOOTBALL:
        g = st.get(spec.group, {})
        return g.get(spec.stat), float(g.get(spec.usage) or 0)
    g = next(iter(st.values()), {}) if st else {}
    return g.get(spec.stat), float(g.get("MIN") or 0)


# ---------------------------------------------------------------- projection


@dataclass
class PropState:
    sport: str
    market: str
    player: str
    so_far: float
    anchor: float          # pregame full-game expectation (Marv projection or pregame line)
    elapsed: float         # game minutes played
    team_plays: float      # player's team plays / possessions so far
    margin: float = 0.0    # player's team score minus opponent's
    fouls: float = 0.0
    minutes: float = 0.0   # basketball: player minutes so far
    usage: float = 0.0
    extra: dict = field(default_factory=dict)


def project(st: PropState) -> dict:
    """Mean of the stat over the rest of the game and the pieces that went into it."""
    spec = specs(st.sport)[st.market]
    total_min, league_plays, foul_out = GAME[st.sport]
    left = max(total_min - st.elapsed, 0.0)
    if left <= 0:
        return {"rem_mean": 0.0, "final_mean": st.so_far, "pace": 1.0, "script": 1.0, "left": 0.0}
    league_rate = league_plays / total_min  # plays per minute
    # This game's pace for the player's team, shrunk towards the league norm by how many plays we've seen.
    obs_rate = st.team_plays / st.elapsed if st.elapsed > 0 else league_rate
    k = PACE_PRIOR[st.sport]
    pace_rate = (k * league_rate + st.team_plays * obs_rate) / (k + st.team_plays) if st.team_plays > 0 else league_rate
    pace = min(max(pace_rate / league_rate, 0.75), 1.3)
    # Production per team play: pregame anchor (per league play) blended with what he's done per play so far.
    pre_pp = st.anchor / league_plays
    live_pp = st.so_far / st.team_plays if st.team_plays > 0 else pre_pp
    w = PRIOR_PLAYS[st.sport]
    per_play = (w * pre_pp + st.team_plays * live_pp) / (w + st.team_plays) if st.team_plays > 0 else pre_pp
    rem_plays = left * pace_rate
    script = 1.0
    frac_left = left / total_min
    if st.sport in FOOTBALL:
        deficit = max(min(-st.margin, 21), -21)
        script = 1 + spec.script * deficit * (1.5 - frac_left)  # script matters more late
        if st.margin >= 24 and frac_left <= 0.25:
            script *= 0.55  # rout: starters rest
    else:
        if abs(st.margin) >= 15 and frac_left <= 0.5:
            script *= max(0.55, 1 - 0.03 * (abs(st.margin) - 12) * (1.2 - frac_left))  # blowout minutes
        if foul_out and st.fouls >= foul_out:
            script = 0.0
        elif foul_out and st.fouls == foul_out - 1:
            script *= 0.7
        elif foul_out and st.fouls == foul_out - 2 and frac_left > 0.4:
            script *= 0.88
    script = max(script, 0.0)
    rem = max(per_play * rem_plays * script, 0.0)
    return {"rem_mean": rem, "final_mean": st.so_far + rem, "pace": round(pace, 3), "script": round(script, 3),
            "left": left, "per_play": per_play}


def p_over(sport: str, market: str, so_far: float, rem_mean: float, line: float, n: int = 20000,
           seed: int = 7) -> tuple[float, float]:
    """(P(final > line), P(push)) from a compound Poisson-gamma Monte Carlo of the remaining production."""
    spec = specs(sport)[market]
    need = line - so_far
    if need < 0:
        return 1.0, 0.0
    if rem_mean <= 1e-9:
        return 0.0, float(need == 0)
    rng = np.random.default_rng(seed)
    lam = rng.gamma(spec.disp, rem_mean / spec.unit / spec.disp, n)
    events = rng.poisson(lam)
    if spec.cv > 0:
        shape = 1 / spec.cv ** 2
        tot = np.where(events > 0, rng.gamma(np.maximum(events, 1) * shape, spec.unit / shape), 0.0)
        if sport not in FOOTBALL:  # points come in whole numbers
            tot = np.round(tot)
    else:
        tot = events.astype(float)
    over = float((tot > need).mean())
    push = float((tot == need).mean()) if float(need).is_integer() else 0.0
    return over, push


def payout(price: float) -> float:
    return price / 100 if price > 0 else 100 / -price


def evaluate(st: PropState, line: float, over_price: float | None, under_price: float | None) -> dict | None:
    """Fair probability, no-vig market probability, edge and EV for both sides; picks the better side."""
    if over_price is None or under_price is None:
        return None
    pr = project(st)
    po, push = p_over(st.sport, st.market, st.so_far, pr["rem_mean"], line)
    pu = max(1 - po - push, 0.0)
    m_over, m_under = no_vig(over_price, under_price)
    rows = []
    for side, p, mkt, price in (("Over", po, m_over, over_price), ("Under", pu, m_under, under_price)):
        ev = p * payout(price) - (1 - p - push)
        rows.append({"side": side, "p": p, "market_p": mkt, "edge": p - mkt, "ev": ev, "price": price})
    best = max(rows, key=lambda r: r["ev"])
    return {**best, "line": line, "proj": pr["final_mean"], "rem": pr["rem_mean"], "pace": pr["pace"],
            "script": pr["script"], "left": pr["left"], "p_over": po, "push": push, "so_far": st.so_far,
            "anchor": st.anchor}


def qualifies(ev: dict, sport: str, market: str) -> tuple[bool, str]:
    """The recommendation gate. Returns (ok, reason when not)."""
    spec = specs(sport)[market]
    min_edge, min_ev = _env("LIVE_PROP_MIN_EDGE", 0.07), _env("LIVE_PROP_MIN_EV", 0.04)
    gap = ev["proj"] - ev["line"] if ev["side"] == "Over" else ev["line"] - ev["proj"]
    total_min = GAME[sport][0]
    if ev["left"] < (6 if sport in FOOTBALL else 4) or ev["left"] > total_min * 0.8:
        return False, "too early/late"
    if ev["so_far"] > ev["line"] and ev["side"] == "Over":
        return False, "already over"
    if not -200 <= ev["price"] <= 180:
        return False, "price out of range"
    if ev["edge"] < min_edge:
        return False, "edge"
    if ev["ev"] < min_ev:
        return False, "ev"
    if gap < spec.min_gap:
        return False, "gap"
    if ev["p"] < 0.55:
        return False, "probability"
    return True, ""


# ---------------------------------------------------------------- odds + anchors


def bovada_lines(event_odds: dict, book: str = "bovada", max_age: float | None = None,
                 now: datetime | None = None) -> list[dict]:
    """One row per player/market from the book's live props (two-sided only, fresh only)."""
    max_age = _env("LIVE_PROP_MAX_AGE", 180) if max_age is None else max_age
    now = now or datetime.now(timezone.utc)
    out = []
    for b in event_odds.get("bookmakers", []):
        if b.get("key") != book:
            continue
        for m in b.get("markets", []):
            upd = m.get("last_update")
            age = None
            if upd:
                try:
                    age = (now - datetime.fromisoformat(upd.replace("Z", "+00:00"))).total_seconds()
                except ValueError:
                    age = None
            if max_age and age is not None and age > max_age:
                continue
            by: dict[tuple, dict] = {}
            for o in m.get("outcomes", []):
                if o.get("name") in ("Over", "Under") and o.get("description") and o.get("point") is not None:
                    r = by.setdefault((o["description"], float(o["point"])), {})
                    r[o["name"].lower()] = o.get("price")
            for (player, line), r in by.items():
                if "over" in r and "under" in r:
                    out.append({"player": player, "market": m["key"], "line": line, "over": r["over"],
                                "under": r["under"], "age": age})
    return out


def _norm(name: str) -> str:
    from .props.odds import norm_name
    return norm_name(name)


def anchors(state_dir: Path, sport: str, event_id: str | None = None) -> dict[tuple[str, str], float]:
    """{(normalised player, market): pregame expectation}: pregame Bovada line captured before kickoff,
    overridden by Marv's own pregame projection when the daily props run priced the player."""
    out: dict[tuple[str, str], float] = {}
    pre = state_dir / "live_props" / "pregame_lines.json"
    if pre.exists():
        try:
            data = json.loads(pre.read_text())
        except json.JSONDecodeError:
            data = {}
        for ev_id, rows in data.items():
            if event_id and ev_id != event_id:
                continue
            for r in rows.get("lines", []):
                out[(_norm(r["player"]), r["market"])] = float(r["line"])
    f = state_dir / f"props_priced_{sport}.json"
    if f.exists():
        try:
            for r in json.loads(f.read_text()):
                if r.get("proj") is not None and r.get("market"):
                    out[(_norm(r["player"]), r["market"])] = float(r["proj"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass
    return out


def save_pregame(state_dir: Path, event: dict, lines: list[dict]) -> None:
    path = state_dir / "live_props" / "pregame_lines.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text()) if path.exists() else {}
    data[event["id"]] = {"game": f"{event.get('away_team')} @ {event.get('home_team')}",
                         "commence": event.get("commence_time"), "lines": lines}
    keep = sorted(data.items(), key=lambda kv: kv[1].get("commence") or "")[-300:]
    path.write_text(json.dumps(dict(keep)))


# ---------------------------------------------------------------- scan one game


def scan_game(sport: str, summary: dict, event_odds: dict, anchor: dict, game_minutes_elapsed: float,
              home: str, away: str, home_score: float, away_score: float, prev_usage: dict | None = None,
              now: datetime | None = None) -> tuple[list[dict], list[dict]]:
    """(every priced prop, recommendations) for one live game."""
    from .data.teams import similarity
    box = parse_box(summary)
    teams = box["teams"]
    sp = specs(sport)
    priced, recs = [], []
    by_player = {_norm(n): (n, p) for n, p in box["players"].items()}
    for row in bovada_lines(event_odds, now=now):
        if row["market"] not in sp:
            continue
        hit = by_player.get(_norm(row["player"]))
        a = anchor.get((_norm(row["player"]), row["market"]))
        if not hit or a is None:
            continue
        name, pl = hit
        so_far, usage = stat_value(sport, sp[row["market"]], pl)
        if so_far is None:
            so_far = 0.0
        is_home = similarity(pl["team"], home) >= similarity(pl["team"], away)
        margin = (home_score - away_score) * (1 if is_home else -1)
        tstats = next((v for k, v in teams.items() if similarity(k, pl["team"]) >= 0.75), {})
        plays = team_plays(sport, tstats) or GAME[sport][1] * game_minutes_elapsed / GAME[sport][0]
        bstats = next(iter(pl["stats"].values()), {}) if sport not in FOOTBALL else {}
        st = PropState(sport, row["market"], row["player"], float(so_far), a, game_minutes_elapsed, plays, margin,
                       fouls=float(bstats.get("PF") or 0), minutes=float(bstats.get("MIN") or 0), usage=usage)
        ev = evaluate(st, row["line"], row["over"], row["under"])
        if not ev:
            continue
        ok, why = qualifies(ev, sport, row["market"])
        # No usage since the last break while his team kept playing = possibly hurt / benched: never recommend.
        key = f"{_norm(row['player'])}|{row['market']}"
        if ok and prev_usage is not None and key in prev_usage and usage <= prev_usage[key] \
                and game_minutes_elapsed - prev_usage.get("_elapsed", 0) >= GAME[sport][0] / 8:
            ok, why = False, "no usage since last break"
        if ok and usage <= 0:
            ok, why = False, "no usage yet"
        if prev_usage is not None:
            prev_usage[key] = usage
        out = {"player": row["player"], "team": pl["team"], "market": row["market"],
               "label": sp[row["market"]].label, "age": row["age"], "usage": usage, **ev,
               "recommended": ok, "reason": why}
        priced.append(out)
        if ok:
            recs.append(out)
    if prev_usage is not None:
        prev_usage["_elapsed"] = game_minutes_elapsed
    recs.sort(key=lambda r: -r["ev"])
    picked, seen = [], set()
    for r in recs:
        if r["player"] in seen:
            continue
        seen.add(r["player"])
        picked.append(r)
        if len(picked) >= int(_env("LIVE_PROP_MAX_PER_GAME", 3)):
            break
    return priced, picked


# ---------------------------------------------------------------- messages


def _price(p: float) -> str:
    return f"{p:+.0f}"


def alert_text(sport: str, game_label: str, period_label: str, score: str, recs: list[dict]) -> str:
    lines = [f"🎯 <b>LIVE PROPS · Bovada</b> · {sport.upper()} · PAPER",
             f"{game_label} · {period_label} · {score}"]
    for r in recs:
        lines.append(f"• <b>{r['player']}</b> {r['side']} {r['line']:g} {r['label'].lower()} ({_price(r['price'])})"
                     f"\n   has {r['so_far']:g} · Marv proj {r['proj']:.1f} · fair {r['p']:.0%} vs Bovada "
                     f"{r['market_p']:.0%} · edge {r['edge']:+.0%} · pace x{r['pace']:.2f}"
                     + (f" · script x{r['script']:.2f}" if abs(r['script'] - 1) >= 0.03 else ""))
    lines.append("<i>Paper: live props are untested vs real results. Line may move; only bet the number shown or better.</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------- ledger + grading


def _dir(state_dir: Path) -> Path:
    d = Path(state_dir) / "live_props"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_shadow(state_dir: Path, sport: str, game_id: str, period: str, priced: list[dict]) -> None:
    at = datetime.now(timezone.utc).isoformat()
    with (_dir(state_dir) / "shadow.jsonl").open("a") as fh:
        for r in priced:
            fh.write(json.dumps({"at": at, "sport": sport, "game_id": game_id, "period": period,
                                 **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}}) + "\n")


def _recs_path(state_dir: Path) -> Path:
    return _dir(state_dir) / "recs.json"


def load_recs(state_dir: Path) -> list[dict]:
    p = _recs_path(state_dir)
    return json.loads(p.read_text()) if p.exists() else []


def save_recs(state_dir: Path, book: list[dict]) -> None:
    _recs_path(state_dir).write_text(json.dumps(book[-5000:], indent=1))


def record_recs(state_dir: Path, sport: str, game_id: str, game_label: str, period: str, recs: list[dict]) -> list[dict]:
    """Adds recs not already sent for this game (same player/market/side); returns the new ones."""
    book = load_recs(state_dir)
    seen = {(b["game_id"], _norm(b["player"]), b["market"], b["side"]) for b in book}
    new = [r for r in recs if (str(game_id), _norm(r["player"]), r["market"], r["side"]) not in seen]
    at = datetime.now(timezone.utc).isoformat()
    for r in new:
        book.append({"at": at, "sport": sport, "game_id": str(game_id), "game": game_label, "period": period,
                     "player": r["player"], "market": r["market"], "label": r["label"], "side": r["side"],
                     "line": r["line"], "price": r["price"], "p": round(r["p"], 4), "market_p": round(r["market_p"], 4),
                     "edge": round(r["edge"], 4), "so_far": r["so_far"], "proj": round(r["proj"], 2),
                     "result": None})
    if new:
        save_recs(state_dir, book)
    return new


def grade_result(side: str, line: float, final: float) -> str:
    if final == line:
        return "push"
    return "win" if (final > line) == (side == "Over") else "loss"


def grade_game(state_dir: Path, sport: str, game_id: str, summary: dict) -> list[dict]:
    """Grades this game's open recs against the final box score; returns the graded rows."""
    book = load_recs(state_dir)
    box = parse_box(summary)["players"]
    by = {_norm(n): p for n, p in box.items()}
    done = []
    for b in book:
        if b["game_id"] != str(game_id) or b.get("result"):
            continue
        pl = by.get(_norm(b["player"]))
        if not pl:
            b["result"] = "void"  # didn't play / not in the box: Bovada voids or grades by its own rule
            done.append(b)
            continue
        val, _ = stat_value(sport, specs(sport)[b["market"]], pl)
        b["final"] = float(val or 0)
        b["result"] = grade_result(b["side"], b["line"], b["final"])
        b["units"] = round(payout(b["price"]), 4) if b["result"] == "win" else (-1.0 if b["result"] == "loss" else 0.0)
        done.append(b)
    if done:
        save_recs(state_dir, book)
    return done


def record(state_dir: Path, sport: str | None = None) -> dict:
    rows = [b for b in load_recs(state_dir) if b.get("result") in ("win", "loss", "push") and (not sport or b["sport"] == sport)]
    w = sum(b["result"] == "win" for b in rows)
    l = sum(b["result"] == "loss" for b in rows)
    units = sum(b.get("units", 0) for b in rows)
    staked = w + l
    return {"n": len(rows), "wins": w, "losses": l, "pushes": len(rows) - w - l,
            "win_rate": w / staked if staked else None, "units": round(units, 2), "roi": units / staked if staked else None,
            "proven": staked >= 100 and w / staked > 0.524 and units > 0}


def final_text(game_label: str, graded: list[dict], rec: dict) -> str:
    lines = [f"🎯 <b>LIVE PROPS · final</b> · {game_label}"]
    icon = {"win": "✅", "loss": "❌", "push": "➖", "void": "⚪"}
    for b in graded:
        lines.append(f"{icon.get(b['result'], '?')} {b['player']} {b['side']} {b['line']:g} {b['label'].lower()}"
                     + (f" · final {b['final']:g}" if b.get("final") is not None else " · void"))
    if rec["n"]:
        lines.append(f"Live-prop record (paper): {rec['wins']}-{rec['losses']}" + (f"-{rec['pushes']}" if rec["pushes"] else "")
                     + f" · {rec['units']:+.2f}u")
    return "\n".join(lines)


def report(state_dir: Path) -> str:
    out = ["Live props (paper) record"]
    for sport in (None, "nfl", "cfb", "nba", "wnba", "ncaab", "ncaaw"):
        r = record(state_dir, sport)
        if sport and not r["n"]:
            continue
        wr = f"{r['win_rate']:.1%}" if r["win_rate"] is not None else "-"
        roi = f"{r['roi']:+.1%}" if r["roi"] is not None else "-"
        out.append(f"  {(sport or 'all'):6s} {r['wins']}-{r['losses']}-{r['pushes']}  win {wr}  {r['units']:+.2f}u  ROI {roi}"
                   + ("  PROVEN BAR MET" if r["proven"] else ""))
    out.append("Bar before this is more than paper: 100+ graded, win rate > 52.4%, positive ROI.")
    return "\n".join(out)


# ---------------------------------------------------------------- live-monitor hook


class Runner:
    """Fetches Bovada live props for a game at a break, scans, logs, sends. Keeps a daily Odds API credit cap."""

    def __init__(self, settings, client):
        self.s, self.client = settings, client
        self.dir = _dir(Path(settings.state_dir))

    def enabled(self, sport: str) -> bool:
        on = os.environ.get("LIVE_PROPS_ENGINE", "on").lower() not in ("0", "off", "false", "no")
        return on and bool(self.s.odds_api_key) and sport in ODDS_KEYS

    def _budget_ok(self, cost: int) -> bool:
        p = self.dir / "credits.json"
        today = datetime.now(timezone.utc).date().isoformat()
        data = json.loads(p.read_text()) if p.exists() else {}
        used = data.get(today, 0)
        if used + cost > int(_env("LIVE_PROPS_MAX_CREDITS", 600)):
            return False
        p.write_text(json.dumps({today: used + cost}))
        return True

    def _event(self, sport: str, home: str, away: str) -> dict | None:
        from .live_props import _event_for
        from .props import odds as O
        return _event_for(O.events(self.s.odds_api_key, ODDS_KEYS[sport]), home, away)

    def _odds(self, sport: str, event_id: str) -> dict:
        from .props import odds as O
        return O.event_props(self.s.odds_api_key, ODDS_KEYS[sport], event_id, list(specs(sport)), "bovada")

    def capture_pregame(self, sport: str, home: str, away: str) -> int:
        """Before kickoff: save Bovada's pregame lines as the anchor for players Marv didn't project."""
        if not self._budget_ok(len(specs(sport))):
            return 0
        ev = self._event(sport, home, away)
        if not ev:
            return 0
        rows = bovada_lines(self._odds(sport, ev["id"]), max_age=0)
        lines = [{"player": r["player"], "market": r["market"], "line": r["line"]} for r in rows]
        if lines:
            save_pregame(Path(self.s.state_dir), ev, lines)
        return len(lines)

    def on_period(self, sport: str, game, elapsed: float, period_label: str, mem: dict, send, summary_path: str) -> int:
        """Called at a break. `mem` is this game's live-monitor state dict (persists usage between breaks)."""
        if not self._budget_ok(len(specs(sport))):
            log.info("live props: daily credit cap reached")
            return 0
        ev = self._event(sport, game.home, game.away)
        if not ev:
            return 0
        state_dir = Path(self.s.state_dir)
        summary = self.client.summary(summary_path, str(game.id))
        odds = self._odds(sport, ev["id"])
        hs, as_ = float(game.info.get("live_home") or 0), float(game.info.get("live_away") or 0)
        usage = mem.setdefault("prop_usage", {})
        priced, recs = scan_game(sport, summary, odds, anchors(state_dir, sport, ev["id"]), elapsed,
                                 game.home, game.away, hs, as_, usage)
        log_shadow(state_dir, sport, str(game.id), period_label, priced)
        label = f"{game.away} @ {game.home}"
        new = record_recs(state_dir, sport, str(game.id), label, period_label, recs)
        mem["prop_recs"] = mem.get("prop_recs", 0) + len(new)
        if new and os.environ.get("LIVE_PROPS_SEND", "on").lower() not in ("0", "off", "false", "no"):
            send(alert_text(sport, label, period_label, f"{game.away} {as_:.0f} – {hs:.0f} {game.home}", new))
            return 1
        return 0

    def on_final(self, sport: str, game, send, summary_path: str) -> int:
        state_dir = Path(self.s.state_dir)
        if not any(b["game_id"] == str(game.id) and not b.get("result") for b in load_recs(state_dir)):
            return 0
        graded = grade_game(state_dir, sport, str(game.id), self.client.summary(summary_path, str(game.id)))
        if graded and os.environ.get("LIVE_PROPS_SEND", "on").lower() not in ("0", "off", "false", "no"):
            send(final_text(f"{game.away} {float(game.away_score):.0f} – {float(game.home_score):.0f} {game.home}",
                            graded, record(state_dir)))
            return 1
        return 0


def elapsed_minutes(sport: str, ended_period: int) -> float:
    from .live_monitor import STRUCTURE
    n, length, _, _ = STRUCTURE[sport]
    return float(min(ended_period, n) * length)


def summary_path(sport: str) -> str:
    from .live_monitor import STRUCTURE
    return STRUCTURE[sport][3]
