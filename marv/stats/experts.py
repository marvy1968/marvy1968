"""The three "experts" whose consensus drives every stats-based prediction.

  1. Stat formula  - ridge regression over every offense and defense stat (standardized), i.e. a
                     weighted formula where each stat earns a learned weight.
  2. Random forest - learns non-linear interactions between the same stats.
  3. Power rating  - opponent-adjusted points ratings (marv.ratings), independent of box stats.

Each expert projects both teams' points; the consensus is a weighted average, and agreement
between experts is reported so the veto can require it.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..models import Game
from ..ratings import RatingParams, fit_ratings


@dataclass
class ExpertConfig:
    rf_trees: int = 120
    rf_min_leaf: int = 25
    rf_max_features: float = 0.2
    rf_max_depth: int | None = 12
    weights: tuple[float, float, float] = (1.0, 1.0, 1.0)  # formula, forest, ratings
    min_train_rows: int = 400
    refit_days: int = 28  # stat models are refit this often; features and ratings update every chunk


def make_formula():
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                         RidgeCV(alphas=np.logspace(-1, 4, 12)))


def make_forest(cfg: ExpertConfig, seed: int = 0):
    return make_pipeline(SimpleImputer(strategy="median"), RandomForestRegressor(
        n_estimators=cfg.rf_trees, min_samples_leaf=cfg.rf_min_leaf, max_features=cfg.rf_max_features,
        max_depth=cfg.rf_max_depth, n_jobs=-1, random_state=seed))


@dataclass
class ExpertPanel:
    cfg: ExpertConfig
    cols: list[str]
    rating_params: RatingParams
    formula: object = None
    forest: object = None
    ratings: object = None
    importances: dict = field(default_factory=dict)

    def fit(self, train: pd.DataFrame, history: list[Game], as_of) -> "ExpertPanel":
        self.fit_models(train)
        self.fit_ratings(history, as_of)
        return self

    def fit_ratings(self, history: list[Game], as_of) -> "ExpertPanel":
        self.ratings = fit_ratings(history, self.rating_params, as_of=as_of)
        return self

    def fit_models(self, train: pd.DataFrame) -> "ExpertPanel":
        rows = train.dropna(subset=["points"])
        rows = rows[rows["gp_all"] >= 3]  # need a few games before features mean anything
        X, y = rows[self.cols].to_numpy(dtype=float), rows["points"].to_numpy(dtype=float)
        self.formula = make_formula().fit(X, y)
        self.forest = make_forest(self.cfg).fit(X, y)
        rf = self.forest[-1]
        self.importances = dict(sorted(zip(self.cols, rf.feature_importances_), key=lambda kv: -kv[1])[:15])
        return self

    def project(self, rows: pd.DataFrame) -> pd.DataFrame:
        """Points projections per team-game row from each expert plus the weighted consensus."""
        X = rows[self.cols].to_numpy(dtype=float)
        out = pd.DataFrame(index=rows.index)
        out["formula"] = self.formula.predict(X)
        out["forest"] = self.forest.predict(X)
        rat = []
        for team, opp, home in zip(rows["team"], rows["opp"], rows["home"]):
            if home == 0:  # team is the visitor: ask for (home=opp, away=team)
                _, mine = self.ratings.expected(opp, team, neutral=False)
            else:
                mine, _ = self.ratings.expected(team, opp, neutral=home == 0.5)
            rat.append(mine)
        out["ratings"] = rat
        w = np.array(self.cfg.weights, dtype=float)
        out["consensus"] = (out[["formula", "forest", "ratings"]].to_numpy() * w).sum(axis=1) / w.sum()
        return out
