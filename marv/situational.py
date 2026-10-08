"""NFL situational tags: shown on game cards and logged for paper tracking, never used to make picks.

Each tag was backtested against closing spreads (ANALYSIS.md, "Situational spots"); none was a proven edge.
They're logged with the closing spread so `python -m marv situational` can grade them each week and show
whether any angle starts beating 52.4% on new games.

Tags (side = the team the angle backs):
  PT-WIN   team beat the closing number by 14+ in a prime-time game last week (backing them went 54.8% 2006-26)
  PT-LOSS  team missed the number by 14+ in prime time last week (50% historically)
  REST+4   team has a 4+ day rest edge
  WEST@1PM West Coast visitor at a 1:00 PM ET kickoff in the Eastern time zone (backs the home team)
  REST+TZ  4+ day rest edge and 2+ time zones of travel for the opponent
  HOMEDOG  home underdog getting more than 3.5 (non-division: 55.4% 2006-26, 46.8% 2024-26)
  DIVDOG   divisional home underdog getting more than 3.5
  KEY3/KEY7 spread sits on 3 or 7 (backs the underdog)
  MARV4    Marv's projected margin is 4+ points off the closing spread (backs Marv's side; 54.4% of 463 in
           2016-26, p~0.19: the one model angle worth paper-tracking)
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .data.teams import NFL_TEAMS

TZ = {**{t: -8 for t in ("LA", "LAC", "SF", "SEA", "LV", "OAK", "SD")}, **{t: -7 for t in ("DEN", "ARI")},
      **{t: -6 for t in ("CHI", "DAL", "HOU", "KC", "MIN", "NO", "GB", "TEN", "STL")}}
HISTORY = {"PT-WIN": "54.8% ATS 2006-26 (310)", "PT-LOSS": "50% ATS", "REST+4": "51% ATS", "WEST@1PM": "48% ATS",
           "REST+TZ": "51% ATS, inconsistent", "HOMEDOG": "55.4% ATS 2006-26, 46.8% 2024-26", "DIVDOG": "50% ATS",
           "KEY3": "dog +3: 54% ATS", "KEY7": "dog +7: 49% ATS",
           "MARV4": "Marv 4+ pts off the spread: 54.4% ATS 2016-26 (463)"}


def tz(team: str) -> int:
    return TZ.get(team, -5)


def tags_for(games: pd.DataFrame) -> pd.DataFrame:
    """One row per (game_id, tag, side) for games without a result yet, using only earlier results."""
    g = games.copy()
    g["prime"] = g["weekday"].isin(["Monday", "Thursday"]) | (g["gametime"].astype(str) >= "19:00")
    g["ats"] = g["result"] - g["spread_line"]
    long = pd.concat([
        g[["season", "gameday", "home_team", "prime", "ats"]].set_axis(["season", "gameday", "team", "prime", "ats"], axis=1),
        g[["season", "gameday", "away_team", "prime", "ats"]].assign(ats=lambda x: -x["ats"]).set_axis(
            ["season", "gameday", "team", "prime", "ats"], axis=1)]).dropna(subset=["ats"]).sort_values("gameday")
    last = long.groupby(["season", "team"]).tail(1).set_index(["season", "team"])
    rows = []
    for r in g[g["result"].isna() & g["spread_line"].notna()].itertuples():
        def add(tag, side):
            rows.append({"game_id": r.game_id, "season": r.season, "week": r.week, "tag": tag, "side": side,
                         "spread_line": r.spread_line, "home": r.home_team, "away": r.away_team})
        for team in (r.home_team, r.away_team):
            if (r.season, team) in last.index:
                prev = last.loc[(r.season, team)]
                if bool(prev["prime"]) and prev["ats"] >= 14:
                    add("PT-WIN", team)
                if bool(prev["prime"]) and prev["ats"] <= -14:
                    add("PT-LOSS", team)
        rest = (r.home_rest or 0) - (r.away_rest or 0)
        if abs(rest) >= 4:
            add("REST+4", r.home_team if rest > 0 else r.away_team)
            if abs(tz(r.home_team) - tz(r.away_team)) >= 2 and r.location == "Home":
                add("REST+TZ", r.home_team if rest > 0 else r.away_team)
        if tz(r.away_team) == -8 and tz(r.home_team) == -5 and str(r.gametime) == "13:00" and r.location == "Home":
            add("WEST@1PM", r.home_team)
        if r.spread_line < -3.5 and r.location == "Home":
            add("DIVDOG" if r.div_game == 1 else "HOMEDOG", r.home_team)
        if abs(r.spread_line) in (3.0, 7.0):
            add(f"KEY{int(abs(r.spread_line))}", r.home_team if r.spread_line < 0 else r.away_team)
    return pd.DataFrame(rows)


def note(tags: pd.DataFrame, game_id: str, name=lambda a: a) -> str:
    t = tags[tags["game_id"] == game_id] if not tags.empty else tags
    if t is None or t.empty:
        return ""
    return "spots (tracked, not picks): " + ", ".join(f"{x.tag}→{name(x.side)} [{HISTORY.get(x.tag, '')}]" for x in t.itertuples())


def log(state: Path, tags: pd.DataFrame) -> None:
    path = state / "situational_log.json"
    book = json.loads(path.read_text()) if path.exists() else {}
    for x in tags.itertuples():
        key = f"{x.game_id}:{x.tag}:{x.side}"
        book.setdefault(key, {"game_id": x.game_id, "season": int(x.season), "week": int(x.week), "tag": x.tag,
                              "side": x.side, "spread_line": float(x.spread_line), "home": x.home, "away": x.away,
                              "result": None})
    path.write_text(json.dumps(book, indent=1))


def grade(state: Path, games: pd.DataFrame) -> dict:
    """Grade logged tags against the spread at the time they were logged. Returns {tag: [wins, losses]}."""
    path = state / "situational_log.json"
    book = json.loads(path.read_text()) if path.exists() else {}
    res = games.set_index("game_id")["result"]
    rec: dict[str, list[int]] = {}
    for b in book.values():
        if b["result"] is None and b["game_id"] in res.index and pd.notna(res[b["game_id"]]):
            margin = res[b["game_id"]] - b["spread_line"]  # home vs the number
            if margin != 0:
                b["result"] = "win" if (margin > 0) == (b["side"] == b["home"]) else "loss"
            else:
                b["result"] = "push"
        if b["result"] in ("win", "loss"):
            r = rec.setdefault(b["tag"], [0, 0])
            r[0 if b["result"] == "win" else 1] += 1
    path.write_text(json.dumps(book, indent=1))
    return rec


def report(rec: dict) -> str:
    if not rec:
        return "Situational tags: nothing graded yet."
    lines = ["🧭 Situational tags (paper tracking, ATS vs the logged spread; need 52.4%+ over 100+ games)"]
    for tag, (w, l) in sorted(rec.items()):
        lines.append(f"{tag}: {w}-{l} ({w / (w + l):.1%})")
    return "\n".join(lines)


def team_name(abbr: str) -> str:
    return NFL_TEAMS.get(abbr, abbr)


def marv_tags(preds, games: pd.DataFrame) -> pd.DataFrame:
    """MARV4: Marv's projected margin differs from the posted spread by 4+ points."""
    rows = []
    open_games = games[games["result"].isna()]
    for p in preds:
        o = p.game.odds
        if not o or o.spread is None:
            continue
        diff = p.model_margin + o.spread  # > 0: Marv has the home team covering
        if abs(diff) < 4:
            continue
        m = open_games[(open_games["home_team"].map(team_name) == p.game.home) & (open_games["away_team"].map(team_name) == p.game.away)]
        if m.empty:
            continue
        r = m.iloc[0]
        rows.append({"game_id": r.game_id, "season": r.season, "week": r.week, "tag": "MARV4",
                     "side": r.home_team if diff > 0 else r.away_team, "spread_line": -o.spread,
                     "home": r.home_team, "away": r.away_team})
    return pd.DataFrame(rows)


def attach(preds, games: pd.DataFrame, state: Path) -> None:
    """Add tag notes to NFL predictions (matched by full team names) and log them for grading."""
    tags = pd.concat([tags_for(games), marv_tags(preds, games)], ignore_index=True)
    if tags.empty:
        return
    log(state, tags)
    for p in preds:
        gid = next((t.game_id for t in tags.itertuples()
                    if team_name(t.home) == p.game.home and team_name(t.away) == p.game.away), None)
        if gid:
            p.notes.append(note(tags, gid, team_name))
