"""Injury trends: when a team's situation changes (new starting QB, its No. 1 receiver out), its scoring
since that change becomes a new trend, and the projection moves toward it.

  * QB change: the latest starter isn't the team's season leader in starts. The trend is the run of
    games he has started since taking over.
  * WR1 out: the team's top receiver by targets is ruled Out/Doubtful this week or missed the latest
    game(s). The trend is the games he missed this season.

The team's points per game in those games vs its other games this season gives a ratio, shrunk by the
sample size (weight n / (n + 2): one game moves the projection a third of the way, four games two
thirds), capped at +/-25%, and the team's projected points are scaled by it. Opponents aren't adjusted
for, so it's a trend, not a rating. With no games yet in the new situation the
projection is unchanged and the note says so. NFL uses nflverse weekly player stats; college uses the
play-by-play passer table (QB changes only).
"""

from dataclasses import dataclass

import pandas as pd

SHRINK_GAMES = 2.0
MAX_MOVE = 0.25  # a handful of games is noisy: never move a team's projection more than 25%


@dataclass
class Regime:
    team: str
    kind: str  # "QB" or "WR1"
    who: str  # the new starter, or the missing receiver
    games: list  # game ids in the new situation (this season)
    note: str = ""


def starters_from_passes(passes: pd.DataFrame) -> pd.DataFrame:
    """passes: team, game_id, date, player, attempts -> one row per team-game with the starting QB."""
    p = passes[passes["attempts"].fillna(0) > 0]
    return p.sort_values("attempts").groupby(["team", "game_id"]).tail(1).sort_values("date")[["team", "game_id", "date", "player"]]


def qb_regimes(starters: pd.DataFrame) -> dict[str, Regime]:
    out = {}
    for team, d in starters.groupby("team"):
        d = d.sort_values("date")
        current = d["player"].iloc[-1]
        leader = d["player"].value_counts().idxmax()
        if current == leader:
            continue
        streak = []
        for r in d.iloc[::-1].itertuples():
            if r.player != current:
                break
            streak.append(r.game_id)
        out[team] = Regime(team, "QB", current, streak[::-1])
    return out


def wr1_regimes(receiving: pd.DataFrame, ruled_out: set[tuple[str, str]]) -> dict[str, Regime]:
    """receiving: team, game_id, date, player, targets (every team-game in `receiving` was played).
    ruled_out: {(team, player)} Out/Doubtful this week."""
    out = {}
    for team, d in receiving.groupby("team"):
        games = d.drop_duplicates("game_id").sort_values("date")["game_id"].tolist()
        wr1 = d.groupby("player")["targets"].sum().idxmax()
        played = set(d.loc[(d["player"] == wr1) & (d["targets"].fillna(0) > 0), "game_id"])
        missed_recent = []
        for gid in games[::-1]:
            if gid in played:
                break
            missed_recent.append(gid)
        if (team, wr1) in ruled_out or missed_recent:
            missed = [g for g in games if g not in played]
            out[team] = Regime(team, "WR1", wr1, missed)
    return out


def trend_factor(points: pd.DataFrame, regime: Regime) -> tuple[float, str]:
    """points: team, game_id, points (this season, finished games). Returns (factor, explanation)."""
    d = points[points["team"] == regime.team]
    new = d[d["game_id"].isin(regime.games)]["points"]
    old = d[~d["game_id"].isin(regime.games)]["points"]
    what = f"{regime.who} starting at QB" if regime.kind == "QB" else f"WR1 {regime.who} out"
    if new.empty or old.empty or old.mean() <= 0:
        return 1.0, f"injury trend: {regime.team} {what}, no games yet in the new situation (watch it)"
    n = len(new)
    w = n / (n + SHRINK_GAMES)
    ratio = new.mean() / old.mean()
    factor = min(max(1 + w * (ratio - 1), 1 - MAX_MOVE), 1 + MAX_MOVE)
    return factor, (f"injury trend: {regime.team} {what}: {new.mean():.1f} pts/game in {n} game{'s' if n != 1 else ''} "
                    f"vs {old.mean():.1f} before → projection x{factor:.2f}")


def adjust(proj_home: float, proj_away: float, home: str, away: str, regimes: dict[str, list[Regime]],
           points: pd.DataFrame) -> tuple[float, float, list[str]]:
    """Scale each team's projected points by its injury-trend factors; returns (home, away, notes)."""
    notes = []
    out = {}
    for team, proj in ((home, proj_home), (away, proj_away)):
        f = 1.0
        for r in regimes.get(team, []):
            fac, note = trend_factor(points, r)
            f *= fac
            notes.append(note)
        out[team] = proj * f
    return out[home], out[away], notes
