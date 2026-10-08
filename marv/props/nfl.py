"""NFL player props: passing, rushing and receiving yards plus receptions.

Projection (every input is known before kickoff):
  * the player's own form: weighted recent average, season average, last 3 games
  * opportunity: pass attempts, carries, targets, target and air-yards share
  * the opponent: what that defense has allowed to the player's position, relative to the league
  * game script: team points implied by the closing spread and total, home/away, roof and wind
A gradient-boosted model per market turns those into a projected stat, and a Monte Carlo draws the
outcome from the model's own past misses for similar projections, giving P(over the line).
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..data.nflverse import URL as GAMES_URL
from ..stats.base import fetch

log = logging.getLogger(__name__)
PLAYERS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"


@dataclass(frozen=True)
class Market:
    key: str  # Odds API market key
    stat: str  # nflverse column
    positions: tuple
    usage: str  # opportunity column that decides who is a regular
    min_usage: float  # recent average needed to count as a regular (books post props for these players)
    label: str


MARKETS = {
    "pass_yds": Market("player_pass_yds", "passing_yards", ("QB",), "attempts", 20, "Passing yards"),
    "rush_yds": Market("player_rush_yds", "rushing_yards", ("RB", "QB"), "carries", 8, "Rushing yards"),
    "rec_yds": Market("player_reception_yds", "receiving_yards", ("WR", "TE", "RB"), "targets", 4, "Receiving yards"),
    "receptions": Market("player_receptions", "receptions", ("WR", "TE", "RB"), "targets", 4, "Receptions"),
}
FORM_COLS = ["passing_yards", "attempts", "completions", "passing_tds", "passing_epa", "sacks_suffered",
             "rushing_yards", "carries", "rushing_epa", "receiving_yards", "receptions", "targets",
             "target_share", "air_yards_share", "receiving_air_yards", "wopr", "receiving_epa"]


def load_players(cache: Path, seasons: list[int], current: int | None = None) -> pd.DataFrame:
    frames = []
    for season in seasons:
        path = fetch(PLAYERS_URL.format(season=season), cache / f"nfl_players_week_{season}.csv", 6 if season == current else None)
        if path:
            frames.append(pd.read_csv(path, low_memory=False))
    p = pd.concat(frames, ignore_index=True)
    p = p[p["season_type"].isin(["REG", "POST"])]
    keep = ["player_id", "player_display_name", "position", "season", "week", "game_id", "team", "opponent_team", *FORM_COLS]
    return p[[c for c in keep if c in p]].copy()


def load_games(cache: Path) -> pd.DataFrame:
    g = pd.read_csv(fetch(GAMES_URL, cache / "nflverse_games.csv", 3), low_memory=False)
    return g[["game_id", "season", "week", "gameday", "home_team", "away_team", "spread_line", "total_line",
              "home_score", "away_score", "roof", "wind"]]


def build_rows(players: pd.DataFrame, games: pd.DataFrame, halflife: float = 4.0) -> pd.DataFrame:
    """One row per player-game with pre-game features (no information from the game itself)."""
    p = players.merge(games, on=["game_id", "season", "week"], how="inner")
    p["date"] = pd.to_datetime(p["gameday"])
    p["is_home"] = (p["team"] == p["home_team"]).astype(float)
    # nflverse spread_line is home minus away; the team's expected points from the market:
    margin = np.where(p["is_home"] == 1, p["spread_line"], -p["spread_line"])
    p["implied_pts"] = p["total_line"] / 2 + margin / 2
    p["total_line"] = p["total_line"].astype(float)
    p["indoors"] = p["roof"].isin(["dome", "closed"]).astype(float)
    p["wind"] = pd.to_numeric(p["wind"], errors="coerce").fillna(0).where(p["indoors"] == 0, 0)
    p = p.sort_values(["player_id", "date"]).reset_index(drop=True)

    g = p.groupby("player_id", sort=False)
    for c in FORM_COLS:
        if c not in p:
            continue
        prev = g[c].shift()
        p[f"ewm_{c}"] = prev.groupby(p["player_id"]).transform(lambda s: s.ewm(halflife=halflife, min_periods=1).mean())
        p[f"l3_{c}"] = prev.groupby(p["player_id"]).transform(lambda s: s.rolling(3, min_periods=1).mean())
    sg = p.groupby(["player_id", "season"], sort=False)
    for c in ("passing_yards", "rushing_yards", "receiving_yards", "receptions"):
        prev = sg[c].shift()
        p[f"szn_{c}"] = prev.groupby([p["player_id"], p["season"]]).transform(lambda s: s.expanding().mean())
    p["games_before"] = g.cumcount()

    # Opponent defense: yards/receptions allowed to the position, before this game, vs the league.
    pos_allow = p.groupby(["game_id", "opponent_team", "position"], as_index=False)[
        ["passing_yards", "rushing_yards", "receiving_yards", "receptions"]].sum()
    pos_allow = pos_allow.merge(games[["game_id", "gameday", "season"]], on="game_id")
    pos_allow["date"] = pd.to_datetime(pos_allow["gameday"])
    pos_allow = pos_allow.sort_values("date")
    for c in ("passing_yards", "rushing_yards", "receiving_yards", "receptions"):
        prev = pos_allow.groupby(["opponent_team", "position"])[c].shift()
        pos_allow[f"def_{c}"] = prev.groupby([pos_allow["opponent_team"], pos_allow["position"]]).transform(
            lambda s: s.ewm(halflife=6, min_periods=1).mean())
        league = pos_allow.groupby(["season", "position"])[c].transform(lambda s: s.shift().expanding().mean())
        pos_allow[f"def_{c}"] = pos_allow[f"def_{c}"] / league.replace(0, np.nan)
    p = p.merge(pos_allow[["game_id", "opponent_team", "position", *[f"def_{c}" for c in
                ("passing_yards", "rushing_yards", "receiving_yards", "receptions")]]],
                on=["game_id", "opponent_team", "position"], how="left")
    return p


def feature_cols(rows: pd.DataFrame) -> list[str]:
    base = [c for c in rows.columns if c.startswith(("ewm_", "l3_", "szn_", "def_", "ctx_"))]
    return base + [c for c in ("implied_pts", "total_line", "is_home", "indoors", "wind", "games_before") if c in rows]


def eligible(rows: pd.DataFrame, market: Market) -> pd.DataFrame:
    """Regular players at the market's positions (the ones books post lines for)."""
    m = (rows["position"].isin(market.positions) if market.positions else True) \
        & (rows[f"ewm_{market.usage}"] >= market.min_usage) & (rows["games_before"] >= 2)
    return rows[m & rows[market.stat].notna()]


class PropModel:
    """Gradient-boosted projection plus an empirical miss distribution for the Monte Carlo."""

    def __init__(self, market: Market):
        self.market = market

    def fit(self, train: pd.DataFrame, cols: list[str] | None = None) -> "PropModel":
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.model_selection import cross_val_predict
        self.cols = cols or feature_cols(train)
        X, y = train[self.cols].to_numpy(float), train[self.market.stat].to_numpy(float)
        self.model = HistGradientBoostingRegressor(max_iter=250, learning_rate=0.05, min_samples_leaf=40,
                                                   l2_regularization=1.0, random_state=0)
        oof = cross_val_predict(self.model, X, y, cv=4)  # honest misses, not in-sample ones
        self.model.fit(X, y)
        self.edges = np.quantile(oof, np.linspace(0, 1, 11)[1:-1])
        bins = np.digitize(oof, self.edges)
        self.resid = [y[bins == b] - oof[bins == b] for b in range(10)]
        return self

    def median(self, proj: np.ndarray) -> np.ndarray:
        """Median outcome for each projection (where a sportsbook would hang its line)."""
        bins = np.digitize(np.asarray(proj, float), self.edges)
        return np.maximum(np.asarray(proj, float) + np.array([np.median(self.resid[int(b)]) for b in bins]), 0)

    def project(self, rows: pd.DataFrame) -> np.ndarray:
        return self.model.predict(rows[self.cols].to_numpy(float))

    def p_over(self, proj: np.ndarray, line: np.ndarray, n: int = 0, seed: int = 0) -> np.ndarray:
        """Monte Carlo over the model's past misses: outcome = projection + a miss from similar projections
        (floored at 0). Computed exactly over every stored miss instead of sampling, so it's fast and
        has no sampling noise. n and seed are accepted for compatibility."""
        proj, line = np.asarray(proj, float), np.asarray(line, float)
        out = np.full(len(proj), 0.5)
        bins = np.digitize(proj, self.edges)
        for b in np.unique(bins):
            r = np.sort(self.resid[int(b)])
            i = bins == b
            need = line[i] - proj[i]  # over when projection + miss > line (line >= 0, so the floor never matters)
            out[i] = 1 - np.searchsorted(r, need, side="right") / len(r)
        return out


def walk_forward(rows: pd.DataFrame, market: Market, test_seasons: list[int],
                 train_years: int = 6) -> tuple[pd.DataFrame, dict]:
    """Each test season is projected by a model fit only on earlier seasons. Returns (rows, models by season)."""
    data = eligible(rows, market)
    out, models = [], {}
    for season in test_seasons:
        train = data[(data["season"] < season) & (data["season"] >= season - train_years)]
        test = data[data["season"] == season]
        if len(train) < 500 or test.empty:
            continue
        model = models[season] = PropModel(market).fit(train)
        keep = ["player_id", "player_display_name", "position", "team", "opponent_team", "season", "week", "date",
                "game_id", market.stat, f"ewm_{market.stat}", f"szn_{market.stat}", f"l3_{market.stat}"]
        t = test[[c for c in keep if c in test]].copy()
        t["proj"] = model.project(test)
        t["market"] = market.key
        out.append(t)
    return (pd.concat(out, ignore_index=True) if out else pd.DataFrame()), models


def half_line(x: np.ndarray) -> np.ndarray:
    """Sportsbook-style line: nearest .5, so there are no pushes."""
    return np.floor(x) + 0.5


def grade(df: pd.DataFrame, stat: str, line_col: str, p_col: str, min_edge: float) -> dict:
    """Bet the side with P >= 0.5 + min_edge; win rate and ROI at -110 (pushes impossible on .5 lines)."""
    p = df[p_col].to_numpy(float)
    over = p >= 0.5
    conf = np.where(over, p, 1 - p)
    bet = conf >= 0.5 + min_edge
    hit = np.where(over, df[stat] > df[line_col], df[stat] < df[line_col])[bet]
    n = int(bet.sum())
    roi = (hit.sum() * 100 / 110 - (n - hit.sum())) / n if n else np.nan
    return {"n": n, "win_rate": float(hit.mean()) if n else np.nan, "roi": roi}
