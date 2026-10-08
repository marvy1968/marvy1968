"""Pro Football Max (the owner's formula), fixed: EPA -> Poisson scoring drives -> Monte Carlo -> veto.

Fixes to the original:
  1. Scale: scoring drives per team are fitted from real games (about 6.3), not 2.4, so projected totals
     land near real NFL totals (~45) instead of ~16.
  2. Defense sign: a team's lambda rises with the OPPONENT's EPA allowed per play (a bad defense
     allows positive EPA). The original subtracted it, so bad defenses lowered the other team's score.
  3. Home field and ties: a fitted home-field bump; tied simulations count half.
  4. Point-in-time EPA: season-to-date EPA per play blended with last season (worth 4 games), using only
     games before kickoff.
The veto keeps the original rules (weather, injuries, tight spread), with injuries and weather taken from
real data: starting QB out or 1.5+ full-time starters out, outdoor wind 15+ mph, |spread| < 1.5.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

POINTS_PER_DRIVE = 3.5


@dataclass
class PFMParams:
    base: float = 6.3  # scoring drives per team vs an average opponent
    scale: float = 9.0  # extra scoring drives per +1.0 EPA/play (own offense + opponent defense allowed)
    home: float = 0.35  # extra scoring drives at home (~1.2 points)
    prior_games: float = 4.0


def team_epa(st: pd.DataFrame, games: pd.DataFrame, prior_games: float = 4.0) -> pd.DataFrame:
    """Pre-game offense EPA/play, defense EPA/play allowed and plays/game for each team-game."""
    s = st.copy()
    s["plays"] = s["attempts"].fillna(0) + s["carries"].fillna(0) + s["sacks_suffered"].fillna(0)
    s["epa"] = s["passing_epa"].fillna(0) + s["rushing_epa"].fillna(0)
    s = s.merge(games[["game_id", "gameday"]], on="game_id", how="inner").sort_values("gameday")
    allowed = s[["game_id", "team", "epa", "plays"]].rename(columns={"team": "opponent_team", "epa": "a_epa", "plays": "a_plays"})
    s = s.merge(allowed, on=["game_id", "opponent_team"], how="left")
    out = []
    for team, t in s.groupby("team"):
        t = t.sort_values("gameday").copy()
        for col, plays, name in (("epa", "plays", "off"), ("a_epa", "a_plays", "def")):
            cum_e = t.groupby("season")[col].cumsum() - t[col]
            cum_p = t.groupby("season")[plays].cumsum() - t[plays]
            season_tot = t.groupby("season")[[col, plays]].sum()
            prev = season_tot[col] / season_tot[plays]
            prior = t["season"].map(lambda y: prev.get(y - 1, np.nan)).fillna(0.0)
            avg_plays = (t.groupby("season")[plays].cumsum() - t[plays]) / t.groupby("season").cumcount().replace(0, np.nan)
            w = prior_games * avg_plays.fillna(63)
            t[f"{name}_epa"] = (cum_e + prior * w) / (cum_p + w)
        n = t.groupby("season").cumcount()
        t["pace"] = ((t.groupby("season")["plays"].cumsum() - t["plays"]) / n.replace(0, np.nan)).fillna(63) / 63.0
        out.append(t[["game_id", "team", "off_epa", "def_epa", "pace"]])
    return pd.concat(out, ignore_index=True)


def lambdas(home: dict, away: dict, p: PFMParams) -> tuple[float, float]:
    hl = p.base + p.scale * (home["off_epa"] + away["def_epa"]) * home["pace"] + p.home
    al = p.base + p.scale * (away["off_epa"] + home["def_epa"]) * away["pace"]
    return max(hl, 0.5), max(al, 0.5)


def simulate(hl: float, al: float, n: int = 5000, rng=None) -> tuple[np.ndarray, np.ndarray]:
    rng = rng or np.random.default_rng()
    return rng.poisson(hl, n) * POINTS_PER_DRIVE, rng.poisson(al, n) * POINTS_PER_DRIVE


def fit(rows: pd.DataFrame) -> PFMParams:
    """Least squares on scoring drives (points / 3.5) from earlier seasons."""
    y = np.concatenate([rows["home_score"], rows["away_score"]]) / POINTS_PER_DRIVE
    x = np.concatenate([(rows["h_off_epa"] + rows["a_def_epa"]) * rows["h_pace"], (rows["a_off_epa"] + rows["h_def_epa"]) * rows["a_pace"]])
    home = np.concatenate([np.ones(len(rows)), np.zeros(len(rows))])
    ok = ~np.isnan(x) & ~np.isnan(y)
    A = np.column_stack([np.ones(ok.sum()), x[ok], home[ok]])
    b, s, h = np.linalg.lstsq(A, y[ok], rcond=None)[0]
    return PFMParams(base=float(b), scale=float(s), home=float(h))


def veto(row: dict) -> str | None:
    if row.get("windy"):
        return "weather"
    if row.get("injuries"):
        return "injuries"
    if abs(row.get("spread", 3.0)) < 1.5:
        return "tight spread"
    return None
