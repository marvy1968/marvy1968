"""NFL offensive-line health from snap counts and the weekly injury report (known before kickoff).

Per team and week: the five offensive linemen (T, G, C, OL) with the most offensive snaps over the team's previous 4 games are its
line. Each carries his usual share of offensive snaps (1.0 = plays every snap). The game-week report then costs the line:
Out / Doubtful = his usual share, Questionable = QUESTIONABLE_COST x his usual share.
  oline_loss    snap-weighted starters lost (0 = full line, 1 = one full-time lineman out, up to 5)
  oline_index   1 - 0.08 x oline_loss, clipped to 0.6-1.0 (the multiplier used in the pasted NFL formula, but data-driven)
Research: ANALYSIS.md "NFL O-line index". Uses only earlier games and that week's report.
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..stats.base import fetch
from .nfl_availability import INJ_URL, SNAP_URL, _name, _team

OL_POS = {"T", "G", "C", "OL", "OT", "OG"}
QUESTIONABLE_COST = 0.25
INDEX_SLOPE = 0.08


def oline_health(cache: Path, seasons: list[int], current: int | None = None) -> pd.DataFrame:
    """season, week, team, oline_loss, oline_index, oline_out (names) for every team-week with an injury report."""
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
        snaps = snaps[snaps["position"].isin(OL_POS) & (snaps["game_type"] == "REG")].copy()
        snaps["key"] = snaps["player"].map(_name)
        snaps["share"] = snaps["offense_pct"].fillna(0)
        inj = inj[inj["report_status"].isin(["Out", "Doubtful", "Questionable"]) & inj["position"].isin(OL_POS)].copy()
        inj["team"] = inj["team"].map(_team)
        inj["key"] = inj["full_name"].map(_name)
        weeks = sorted(set(snaps["week"]) | set(inj["week"]))
        for team in sorted(snaps["team"].unique()):
            mine = snaps[snaps["team"] == team]
            for week in weeks:
                hist = mine[(mine["week"] < week) & (mine["week"] >= week - 4)]
                if hist.empty:
                    continue
                usual = hist.groupby("key")["share"].sum() / hist["week"].nunique()
                line = usual.sort_values(ascending=False).head(5)
                report = inj[(inj["team"] == team) & (inj["week"] == week)].drop_duplicates("key").set_index("key")["report_status"]
                loss, out = 0.0, []
                for key, share in line.items():
                    status = report.get(key)
                    if status in ("Out", "Doubtful"):
                        loss += share
                        out.append(key)
                    elif status == "Questionable":
                        loss += QUESTIONABLE_COST * share
                rows.append({"season": season, "week": week, "team": team, "oline_loss": loss, "oline_out": ",".join(out)})
    df = pd.DataFrame(rows)
    if not df.empty:
        df["oline_index"] = (1.0 - INDEX_SLOPE * df["oline_loss"]).clip(0.6, 1.0)
    return df
