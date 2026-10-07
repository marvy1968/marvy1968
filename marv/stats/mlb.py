"""MLB stats module: Retrosheet game logs (every batting, pitching and fielding column) plus
starting-pitcher form (runs his team allowed in his recent starts)."""

from pathlib import Path

import numpy as np
import pandas as pd

from ..ratings import RatingParams
from ..sims import baseball
from .base import StatsModule, fetch
from .experts import ExpertConfig

GL_URL = "https://raw.githubusercontent.com/chadwickbureau/retrosheet/master/seasons/{season}/{name}"
TEAMS = {
    "ANA": "Los Angeles Angels", "ARI": "Arizona Diamondbacks", "ATL": "Atlanta Braves", "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox", "CHA": "Chicago White Sox", "CHN": "Chicago Cubs", "CIN": "Cincinnati Reds",
    "CLE": "Cleveland Guardians", "COL": "Colorado Rockies", "DET": "Detroit Tigers", "HOU": "Houston Astros",
    "KCA": "Kansas City Royals", "LAN": "Los Angeles Dodgers", "MIA": "Miami Marlins", "FLO": "Miami Marlins",
    "MIL": "Milwaukee Brewers", "MIN": "Minnesota Twins", "NYA": "New York Yankees", "NYN": "New York Mets",
    "OAK": "Athletics", "ATH": "Athletics", "PHI": "Philadelphia Phillies", "PIT": "Pittsburgh Pirates",
    "SDN": "San Diego Padres", "SEA": "Seattle Mariners", "SFN": "San Francisco Giants", "SLN": "St. Louis Cardinals",
    "TBA": "Tampa Bay Rays", "TEX": "Texas Rangers", "TOR": "Toronto Blue Jays", "WAS": "Washington Nationals",
}
BAT = ["ab", "h", "doubles", "triples", "hr", "rbi", "sh", "sf", "hbp", "bb", "ibb", "so", "sb", "cs", "gidp", "ci", "lob"]
PIT = ["pitchers_used", "ind_er", "team_er", "wp", "balks"]
FLD = ["po", "a", "e", "pb", "dp", "tp"]
STATS = BAT + PIT + FLD
SP_HALFLIFE = 6  # starts


def _cols(prefix: str, start: int) -> dict[int, str]:
    return {start + i: f"{prefix}{name}" for i, name in enumerate(STATS)}


def parse_gamelog(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, header=None, dtype=str, keep_default_na=False)
    names = {0: "date", 1: "game_num", 3: "vis", 6: "home", 9: "vis_runs", 10: "home_runs", 13: "completion",
             101: "vis_sp", 103: "home_sp", **_cols("v_", 21), **_cols("h_", 49)}
    df = raw[list(names)].rename(columns=names)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    for c in df.columns:
        if c not in ("date", "vis", "home", "completion", "vis_sp", "home_sp", "game_num"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["game_id"] = df["home"] + df["date"].dt.strftime("%Y%m%d") + df["game_num"]
    return df


class MLBStats(StatsModule):
    extra_cols = ["sp_ra", "sp_starts", "park"]

    def load(self, cache: Path, seasons: list[int], current: int | None = None):
        frames = []
        for season in seasons:
            for name in (f"GL{season}.TXT", f"gl{season}.txt"):
                path = fetch(GL_URL.format(season=season, name=name), cache / f"mlb_{name.lower()}")
                if path:
                    df = parse_gamelog(path)
                    df["season"] = season
                    frames.append(df)
                    break
        gl = pd.concat(frames, ignore_index=True)
        gl["home_team"] = gl["home"].map(TEAMS).fillna(gl["home"])
        gl["vis_team"] = gl["vis"].map(TEAMS).fillna(gl["vis"])

        games = pd.DataFrame({
            "game_id": gl["game_id"], "date": gl["date"], "season": gl["season"],
            "home": gl["home_team"], "away": gl["vis_team"], "neutral": False,
            "home_points": gl["home_runs"].astype(float), "away_points": gl["vis_runs"].astype(float),
            "spread": np.nan, "total": np.nan, "home_ml": np.nan, "away_ml": np.nan,
            "home_sp": gl["home_sp"], "away_sp": gl["vis_sp"],
        })

        long = []
        for side, other, pre, opp_runs in (("home", "vis", "h_", "vis_runs"), ("vis", "home", "v_", "home_runs")):
            part = pd.DataFrame({
                "game_id": gl["game_id"], "date": gl["date"], "season": gl["season"],
                "team": gl[f"{side}_team"], "opp": gl[f"{other}_team"],
                "home": 1.0 if side == "home" else 0.0, "points": gl[f"{side}_runs"].astype(float),
                "sp": gl[f"{side}_sp"], "runs_allowed": gl[opp_runs].astype(float),
            })
            for stat in STATS:
                part[stat] = gl[f"{pre}{stat}"].astype(float)
            long.append(part)
        tg = pd.concat(long, ignore_index=True)
        pa = tg["ab"] + tg["bb"] + tg["hbp"] + tg["sf"] + tg["sh"]
        tg["obp"] = (tg["h"] + tg["bb"] + tg["hbp"]) / pa
        singles = tg["h"] - tg["doubles"] - tg["triples"] - tg["hr"]
        tg["slg"] = (singles + 2 * tg["doubles"] + 3 * tg["triples"] + 4 * tg["hr"]) / tg["ab"]
        tg["k_rate"] = tg["so"] / pa
        tg["bb_rate"] = tg["bb"] / pa
        tg["baserunners"] = tg["h"] + tg["bb"] + tg["hbp"]
        tg = self.add_starter_form(tg)
        tg = self.add_park_factor(tg, games)
        return games, tg

    def add_park_factor(self, tg: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
        """Runs at each home park vs the same team's road games over the previous 3 seasons, half-regressed."""
        g = games.dropna(subset=["home_points"])
        home = g.groupby(["home", "season"]).apply(lambda d: (d["home_points"] + d["away_points"]).mean(), include_groups=False)
        road = g.groupby(["away", "season"]).apply(lambda d: (d["home_points"] + d["away_points"]).mean(), include_groups=False)
        factors = {}
        for (team, season) in {(t, s) for t, s in zip(games["home"], games["season"])}:
            prev = [(home.get((team, s)), road.get((team, s))) for s in range(season - 3, season)]
            prev = [(h, r) for h, r in prev if h is not None and r is not None and not np.isnan(h) and not np.isnan(r)]
            raw = np.mean([h / r for h, r in prev]) if prev else 1.0
            factors[(team, season)] = 0.5 * raw + 0.5
        host = tg["team"].where(tg["home"] == 1, tg["opp"])
        tg["park"] = [factors.get((h, s), 1.0) for h, s in zip(host, tg["season"])]
        return tg

    def add_starter_form(self, tg: pd.DataFrame) -> pd.DataFrame:
        """Pre-game EWMA of runs allowed in each starter's previous starts (regressed to league average)."""
        tg = tg.sort_values(["date", "game_id"]).reset_index(drop=True)
        by_sp = tg.groupby("sp", sort=False)["runs_allowed"]
        prior = by_sp.transform(lambda s: s.shift(1).ewm(halflife=SP_HALFLIFE, min_periods=1).mean())
        starts = tg.groupby("sp").cumcount()
        league = tg["runs_allowed"].expanding().mean().shift(1).fillna(4.5)
        weight = starts / (starts + 5)  # regress small samples
        tg["sp_ra"] = (weight * prior.fillna(league) + (1 - weight) * league).astype(float)
        tg["sp_starts"] = starts.astype(float)
        return tg

    def prepare_upcoming(self, games, tg, slate, cache, now):
        """Add the current season from the MLB Stats API (finished box scores + probable starters)."""
        from ..data import mlbstats
        season = now.year
        if (games["season"] == season).any():
            return games, tg
        finished, upcoming = mlbstats.season_games(season, cache, now)
        new_games, rows = [], []
        for rec in finished + upcoming:
            gid = f"mlbam-{rec['game_pk']}"
            day = pd.Timestamp(rec["date"])
            done = "home_line" in rec
            new_games.append({"game_id": gid, "date": day, "season": season, "home": rec["home"], "away": rec["away"],
                              "neutral": False, "home_points": rec["home_runs"] if done else np.nan,
                              "away_points": rec["away_runs"] if done else np.nan})
            for side, other in (("home", "away"), ("away", "home")):
                row = {"game_id": gid, "date": day, "season": season, "team": rec[side], "opp": rec[other],
                       "home": 1.0 if side == "home" else 0.0,
                       "points": float(rec[f"{side}_runs"]) if done else np.nan,
                       "runs_allowed": float(rec[f"{other}_runs"]) if done else np.nan}
                if done:
                    line = dict(rec[f"{side}_line"])
                    row["sp"] = line.pop("sp")
                    row.update(line)
                else:
                    row["sp"] = f"mlbam{rec[f'{side}_sp']}" if rec.get(f"{side}_sp") else None
                rows.append(row)
        if not rows:
            return games, tg
        games = pd.concat([games, pd.DataFrame(new_games)], ignore_index=True)
        add = pd.DataFrame(rows)
        pa = add["ab"] + add["bb"] + add["hbp"] + add["sf"] + add["sh"]
        add["obp"] = (add["h"] + add["bb"] + add["hbp"]) / pa
        singles = add["h"] - add["doubles"] - add["triples"] - add["hr"]
        add["slg"] = (singles + 2 * add["doubles"] + 3 * add["triples"] + 4 * add["hr"]) / add["ab"]
        add["k_rate"] = add["so"] / pa
        add["bb_rate"] = add["bb"] / pa
        add["baserunners"] = add["h"] + add["bb"] + add["hbp"]
        tg = self.add_starter_form(pd.concat([tg, add], ignore_index=True))
        tg = self.add_park_factor(tg.drop(columns=["park"], errors="ignore"), games)
        return games, tg

    def stat_columns(self, tg):
        return STATS + ["obp", "slg", "k_rate", "bb_rate", "baserunners"]


def _sim(h, a, n, rng):
    return baseball.simulate_game(h, a, n=n, rng=rng)


MLB = MLBStats(key="mlb", name="MLB", simulate=_sim,
               rating_params=RatingParams(multiplicative=True, home_adv=1.06, shrink=10, half_life_days=90),
               halflife=20, chunk_days=14, first_season=2012, has_lines=False,
               experts=ExpertConfig(rf_min_leaf=60, rf_trees=120))
