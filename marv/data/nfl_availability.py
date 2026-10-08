"""NFL roster availability before each game: which regular starters are ruled Out or Doubtful.

A "regular" is a player who averaged at least 50% of his unit's snaps over his team's previous 4
games (nflverse snap counts). The game-week injury report (nflverse injuries) says who is Out or
Doubtful. Per team and week:
  qb_out       the team's regular quarterback is Out/Doubtful
  starters_out number of regular starters Out/Doubtful
  snaps_lost   sum of their usual snap shares (a lineman at 100% counts 1.0)
Everything uses only the report for that week and earlier games, so it's known before kickoff.
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..stats.base import fetch

INJ_URL = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.csv"
SNAP_URL = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv"
_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?")
TEAM_FIX = {"LA": "LA", "LAR": "LA", "STL": "LA", "SD": "LAC", "OAK": "LV"}


def _name(n: str) -> str:
    n = str(n).lower().replace(".", "").replace("'", "").replace("-", " ")
    return " ".join(_SUFFIX.sub("", n).split())


def _team(t: str) -> str:
    return TEAM_FIX.get(str(t), str(t))


def availability(cache: Path, seasons: list[int], current: int | None = None) -> pd.DataFrame:
    rows = []
    for season in seasons:
        age = 6 if season == current else None
        ip = fetch(INJ_URL.format(season=season), cache / f"nfl_injuries_{season}.csv", age)
        sp = fetch(SNAP_URL.format(season=season), cache / f"nfl_snaps_{season}.csv", age)
        if not ip or not sp:
            continue
        inj = pd.read_csv(ip, low_memory=False)
        snaps = pd.read_csv(sp, low_memory=False)
        snaps["team"] = snaps["team"].map(_team)
        snaps["share"] = snaps[["offense_pct", "defense_pct"]].max(axis=1).fillna(0)
        snaps["key"] = snaps["player"].map(_name)
        snaps = snaps.sort_values("week")
        # Usual share over the previous 4 games (shifted: this week's snaps aren't known yet).
        snaps["usual"] = snaps.groupby(["team", "key"])["share"].transform(
            lambda s: s.shift().rolling(4, min_periods=1).mean())
        last = snaps.groupby(["team", "key"]).agg(pos=("position", "last"))
        inj = inj[inj["report_status"].isin(["Out", "Doubtful"])].copy()
        inj["team"] = inj["team"].map(_team)
        inj["key"] = inj["full_name"].map(_name)
        teams_weeks = snaps[["team", "week"]].drop_duplicates()
        for (team, week), part in inj.groupby(["team", "week"]):
            hist = snaps[(snaps["team"] == team) & (snaps["week"] < week)]
            if hist.empty:
                continue
            usual = hist.sort_values("week").groupby("key")["share"].apply(lambda s: s.tail(4).mean())
            out = part[part["key"].isin(usual.index)]
            regular = [k for k in out["key"] if usual.get(k, 0) >= 0.5]
            qb_keys = [k for k in regular if (last.loc[(team, k), "pos"] if (team, k) in last.index else "") == "QB"]
            rows.append({"season": season, "week": int(week), "team": team, "qb_out": int(bool(qb_keys)),
                         "starters_out": len(regular), "snaps_lost": float(sum(usual[k] for k in regular))})
        for r in teams_weeks.itertuples():  # weeks with nobody out
            rows.append({"season": season, "week": int(r.week), "team": r.team, "qb_out": 0, "starters_out": 0,
                         "snaps_lost": 0.0, "_fill": True})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["_fill"] = df.get("_fill", False)
    df["_fill"] = df["_fill"].fillna(False)
    return df.sort_values("_fill").drop_duplicates(["season", "week", "team"]).drop(columns="_fill")
