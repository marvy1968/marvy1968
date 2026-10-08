"""College football team-game stats built from free play-by-play (sportsdataverse / cfbfastR, CFBD-based).

No API key needed. Each season's play-by-play (~120 MB) is downloaded once, rolled up into one row per
team per game (every offensive efficiency stat the plays support) and only that small table is kept.
Closing spread and total come from the same files; final scores from the cfbfastR schedules.
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..stats.base import fetch

log = logging.getLogger(__name__)
PBP_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/cfbfastR_cfb_pbp/play_by_play_{season}.parquet"
SCHED_URL = "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/schedules/parquet/cfb_schedules_{season}.parquet"
PBP_COLS = ["game_id", "pos_team", "def_pos_team", "play_type", "rush", "pass", "yards_gained", "EPA", "success",
            "down", "distance", "sack", "int", "stuffed_run", "drive_id", "drive_pts", "scoring_opp",
            "drive_start_yards_to_goal", "rz_play", "penalty_flag", "yds_penalty", "wp_before", "spread",
            "over_under", "completion", "pass_attempt"]


def _team_rows(pbp: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, offense) with every stat the play-by-play supports."""
    p = pbp[(pbp["rush"] == 1) | (pbp["pass"] == 1)].copy()
    p["explosive"] = (p["yards_gained"] >= 20).astype(float)
    p["third"] = (p["down"] == 3).astype(float)
    p["third_conv"] = ((p["down"] == 3) & (p["yards_gained"] >= p["distance"])).astype(float)
    p["fumble_lost"] = p["play_type"].astype(str).str.startswith("Fumble Recovery (Opponent)").astype(float)
    p["live"] = p["wp_before"].between(0.05, 0.95).astype(float)  # outside garbage time
    for col in ("yards_gained", "EPA", "success"):
        p[f"r_{col}"] = p[col].where(p["rush"] == 1)
        p[f"p_{col}"] = p[col].where(p["pass"] == 1)
        p[f"l_{col}"] = p[col].where(p["live"] == 1)
    p["stuff"] = p["stuffed_run"].where(p["rush"] == 1)
    p["sack_p"] = p["sack"].where(p["pass"] == 1)
    p["cmp"] = p["completion"].where(p["pass_attempt"] == 1)
    g = p.groupby(["game_id", "pos_team"])
    out = pd.DataFrame({
        "plays": g.size(), "yards": g["yards_gained"].sum(), "ypp": g["yards_gained"].mean(),
        "rush_att": g["rush"].sum(), "rush_yds": g["r_yards_gained"].sum(), "ypc": g["r_yards_gained"].mean(),
        "dropbacks": g["pass"].sum(), "pass_yds": g["p_yards_gained"].sum(), "yards_per_dropback": g["p_yards_gained"].mean(),
        "epa_play": g["EPA"].mean(), "rush_epa": g["r_EPA"].mean(), "pass_epa": g["p_EPA"].mean(),
        "success_rate": g["success"].mean(), "rush_success": g["r_success"].mean(), "pass_success": g["p_success"].mean(),
        "live_ypp": g["l_yards_gained"].mean(), "live_epa": g["l_EPA"].mean(), "live_success": g["l_success"].mean(),
        "explosive_rate": g["explosive"].mean(), "stuff_rate": g["stuff"].mean(), "sack_rate": g["sack_p"].mean(),
        "completion_pct": g["cmp"].mean(), "interceptions": g["int"].sum(), "fumbles_lost": g["fumble_lost"].sum(),
        "third_down_rate": g["third_conv"].sum() / g["third"].sum().replace(0, np.nan),
        "rush_share": g["rush"].mean(),
    })
    out["turnovers"] = out["interceptions"] + out["fumbles_lost"]
    drives = pbp.dropna(subset=["drive_id"]).groupby(["game_id", "pos_team", "drive_id"]).agg(
        pts=("drive_pts", "first"), opp=("scoring_opp", "max"), start=("drive_start_yards_to_goal", "first"),
        rz=("rz_play", "max"))
    dg = drives.groupby(["game_id", "pos_team"])
    out["drives"] = dg.size()
    out["points_per_drive"] = dg["pts"].mean()
    out["start_yards_to_goal"] = dg["start"].mean()  # field position (lower is better)
    out["scoring_opps"] = dg["opp"].sum()
    out["points_per_opp"] = drives[drives["opp"] == 1].groupby(["game_id", "pos_team"])["pts"].mean()
    out["redzone_td_rate"] = drives[drives["rz"] == 1].assign(td=lambda d: (d["pts"] >= 6).astype(float)) \
        .groupby(["game_id", "pos_team"])["td"].mean()
    pen = pbp[pbp["penalty_flag"] == 1].groupby(["game_id", "pos_team"])["yds_penalty"].sum()
    out["penalty_yards_for"] = pen  # penalty yards on plays where this team had the ball (either side)
    return out.reset_index().rename(columns={"pos_team": "team"})


def season_tables(cache: Path, season: int, current: bool = False) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """(games, team_games) for one season, cached as small parquet files."""
    gpath, tpath = cache / f"cfb_games_{season}.parquet", cache / f"cfb_teamgames_{season}.parquet"
    if gpath.exists() and tpath.exists() and not current:
        return pd.read_parquet(gpath), pd.read_parquet(tpath)
    sched_path = fetch(SCHED_URL.format(season=season), cache / f"cfb_sched_{season}.parquet", 6 if current else None)
    raw = cache / f"cfb_pbp_{season}.parquet"
    pbp_path = fetch(PBP_URL.format(season=season), raw, 6 if current else None)
    if not sched_path or not pbp_path:
        return None
    sched = pd.read_parquet(sched_path)
    pbp = pd.read_parquet(pbp_path, columns=PBP_COLS)
    pbp["game_id"] = pbp["game_id"].astype(str)
    lines = pbp.groupby("game_id")[["spread", "over_under"]].first()
    tg = _team_rows(pbp)
    sched["game_id"] = sched["game_id"].astype(str)
    sched = sched[sched["completed"].astype(bool) | current]
    games = pd.DataFrame({
        "game_id": sched["game_id"], "season": season,
        "date": pd.to_datetime(sched["start_date"], utc=True).dt.tz_localize(None).dt.normalize(),
        "home": sched["home_team"], "away": sched["away_team"], "neutral": sched["neutral_site"].fillna(False).astype(bool),
        "home_points": pd.to_numeric(sched["home_points"], errors="coerce"),
        "away_points": pd.to_numeric(sched["away_points"], errors="coerce"),
        "home_fbs": sched["home_division"].eq("fbs"), "away_fbs": sched["away_division"].eq("fbs"),
        "home_elo": pd.to_numeric(sched["home_pregame_elo"], errors="coerce"),
        "away_elo": pd.to_numeric(sched["away_pregame_elo"], errors="coerce"),
    })
    games = games.join(lines.rename(columns={"over_under": "total"}), on="game_id")
    games["home_ml"] = games["away_ml"] = np.nan
    cache.mkdir(parents=True, exist_ok=True)
    games.to_parquet(gpath)
    tg.to_parquet(tpath)
    if not current:
        raw.unlink(missing_ok=True)  # keep only the small tables
    log.info("cfb %s: %d games, %d team-games", season, len(games), len(tg))
    return games, tg


def load(cache: Path, seasons: list[int], current: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Games plus the long team-game table (TEAM_GAME_COLS + every stat), FBS games only."""
    gs, ts = [], []
    for season in seasons:
        got = season_tables(cache, season, season == current)
        if got:
            gs.append(got[0])
            ts.append(got[1])
    games = pd.concat(gs, ignore_index=True)
    stats = pd.concat(ts, ignore_index=True)
    games = games[games["home_fbs"] & games["away_fbs"] & games["home_points"].notna()].reset_index(drop=True)
    long = []
    for side, other in (("home", "away"), ("away", "home")):
        part = games[["game_id", "date", "season", side, other, f"{side}_points", "neutral"]].rename(
            columns={side: "team", other: "opp", f"{side}_points": "points"})
        part["home"] = np.where(part["neutral"], 0.5, 1.0 if side == "home" else 0.0)
        long.append(part.drop(columns="neutral"))
    tg = pd.concat(long, ignore_index=True).merge(stats, on=["game_id", "team"], how="left")
    return games, tg


PLAYER_COLS = ["game_id", "pos_team", "def_pos_team", "rush", "pass", "completion", "pass_attempt", "sack",
               "passer_player_name", "receiver_player_name", "rusher_player_name", "yds_receiving", "yds_rushed",
               "yards_gained"]


def _player_rows(pbp: pd.DataFrame) -> pd.DataFrame:
    """Passing, rushing and receiving lines per player per game (sacks excluded from passing)."""
    passes = pbp[(pbp["pass_attempt"] == 1) & (pbp["sack"] != 1)]
    yards = passes["yds_receiving"].fillna(passes["yards_gained"]).where(passes["completion"] == 1, 0)
    qb = passes.assign(y=yards).groupby(["game_id", "pos_team", "def_pos_team", "passer_player_name"]).agg(
        passing_yards=("y", "sum"), attempts=("pass_attempt", "sum"), completions=("completion", "sum")).reset_index() \
        .rename(columns={"passer_player_name": "player"})
    rec = passes.assign(y=yards).dropna(subset=["receiver_player_name"]).groupby(
        ["game_id", "pos_team", "def_pos_team", "receiver_player_name"]).agg(
        receiving_yards=("y", "sum"), receptions=("completion", "sum"), targets=("pass_attempt", "sum")).reset_index() \
        .rename(columns={"receiver_player_name": "player"})
    runs = pbp[pbp["rush"] == 1]
    rb = runs.assign(y=runs["yds_rushed"].fillna(runs["yards_gained"])).dropna(subset=["rusher_player_name"]).groupby(
        ["game_id", "pos_team", "def_pos_team", "rusher_player_name"]).agg(
        rushing_yards=("y", "sum"), carries=("rush", "sum")).reset_index().rename(columns={"rusher_player_name": "player"})
    out = qb.merge(rec, on=["game_id", "pos_team", "def_pos_team", "player"], how="outer") \
        .merge(rb, on=["game_id", "pos_team", "def_pos_team", "player"], how="outer")
    return out.rename(columns={"pos_team": "team", "def_pos_team": "opponent_team"})


def player_tables(cache: Path, season: int, current: bool = False) -> pd.DataFrame | None:
    """Player-game passing/rushing/receiving table for one season (raw play-by-play is not kept)."""
    path = cache / f"cfb_players_{season}.parquet"
    if path.exists() and not current:
        return pd.read_parquet(path)
    raw = cache / f"cfb_pbp_{season}.parquet"
    pbp_path = fetch(PBP_URL.format(season=season), raw, 6 if current else None)
    if not pbp_path:
        return None
    pbp = pd.read_parquet(pbp_path, columns=PLAYER_COLS)
    pbp["game_id"] = pbp["game_id"].astype(str)
    out = _player_rows(pbp)
    out.to_parquet(path)
    if not current:
        raw.unlink(missing_ok=True)
    return out
