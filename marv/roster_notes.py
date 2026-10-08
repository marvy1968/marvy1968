"""One "roster:" line per game for the cards, every run.

NFL: regular starters (50%+ snaps over the last 4 games) ruled Out/Doubtful this week and whether the
starting QB is out (nflverse injury report + snap counts, the same numbers the elimination rule uses).
College: there is no public college injury report, so the check is a QB change from play-by-play: the
passer with the most attempts in the team's latest game vs the team's season leader in attempts.
"""

import pandas as pd

from .data.teams import similarity


def nfl_note(tg: pd.DataFrame, gid: str, home_team: str, home: str, away: str) -> str:
    rows = tg[tg["game_id"] == gid].set_index("team")
    parts = []
    for team, label in ((home_team, home), (rows.index[rows.index != home_team][0] if len(rows) == 2 else None, away)):
        if team is None or team not in rows.index:
            continue
        r = rows.loc[team]
        out, snaps, qb = r.get("my_starters_out", 0) or 0, r.get("my_snaps_lost", 0) or 0, r.get("my_qb_out", 0) or 0
        if out or qb:
            parts.append(f"{label} {int(out)} starters out ({snaps:.1f} full-time){' + QB OUT' if qb else ''}"
                         f"{' ⚠️ elimination' if snaps >= 1.5 or qb else ''}")
        else:
            parts.append(f"{label} no regular starters out")
    return "roster: " + "; ".join(parts) if parts else ""


def cfb_qb_changes(players: pd.DataFrame, games: pd.DataFrame) -> dict[str, str]:
    """{team: note} for teams whose latest game's main passer isn't their season leader in attempts."""
    if players is None or players.empty or "attempts" not in players:
        return {}
    p = players[players["attempts"].fillna(0) > 0].merge(games[["game_id", "date"]].astype({"game_id": str}),
                                                         on="game_id", how="left")
    out = {}
    for team, d in p.groupby("team"):
        last_game = d.sort_values("date")["game_id"].iloc[-1]
        last = d[d["game_id"] == last_game].sort_values("attempts").iloc[-1]
        leader = d.groupby("player")["attempts"].sum().idxmax()
        if last["player"] != leader:
            out[team] = f"QB change? {last['player']} threw most last game, season leader {leader}"
    return out


def cfb_note(changes: dict[str, str], home_team: str, away_team: str, home: str, away: str) -> str:
    parts = [f"{label}: {changes[t]}" for t, label in ((home_team, home), (away_team, away)) if t in changes]
    return "roster: " + ("; ".join(parts) if parts else "same starting QBs as their season leaders") + \
        " (no college injury feed: check team news)"


def match_team(name: str, names) -> str | None:
    best = max(names, key=lambda n: similarity(name, n), default=None)
    return best if best is not None and similarity(name, best) >= 0.75 else None
