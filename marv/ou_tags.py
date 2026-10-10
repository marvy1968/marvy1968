"""Over/under trend tags: shown on cards and logged for paper tracking, never used to make picks.

From the backtests in ANALYSIS.md ("Team over/under trends", "Continuation or reversal", "Line movement"):
following a team's over/under streak loses; the market tends to over-adjust to recent scoring, so the
tags back the other side. "Over trend" = both teams went over the closing total in 2+ of their last 3.

  OVER-FADE           college: over trend -> UNDER (54.9% of 994, 2017-26)
  OVER-FADE+MOVE      college: over trend and the total rose 1.5+ from the open -> UNDER (56.7% of 298)
  OVER-FADE+INFLATED  college: over trend and the total sits 2+ pts above what the teams' recent yardage
                      justifies -> UNDER (56.5% of 310)
  UNDER-FADE          college: both teams under in 2+ of their last 3 -> OVER (51-54% depending on the line source)
  SCORE-GRADE         college: scoring grade 59.1+ (two strong offenses vs two weak defenses, marv/scoregrade.py) -> UNDER
                      (55.7% of 436 held-out 2022-25 games; same family as OVER-FADE)
  TOTAL-INFLATED      NFL: the total sits 5+ pts above what recent yardage justifies -> UNDER (55.8% of 400)

None is a proven edge (about 70 rules were tested to find these); `python -m marv situational` grades
them weekly against the last total logged before kickoff (52.4% needed at -110).
"""

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)
HISTORY = {
    "OVER-FADE": "54.9% 2017-26 (994)",
    "OVER-FADE+MOVE": "56.7% 2014-25 (298)",
    "OVER-FADE+INFLATED": "56.5% 2017-26 (310)",
    "UNDER-FADE": "51-54% 2014-26",
    "TOTAL-INFLATED": "55.8% 2009-26 (400)",
    "SCORE-GRADE": "55.7% 2022-25 holdout (436)",
}
SPORT_TAGS = {"cfb": ("OVER-FADE", "OVER-FADE+MOVE", "OVER-FADE+INFLATED", "UNDER-FADE", "SCORE-GRADE"), "nfl": ("TOTAL-INFLATED",)}
PRIOR_GAMES = 4.0  # last season's average counts as this many games in the baseline


def _yards(tg: pd.DataFrame) -> pd.Series:
    for col in ("yards", "totalYards"):
        if col in tg:
            return tg[col].astype(float)
    if "passing_yards" in tg and "rushing_yards" in tg:
        return tg["passing_yards"].fillna(0) + tg["rushing_yards"].fillna(0)
    return pd.Series(np.nan, index=tg.index)


def team_games(games: pd.DataFrame, tg: pd.DataFrame) -> pd.DataFrame:
    """One row per finished team-game: date, season, closing total, game points and game yardage."""
    t = tg[["game_id", "team", "opp", "points"]].assign(yards=_yards(tg))
    t = t.merge(t.rename(columns={"team": "opp", "opp": "team", "points": "o_pts", "yards": "o_yds"}),
                on=["game_id", "team", "opp"])
    t = t.merge(games[["game_id", "date", "season", "total"]], on="game_id")
    t["g_pts"] = t["points"] + t["o_pts"]
    t["g_yds"] = t["yards"] + t["o_yds"]
    t = t[t["g_pts"].notna()].copy()
    t["date"] = pd.to_datetime(t["date"])
    t["over"] = np.sign(t["g_pts"] - t["total"])
    return t.sort_values(["date", "game_id"])


def team_form(hist: pd.DataFrame, team: str, season: int, before) -> dict | None:
    """Last-3 over count, baseline total and yardage change for one team before a date (None if < 3 games)."""
    d = hist[(hist["team"] == team) & (hist["date"] < before)]
    cur = d[d["season"] == season]
    if len(cur) < 3:
        return None
    last3, older = cur.tail(3), cur.iloc[:-3]
    prev = d[d["season"] == season - 1]
    parts, weights = [], []
    if len(prev):
        parts.append(prev[["total", "g_yds"]].mean()); weights.append(PRIOR_GAMES)
    if len(older):
        parts.append(older[["total", "g_yds"]].mean()); weights.append(len(older))
    if not parts:
        return None
    base = pd.concat(parts, axis=1).T
    base = (base.mul(weights, axis=0).sum() / base.notna().mul(weights, axis=0).sum())
    lines = last3["over"].dropna()
    return {"overs": int((lines > 0).sum()), "graded": int((lines != 0).sum()),
            "base_total": float(base["total"]), "d_yds": float(last3["g_yds"].mean() - base["g_yds"])}


def tags_for_game(sport: str, home: dict | None, away: dict | None, total: float | None,
                  total_open: float | None, ppy: float) -> list[tuple[str, str]]:
    """[(tag, side)] for one game from both teams' form and the current/opening total."""
    if total is None or home is None or away is None:
        return []
    out = []
    inflated = None
    if not np.isnan(home["base_total"]) and not np.isnan(away["base_total"]):
        market_move = total - (home["base_total"] + away["base_total"]) / 2
        yardage_move = ppy * (home["d_yds"] + away["d_yds"]) / 2 if ppy else 0.0
        inflated = market_move - yardage_move if not np.isnan(yardage_move) else None
    if sport == "cfb":
        over_trend = home["overs"] >= 2 and away["overs"] >= 2
        under_trend = home["graded"] == 3 and away["graded"] == 3 and home["overs"] <= 1 and away["overs"] <= 1
        if over_trend:
            out.append(("OVER-FADE", "Under"))
            if total_open is not None and total - total_open >= 1.5:
                out.append(("OVER-FADE+MOVE", "Under"))
            if inflated is not None and inflated >= 2:
                out.append(("OVER-FADE+INFLATED", "Under"))
        if under_trend:
            out.append(("UNDER-FADE", "Over"))
    elif sport == "nfl" and inflated is not None and inflated >= 5:
        out.append(("TOTAL-INFLATED", "Under"))
    return out


def add_recent(hist: pd.DataFrame, games: pd.DataFrame, recent) -> pd.DataFrame:
    """Add finished games the stats tables don't have yet (e.g. last weekend, before the free
    play-by-play release catches up) from the schedule feed's scores and closing totals."""
    from .roster_notes import match_team
    names = pd.concat([games["home"], games["away"]]).dropna().unique()
    have = set(games["game_id"].astype(str))
    rows = []
    for g in recent or []:
        if not g.completed or g.home_score is None or not g.odds or g.odds.total is None or str(g.id) in have:
            continue
        teams = [t for t in (match_team(g.home, names), match_team(g.away, names)) if t]
        when = pd.Timestamp(g.start).tz_convert(None) if pd.Timestamp(g.start).tzinfo else pd.Timestamp(g.start)
        pts = g.home_score + g.away_score
        season = int(games.loc[games["date"] <= when, "season"].max()) if (games["date"] <= when).any() else when.year
        for team in teams:
            if not hist[(hist["team"] == team) & ((hist["date"] - when).abs() < pd.Timedelta(days=2))].empty:
                continue  # already there under another id
            rows.append({"game_id": str(g.id), "team": team, "date": when, "season": season, "total": g.odds.total,
                         "g_pts": pts, "g_yds": np.nan, "over": float(np.sign(pts - g.odds.total))})
    if not rows:
        return hist
    return pd.concat([hist, pd.DataFrame(rows)], ignore_index=True).sort_values(["date", "game_id"])


def tag_slate(sport: str, games: pd.DataFrame, tg: pd.DataFrame, slate, id_map: dict,
              recent=None) -> dict[str, list[tuple[str, str]]]:
    """Tags for every slate game (keyed by the slate game id). Module team names come from `games`;
    `recent` (finished Game objects from the schedule feed) fills results the stats tables lack."""
    if sport not in SPORT_TAGS or "total" not in games:
        return {}
    hist = add_recent(team_games(games, tg), games.assign(date=pd.to_datetime(games["date"])), recent)
    done = hist[hist["g_yds"].notna()]
    ppy = float(done["g_pts"].sum() / done["g_yds"].sum()) if len(done) and done["g_yds"].sum() else 0.0
    lookup = games.set_index("game_id")
    grades: dict = {}
    if sport == "cfb":
        try:
            from . import scoregrade
            grades = scoregrade.game_grades(games, tg)
        except Exception:
            log.exception("scoring grade failed")
    out = {}
    for g in slate:
        gid = id_map.get(g.id)
        if gid is None or gid not in lookup.index or not g.odds or g.odds.total is None:
            continue
        if g.info.get("home_fbs") is False or g.info.get("away_fbs") is False:
            continue  # the trend backtests are FBS only (CFBD lists FCS and lower games too)
        row = lookup.loc[gid]
        when = pd.Timestamp(row["date"])
        season = int(row["season"])
        h = team_form(hist, row["home"], season, when)
        a = team_form(hist, row["away"], season, when)
        tags = tags_for_game(sport, h, a, g.odds.total, g.odds.total_open, ppy)
        sg = grades.get(gid)
        if sg is not None and sg >= 59.1:
            tags.append(("SCORE-GRADE", "Under"))
        if tags:
            out[g.id] = tags
    return out


def note(tags: list[tuple[str, str]], sport: str | None = None) -> str:
    from . import proven

    def hist(t):
        sp = sport or ("cfb" if t in SPORT_TAGS["cfb"] else "nfl")
        return proven.pct(sp, "total", signal=t) if proven.gated(sp) else HISTORY[t]
    return "O/U spots (tracked, not picks): " + ", ".join(f"{t}→{side} [{hist(t)}]" for t, side in tags)


def log(state: Path, sport: str, slate, tags: dict) -> None:
    """Log tags; later runs before kickoff update the line so grading uses the last total seen (≈ close)."""
    path = state / "ou_tags_log.json"
    book = json.loads(path.read_text()) if path.exists() else {}
    games = {g.id: g for g in slate}
    for gid, items in tags.items():
        g = games[gid]
        for tag, side in items:
            entry = book.setdefault(f"{sport}:{gid}:{tag}", {
                "sport": sport, "game_id": gid, "tag": tag, "side": side, "home": g.home, "away": g.away,
                "start": g.start.isoformat(), "open": g.odds.total_open, "result": None})
            if entry["result"] is None:
                entry["line"] = g.odds.total
    path.write_text(json.dumps(book, indent=1))


def grade(state: Path, sport: str, finished) -> dict:
    """Grade logged tags against final scores (Game objects). Returns {tag: [wins, losses]} for all sports."""
    path = state / "ou_tags_log.json"
    if not path.exists():
        return {}
    book = json.loads(path.read_text())
    scores = {g.id: (g.home_score, g.away_score) for g in finished
              if g.completed and g.home_score is not None and g.away_score is not None}
    for b in book.values():
        if b["sport"] == sport and b["result"] is None and b["game_id"] in scores and b.get("line") is not None:
            pts = sum(scores[b["game_id"]])
            if pts == b["line"]:
                b["result"] = "push"
            else:
                b["result"] = "win" if (pts > b["line"]) == (b["side"] == "Over") else "loss"
    path.write_text(json.dumps(book, indent=1))
    return record(state)


def record(state: Path) -> dict:
    path = state / "ou_tags_log.json"
    book = json.loads(path.read_text()) if path.exists() else {}
    rec: dict[str, list[int]] = {}
    for b in book.values():
        if b["result"] in ("win", "loss"):
            r = rec.setdefault(f"{b['sport']} {b['tag']}", [0, 0])
            r[0 if b["result"] == "win" else 1] += 1
    return rec


def report(rec: dict) -> str:
    if not rec:
        return "O/U trend tags: nothing graded yet."
    lines = ["📉 O/U trend tags (paper tracking vs the last total before kickoff; need 52.4%+ over 100+ games)"]
    for tag, (w, l) in sorted(rec.items()):
        lines.append(f"{tag}: {w}-{l} ({w / (w + l):.1%})")
    return "\n".join(lines)
