"""Quarter-by-quarter model: at the end of each period, project the final margin and total from the
pregame line plus the game's own stats so far (yards per play, success rate, turnovers... or shooting,
rebounds, turnovers, pace), then price the moneyline and over/under with a Monte Carlo of the model's
own past misses at that point of the game.
"""

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..stats.base import fetch
from . import plays as PL

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Spec:
    family: str  # football / basketball
    checkpoints: tuple
    first_season: int


SPECS = {
    "nfl": Spec("football", (1, 2, 3), 2014),
    "cfb": Spec("football", (1, 2, 3), 2014),
    "wnba": Spec("basketball", (1, 2, 3), 2014),
    "ncaab": Spec("basketball", (1,), 2016),  # men's college basketball plays two halves
    "ncaaw": Spec("basketball", (1, 2, 3), 2016),
}
NFL_PBP = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"
BB_PBP = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_{league}_pbp/play_by_play_{season}.parquet"
BB_LEAGUE = {"wnba": "wnba", "ncaab": "mens_college_basketball", "ncaaw": "womens_college_basketball"}
NFL_PBP_COLS = ["game_id", "qtr", "posteam", "home_team", "away_team", "play_type", "down", "ydstogo", "yards_gained",
                "sack", "interception", "fumble_lost", "total_home_score", "total_away_score"]
BB_PBP_COLS = ["game_id", "period_number", "team_id", "home_team_id", "type_text", "text", "shooting_play",
               "scoring_play", "points_attempted", "home_score", "away_score"]
CFB_PBP_COLS = ["game_id", "period", "pos_team", "home", "rush", "pass", "down", "distance", "yards_gained", "sack",
                "int", "play_type", "pos_team_score", "def_pos_team_score"]


# ---------- historical data ----------

def _states_cached(cache: Path, sport: str, season: int, current: bool) -> pd.DataFrame | None:
    path = cache / f"ingame_{sport}_{season}.parquet"
    if path.exists() and not current:
        return pd.read_parquet(path)
    spec = SPECS[sport]
    if sport == "nfl":
        f = fetch(NFL_PBP.format(season=season), cache / f"nfl_pbp_{season}.parquet", 6 if current else None)
        plays = PL.nflverse(pd.read_parquet(f, columns=NFL_PBP_COLS)) if f else None
    elif sport == "cfb":
        from ..data.cfb_pbp import PBP_URL
        f = fetch(PBP_URL.format(season=season), cache / f"cfb_pbp_{season}.parquet", 6 if current else None)
        plays = PL.cfbfastr(pd.read_parquet(f, columns=CFB_PBP_COLS)) if f else None
    else:
        f = fetch(BB_PBP.format(league=BB_LEAGUE[sport], season=season), cache / f"{sport}_pbp_{season}.parquet",
                  2 if current else None)
        plays = PL.espn_basketball_file(pd.read_parquet(f, columns=BB_PBP_COLS)) if f else None
    if plays is None or plays.empty:
        return None
    st = (PL.football_states if spec.family == "football" else PL.basketball_states)(plays, spec.checkpoints)
    st["season"] = season
    st.to_parquet(path)
    if not current and f and spec.family == "football":
        Path(f).unlink(missing_ok=True)  # big files: keep only the per-quarter table
    return st


def games_table(cache: Path, sport: str, seasons: list[int]) -> pd.DataFrame:
    """game_id, season, final scores and pregame inputs (spread as the home line, total)."""
    if sport == "nfl":
        from ..data.nflverse import URL
        g = pd.read_csv(fetch(URL, cache / "nflverse_games.csv", 3), low_memory=False)
        g = g[g["season"].isin(seasons)]
        return pd.DataFrame({"game_id": g["game_id"].astype(str), "season": g["season"],
                             "final_h": g["home_score"], "final_a": g["away_score"],
                             "pre_spread": -g["spread_line"], "pre_total": g["total_line"]})
    if sport == "cfb":
        from ..data import cfb_pbp
        games, _ = cfb_pbp.load(cache, seasons, None)
        return pd.DataFrame({"game_id": games["game_id"].astype(str), "season": games["season"],
                             "final_h": games["home_points"], "final_a": games["away_points"],
                             "pre_spread": games["spread"], "pre_total": games["total"]})
    from ..stats import basketball as BS
    module = {"wnba": BS.WNBA, "ncaab": BS.NCAAB, "ncaaw": BS.NCAAW}[sport]
    games, tg = module.load(cache, seasons, None)
    return basketball_pregame(games, tg)


def basketball_pregame(games: pd.DataFrame, tg: pd.DataFrame) -> pd.DataFrame:
    """Pregame strength without a betting line: each team's recent scoring for and against."""
    t = tg[["game_id", "date", "team", "points"]].sort_values("date").copy()
    opp = t.rename(columns={"team": "opp", "points": "allowed"})[["game_id", "opp", "allowed"]]
    t = t.merge(tg[["game_id", "team", "opp"]], on=["game_id", "team"]).merge(opp, on=["game_id", "opp"])
    t = t.sort_values("date")
    for c in ("points", "allowed"):
        prev = t.groupby("team")[c].shift()
        t[f"e_{c}"] = prev.groupby(t["team"]).transform(lambda s: s.ewm(halflife=8, min_periods=1).mean())
    h = games.merge(t[["game_id", "team", "e_points", "e_allowed"]].rename(columns={"team": "home"}), on=["game_id", "home"])
    h = h.merge(t[["game_id", "team", "e_points", "e_allowed"]].rename(columns={"team": "away"}), on=["game_id", "away"],
                suffixes=("_h", "_a"))
    exp_h = (h["e_points_h"] + h["e_allowed_a"]) / 2
    exp_a = (h["e_points_a"] + h["e_allowed_h"]) / 2
    return pd.DataFrame({"game_id": h["game_id"].astype(str), "season": h["season"], "final_h": h["home_points"],
                         "final_a": h["away_points"], "pre_spread": -(exp_h - exp_a), "pre_total": exp_h + exp_a})


def dataset(cache: Path, sport: str, seasons: list[int], current: int | None = None) -> pd.DataFrame:
    states = [s for season in seasons if (s := _states_cached(cache, sport, season, season == current)) is not None]
    st = pd.concat(states, ignore_index=True)
    g = games_table(cache, sport, seasons).drop(columns="season")
    d = st.merge(g, on="game_id", how="inner").dropna(subset=["final_h", "final_a"])
    return add_score_cols(d)


def add_score_cols(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["score_margin"] = d["h_score"] - d["a_score"]
    d["score_total"] = d["h_score"] + d["a_score"]
    return d


# ---------- model ----------

SCORE_ONLY = ["pre_spread", "pre_total", "h_score", "a_score", "score_margin", "score_total"]


def feature_cols(d: pd.DataFrame, stats: bool = True) -> list[str]:
    if not stats:
        return SCORE_ONLY
    extra = [c for c in d.columns if c.startswith(("h_", "a_", "d_")) and c not in ("h_score", "a_score")]
    return SCORE_ONLY + extra + (["pace"] if "pace" in d else [])


class _Head:
    """One target (final margin or total) at one checkpoint: boosted trees + binned past misses."""

    def fit(self, X: np.ndarray, y: np.ndarray):
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.model_selection import cross_val_predict
        self.m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, min_samples_leaf=30,
                                               l2_regularization=1.0, random_state=0)
        oof = cross_val_predict(self.m, X, y, cv=4)
        self.m.fit(X, y)
        self.edges = np.quantile(oof, np.linspace(0, 1, 9)[1:-1])
        b = np.digitize(oof, self.edges)
        self.resid = [np.sort(y[b == i] - oof[b == i]) for i in range(8)]
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.m.predict(X)

    def p_above(self, mu: np.ndarray, line: np.ndarray) -> np.ndarray:
        """P(outcome > line) where outcome = projection + a past miss from similar projections."""
        out = np.full(len(mu), 0.5)
        b = np.digitize(mu, self.edges)
        for i in np.unique(b):
            r = self.resid[int(i)]
            k = b == i
            above = 1 - np.searchsorted(r, line[k] - mu[k], side="right") / len(r)
            at = (np.searchsorted(r, line[k] - mu[k], side="right") - np.searchsorted(r, line[k] - mu[k], side="left")) / len(r)
            out[k] = above / np.maximum(1 - at, 1e-9)  # pushes (exact ties) don't count
        return out


class InGameModel:
    def __init__(self, sport: str, stats: bool = True):
        self.sport, self.stats = sport, stats

    def fit(self, d: pd.DataFrame) -> "InGameModel":
        self.cols = feature_cols(d, self.stats)
        self.heads = {}
        for k in SPECS[self.sport].checkpoints:
            x = d[d["checkpoint"] == k]
            if len(x) < 200:
                continue
            X = x[self.cols].to_numpy(float)
            self.heads[k] = (_Head().fit(X, (x["final_h"] - x["final_a"]).to_numpy(float)),
                             _Head().fit(X, (x["final_h"] + x["final_a"]).to_numpy(float)))
        return self

    def predict(self, d: pd.DataFrame) -> pd.DataFrame:
        """Adds exp_margin, exp_total, p_home (ties in football settle as a coin flip in OT)."""
        out = d.copy()
        for c in ("exp_margin", "exp_total", "p_home"):
            out[c] = np.nan
        for k, (hm, ht) in self.heads.items():
            i = (out["checkpoint"] == k).to_numpy()
            if not i.any():
                continue
            X = out.loc[i, self.cols].to_numpy(float)
            mu_m, mu_t = hm.predict(X), ht.predict(X)
            out.loc[i, "exp_margin"], out.loc[i, "exp_total"] = mu_m, mu_t
            out.loc[i, "p_home"] = hm.p_above(mu_m, np.zeros(i.sum()))
        return out

    def p_over(self, d: pd.DataFrame, line: np.ndarray) -> np.ndarray:
        out = np.full(len(d), np.nan)
        for k, (_, ht) in self.heads.items():
            i = (d["checkpoint"] == k).to_numpy()
            if i.any():
                X = d.loc[i, self.cols].to_numpy(float)
                out[i] = ht.p_above(ht.predict(X), np.asarray(line, float)[i])
        return out

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps(self))

    @staticmethod
    def load(path: Path) -> "InGameModel | None":
        try:
            return pickle.loads(path.read_bytes())
        except (FileNotFoundError, pickle.UnpicklingError, EOFError, AttributeError):
            return None


def model_path(state_dir: Path, sport: str) -> Path:
    return Path(state_dir) / "models" / f"ingame_{sport}.pkl"


def train(state_dir: Path, sport: str, current: int, years: int = 8) -> InGameModel:
    d = dataset(Path(state_dir) / "cache", sport, list(range(max(current - years, SPECS[sport].first_season), current + 1)), current)
    model = InGameModel(sport).fit(d)
    model.save(model_path(state_dir, sport))
    log.info("in-game %s model trained on %d game-checkpoints", sport, len(d))
    return model


# ---------- backtest ----------

def baseline_live(d: pd.DataFrame, sport: str) -> pd.DataFrame:
    """The current live method (pregame projection + score + pace blend) for comparison."""
    from ..bridge import live_state
    from ..live_monitor import STRUCTURE
    periods, minutes, _, _ = STRUCTURE[sport]
    rows = []
    for r in d.itertuples():
        rec = {"model_margin": -r.pre_spread, "model_total": r.pre_total,
               "home_win": 0.5}
        left = (periods - r.checkpoint) * minutes
        st = live_state(rec, sport, r.h_score, r.a_score, left)
        rows.append((st["exp_margin"], st["exp_total"], st["p_home"]))
    return pd.DataFrame(rows, columns=["b_margin", "b_total", "b_home"], index=d.index)


def backtest(cache: Path, sport: str, seasons: list[int], train_years: int = 6) -> pd.DataFrame:
    first = max(min(seasons) - train_years, SPECS[sport].first_season)
    d = dataset(cache, sport, list(range(first, max(seasons) + 1)))
    d = d.dropna(subset=["pre_spread", "pre_total"])
    out = []
    for season in seasons:
        tr = d[(d["season"] < season) & (d["season"] >= season - train_years)]
        te = d[d["season"] == season]
        if len(tr) < 600 or te.empty:
            continue
        model = InGameModel(sport).fit(tr)
        full = model.predict(te)
        score = InGameModel(sport, stats=False).fit(tr).predict(te)
        full["s_home"], full["s_total"], full["s_margin"] = score["p_home"], score["exp_total"], score["exp_margin"]
        full = pd.concat([full, baseline_live(te, sport)], axis=1)
        full["p_over_pre"] = model.p_over(te, te["pre_total"].to_numpy())
        out.append(full)
    return pd.concat(out, ignore_index=True)


def report(df: pd.DataFrame, sport: str) -> str:
    df = df.copy()
    df["margin"] = df["final_h"] - df["final_a"]
    df["total"] = df["final_h"] + df["final_a"]
    df = df[df["margin"] != 0]
    win = (df["margin"] > 0).astype(float)
    label = {1: "End of Q1", 2: "Halftime", 3: "End of Q3"} if sport != "ncaab" else {1: "Halftime"}
    lines = [f"{sport.upper()} in-game model, seasons {sorted(df.season.unique())} (walk-forward)",
             "checkpoint | winner: stats model / score-only / current live method | Brier (lower better) | "
             "final-total miss: stats / score-only / current"]
    for k, g in df.groupby("checkpoint"):
        w = win[g.index]
        acc = lambda p: ((g[p] >= .5).astype(float) == w).mean()
        brier = lambda p: ((g[p] - w) ** 2).mean()
        mae = lambda c: (g[c] - g["total"]).abs().mean()
        lines.append(f"{label.get(k, k)} ({len(g)} games) | {acc('p_home'):.1%} / {acc('s_home'):.1%} / {acc('b_home'):.1%} | "
                     f"{brier('p_home'):.3f} / {brier('s_home'):.3f} / {brier('b_home'):.3f} | "
                     f"{mae('exp_total'):.1f} / {mae('s_total'):.1f} / {mae('b_total'):.1f}")
        conf = np.maximum(g["p_home"], 1 - g["p_home"])
        for lo in (0.8, 0.9):
            m = conf >= lo
            if m.any():
                lines.append(f"    moneyline when the model is >= {lo:.0%}: {acc_mask(g, w, m):.1%} of {int(m.sum())} games")
        po = g["p_over_pre"]
        side = (po >= .5)
        decided = g["total"] != g["pre_total"]
        right = np.where(side, g["total"] > g["pre_total"], g["total"] < g["pre_total"])[decided.to_numpy()]
        lines.append(f"    over/under vs the PREGAME total: {right.mean():.1%} (live lines move with the score, so real "
                     f"live O/U bets would be harder)")
    return "\n".join(lines)


def acc_mask(g: pd.DataFrame, w: pd.Series, m: pd.Series) -> float:
    return float(((g.loc[m, "p_home"] >= .5).astype(float) == w[m]).mean())
