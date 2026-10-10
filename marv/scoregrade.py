"""Scoring grade (college football FBS): paper-tracked Under lean, never a pick.

Research: ANALYSIS.md "Power grades" (research/grade_backtest/). Every team-game box stat (own = offense, what opponents
did to the team = defense) is averaged to date with a prior-season carryover, z-scored per season, and a ridge model fitted on
earlier seasons turns them into an offense power and a defense power. Teams get 0-100 percentile grades within the season.
Scoring grade = mean(both offense grades, 100 - both defense grades): high = two good offenses facing two weak defenses.
Games at or above 59.1 (the top 20% in 2018-21) went Under 56.5% in training, 55.7% on the held-out 2022-25 games (436), 55.9% in 2024-25
against 52.4% break-even at -110. It is the same family as OVER-FADE (the market overprices scoring), so it is a lean until 100+ live
graded bets stay above break-even.
"""

import logging

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

log = logging.getLogger(__name__)
THRESHOLD = 59.1
PRIOR_GAMES = 4.0  # last season's average counts as this many games
TRAIN_YEARS = 6
MIN_GAMES = 3
MIN_TRAIN_ROWS = 500
ALPHA = 300.0
SKIP = {"game_id", "team", "opp", "date", "season", "points", "home", "plays", "drives", "rush_att", "dropbacks", "yards",
        "rush_yds", "pass_yds", "scoring_opps", "penalty_yards_for"}


def stat_columns(tg: pd.DataFrame) -> list[str]:
    return [c for c in tg.columns if c not in SKIP and pd.api.types.is_numeric_dtype(tg[c])
            and not c.startswith(("opp_", "my_", "mx_", "mxd_"))]


def _pregame(L: pd.DataFrame, off: list[str], dfn: list[str]) -> pd.DataFrame:
    """Season-to-date means (before each game) with last season's mean as a prior; games with no stats yet (upcoming) get the same."""
    cols = off + dfn
    last = {}
    for (team, season), d in L.groupby(["team", "season"], sort=False):
        last[(team, season)] = d.loc[d["_done"], cols].mean() if d["_done"].any() else None
    out = []
    for (team, season), d in L.groupby(["team", "season"], sort=False):
        d = d.sort_values("date")
        cum = d[cols].fillna(0).mul(d["_done"].astype(float), axis=0).cumsum().shift(1).fillna(0.0)
        n = d["_done"].astype(float).cumsum().shift(1).fillna(0.0).to_numpy()
        prev = last.get((team, season - 1))
        if prev is not None:
            est = (cum.to_numpy() + PRIOR_GAMES * prev.to_numpy()[None, :]) / (n[:, None] + PRIOR_GAMES)
        else:
            est = cum.to_numpy() / np.maximum(n[:, None], 1.0)
            est[n == 0] = np.nan
        e = pd.DataFrame(est, index=d.index, columns=["m_" + c for c in cols])
        e["n"] = n
        out.append(e)
    return pd.concat(out)


def game_grades(games: pd.DataFrame, tg: pd.DataFrame, season: int | None = None) -> dict:
    """{game_id: scoring grade} for every game of `season` (default: latest) that has both teams' pregame state.
    `tg` is the module's long team-game table (completed games carry stats; upcoming rows may have none)."""
    stats = stat_columns(tg)
    if not stats or "ypp" not in tg.columns:
        return {}
    L = tg[["game_id", "team", "opp", "date", "season", "points", "home"] + stats].copy()
    L["date"] = pd.to_datetime(L["date"])
    L["_done"] = L["ypp"].notna() & L["points"].notna()
    season = int(season or L["season"].max())
    L = L[(L["season"] >= season - TRAIN_YEARS - 1) & (L["season"] <= season)]
    opp = L[["game_id", "team"] + stats].rename(columns={"team": "opp", **{c: "d_" + c for c in stats}})
    L = L.merge(opp, on=["game_id", "opp"], how="left").sort_values(["team", "date"]).reset_index(drop=True)
    off, dfn = stats, ["d_" + c for c in stats]
    F = pd.concat([L, _pregame(L, off, dfn)], axis=1)
    mc = ["m_" + c for c in off + dfn]
    for c in mc:
        g = F.groupby("season")[c]
        F["z_" + c] = ((F[c] - g.transform("mean")) / g.transform("std")).fillna(0.0)
    zo, zd = ["z_m_" + c for c in off], ["z_m_" + c for c in dfn]
    opp_z = F[["game_id", "team"] + zo + zd].rename(columns={"team": "opp", **{c: "opp_" + c for c in zo + zd}})
    T = F.merge(opp_z, on=["game_id", "opp"], how="inner")
    X = zo + ["opp_" + c for c in zd] + ["home"]
    train = T[(T["season"] < season) & (T["season"] >= season - TRAIN_YEARS) & T["_done"] & (T["n"] >= 1)]
    if len(train) < MIN_TRAIN_ROWS:
        return {}
    model = Ridge(alpha=ALPHA).fit(train[X], train["points"])
    cur = F[F["season"] == season].copy()
    off_x = pd.DataFrame(0.0, index=cur.index, columns=X)
    off_x[zo] = cur[zo].to_numpy()
    off_x["home"] = 0.5
    def_x = pd.DataFrame(0.0, index=cur.index, columns=X)
    def_x[["opp_" + c for c in zd]] = cur[zd].to_numpy()
    def_x["home"] = 0.5
    cur["off"] = model.predict(off_x)
    cur["deff"] = -model.predict(def_x)
    cur = cur[cur["n"] >= MIN_GAMES]
    cur["og"] = cur["off"].rank(pct=True) * 100
    cur["dg"] = cur["deff"].rank(pct=True) * 100
    side = cur.set_index(["game_id", "team"])[["og", "dg"]]
    out = {}
    for gid, d in cur.groupby("game_id"):
        if len(d) == 2:
            a, b = d.iloc[0], d.iloc[1]
            out[gid] = float((a.og + b.og + (100 - a.dg) + (100 - b.dg)) / 4)
    return out
