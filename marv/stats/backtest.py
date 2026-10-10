"""Walk-forward backtest for the stats experts.

For each test season the calendar is cut into chunks (StatsModule.chunk_days). Before each chunk
all three experts are refit on games that finished earlier, then they project the chunk's games.
Nothing from a game's own date or later is ever used to predict it.

Every expert's projection is stored, so expert weights, the Monte Carlo and the pick filters
can be re-tuned afterwards without refitting the models.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..markets import no_vig
from .base import StatsModule, games_to_objects
from .features import build_features, feature_columns

log = logging.getLogger(__name__)


class BudgetExhausted(Exception):
    """Raised after the checkpoint is saved when the time budget runs out; rerun the same command to resume."""


def walk_forward(module: StatsModule, cache: Path, test_seasons: list[int], train_years: int = 6,
                 current: int | None = None, feature_filter=None, ckpt: Path | None = None,
                 budget_s: float | None = None) -> pd.DataFrame:
    """ckpt: pickle path; finished chunks are saved there and skipped on a rerun. budget_s: stop (after saving) once exceeded.
    A resumed run refits the experts at its first unfinished chunk, so its projections can differ slightly from a straight run
    (still walk-forward: every fit uses only earlier games)."""
    import pickle
    import time
    t0 = time.time()
    done = pickle.loads(ckpt.read_bytes()) if ckpt and ckpt.exists() else {}
    seasons = list(range(min(test_seasons) - train_years, max(test_seasons) + 1))
    seasons = [s for s in seasons if s >= module.first_season]
    games, tg = module.load(cache, seasons, current)
    model = build_features(tg, module.stat_columns(tg), module.halflife,
                           extra_cols=getattr(module, "extra_cols", None),
                           adjust=module.adjust_schedule)
    cols = feature_columns(model)
    cols = [c for c in cols if not c.startswith(("mx_", "mxd_"))]  # sums of other columns; formula re-derives them
    if feature_filter:
        cols = [c for c in cols if feature_filter(c)]
    game_objs = games_to_objects(games, module.key)
    games = games.set_index("game_id")

    from .experts import ExpertPanel  # local import keeps sklearn optional for the classic bot

    rows = []
    for season in test_seasons:
        sm = model[(model["season"] == season) & model["points"].notna()]
        if sm.empty:
            continue
        start, end = sm["date"].min(), sm["date"].max()
        c0 = start
        panel, fitted_at = None, start
        while c0 <= end:
            c1 = c0 + pd.Timedelta(days=module.chunk_days)
            chunk = sm[(sm["date"] >= c0) & (sm["date"] < c1)]
            key = c0.isoformat()
            if key in done:
                if done[key] is not None:
                    rows.append(done[key])
                c0 = c1
                continue
            if budget_s is not None and time.time() - t0 > budget_s:
                raise BudgetExhausted(f"{len(done)} chunks saved at {ckpt}; rerun to resume")
            if not chunk.empty:
                train = model[(model["date"] < c0) & (model["season"] >= season - train_years)]
                history = [g for g in game_objs if g.start < c0.to_pydatetime()
                           and (c0.to_pydatetime() - g.start).days < 400]
                if len(train) >= module.experts.min_train_rows:
                    if panel is None or (c0 - fitted_at).days >= module.experts.refit_days:
                        panel = ExpertPanel(module.experts, cols, module.rating_params).fit_models(train)
                        fitted_at = c0
                    panel.fit_ratings(history, c0.to_pydatetime())
                    chunk = module.focus(chunk, panel)
                    if chunk.empty:
                        c0 = c1
                        continue
                    proj = panel.project(chunk)
                    rows.append(pd.concat([chunk[["game_id", "date", "season", "team", "home", "points", "gp"]], proj], axis=1))
                    done[key] = rows[-1]
                    if ckpt:
                        ckpt.parent.mkdir(parents=True, exist_ok=True)
                        ckpt.write_bytes(pickle.dumps(done))
            c0 = c1
        log.info("%s season %s projected", module.key, season)

    team_rows = pd.concat(rows, ignore_index=True)
    return to_games(team_rows, games)


def to_games(team_rows: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Pivot team-level projections into one row per game with both sides and the market."""
    experts = ["formula", "forest", "ratings", "consensus"]
    home = team_rows[team_rows["home"] != 0].drop_duplicates("game_id")
    away = team_rows[team_rows["home"] == 0].drop_duplicates("game_id")
    if (team_rows["home"] == 0.5).any():  # neutral sites: split by the games table's home team
        home = team_rows[team_rows.apply(lambda r: games.at[r.game_id, "home"] == r.team, axis=1)]
        away = team_rows[team_rows.apply(lambda r: games.at[r.game_id, "away"] == r.team, axis=1)]
    h = home.set_index("game_id")[["date", "season", "points", "gp", *experts]].add_prefix("h_")
    a = away.set_index("game_id")[["points", "gp", *experts]].add_prefix("a_")
    out = h.join(a, how="inner").join(games[["home", "away", "spread", "total", "home_ml", "away_ml"]], how="left")
    out = out.rename(columns={"h_date": "date", "h_season": "season"})
    return out.reset_index()


@dataclass
class PickRules:
    weights: tuple[float, float, float] = (1.0, 1.0, 1.0)  # formula, forest, ratings
    ml_min_prob: float = 0.5  # only call a moneyline when the model is at least this confident
    ou_min_edge: float = 0.0  # P(side) - 0.5 required for an over/under call
    require_agreement: bool = False  # all three experts must agree on the side
    min_gp: int = 0  # both teams need this many games this season


def simulate_probs(df: pd.DataFrame, module: StatsModule, weights, n: int = 4000, seed: int = 0) -> pd.DataFrame:
    """Consensus projection -> shared Monte Carlo -> win/over probabilities for each game."""
    rng = np.random.default_rng(seed)
    w = np.array(weights, dtype=float) / sum(weights)
    df = df.copy()
    df["h_proj"] = df[["h_formula", "h_forest", "h_ratings"]].to_numpy() @ w
    df["a_proj"] = df[["a_formula", "a_forest", "a_ratings"]].to_numpy() @ w
    p_home, p_over, med_total = [], [], []
    for h, a, total in zip(df["h_proj"], df["a_proj"], df["total"]):
        sim = module.simulate(max(h, 0.5), max(a, 0.5), n, rng)
        p_home.append(sim.home_win())
        if pd.notna(total):
            t = sim.total
            decided = t != total
            p_over.append(float((t[decided] > total).mean()) if decided.any() else 0.5)
        else:
            p_over.append(np.nan)
        med_total.append(float(np.median(sim.total)))
    df["p_home"], df["p_over"], df["model_total"] = p_home, p_over, med_total
    return df


def score(df: pd.DataFrame, rules: PickRules) -> dict:
    """Accuracy and ROI of moneyline and over/under calls under the given rules."""
    d = df[(df["h_gp"] >= rules.min_gp) & (df["a_gp"] >= rules.min_gp)].copy()
    d = d[d["h_points"] != d["a_points"]]
    home_won = d["h_points"] > d["a_points"]

    # Moneyline: back the side the model favors.
    pick_home = d["p_home"] >= 0.5
    conf = np.where(pick_home, d["p_home"], 1 - d["p_home"])
    ml_mask = conf >= rules.ml_min_prob
    if rules.require_agreement:
        signs = np.sign(d[["h_formula", "h_forest", "h_ratings"]].to_numpy() - d[["a_formula", "a_forest", "a_ratings"]].to_numpy())
        ml_mask &= (np.abs(signs.sum(axis=1)) == 3)
    ml_correct = (pick_home == home_won)[ml_mask]
    price = np.where(pick_home, d["home_ml"], d["away_ml"])[ml_mask]
    payout = np.where(price > 0, price / 100, 100 / np.abs(price))
    priced = ~np.isnan(price)
    ml_units = np.where(ml_correct.values[priced], payout[priced], -1.0).sum() if priced.any() else np.nan
    market_fav_correct = None
    if d["home_ml"].notna().any():
        fav_home = d["home_ml"] < d["away_ml"]
        market_fav_correct = float((fav_home == home_won)[d["home_ml"].notna()].mean())

    # Over/under vs the closing total.
    res = {}
    t = d[d["total"].notna() & d["p_over"].notna()]
    t = t[(t["h_points"] + t["a_points"]) != t["total"]]
    actual = t["h_points"] + t["a_points"]
    over = t["p_over"] >= 0.5
    edge = np.abs(t["p_over"] - 0.5)
    ou_mask = edge >= rules.ou_min_edge
    if rules.require_agreement:
        tots = (t[["h_formula", "h_forest", "h_ratings"]].to_numpy() + t[["a_formula", "a_forest", "a_ratings"]].to_numpy())
        agree = np.all(tots > t["total"].to_numpy()[:, None], axis=1) | np.all(tots < t["total"].to_numpy()[:, None], axis=1)
        ou_mask &= agree
    ou_correct = ((actual > t["total"]) == over)[ou_mask]

    res["games"] = len(d)
    res["ml_picks"] = int(ml_mask.sum())
    res["ml_acc"] = float(ml_correct.mean()) if len(ml_correct) else np.nan
    res["ml_units"] = float(ml_units) if priced.any() else np.nan
    res["ml_roi"] = float(ml_units / priced.sum()) if priced.any() else np.nan
    res["market_fav_acc"] = market_fav_correct
    res["ou_picks"] = int(ou_mask.sum())
    res["ou_acc"] = float(ou_correct.mean()) if len(ou_correct) else np.nan
    res["ou_roi"] = float((ou_correct * (100 / 110) - (~ou_correct)).mean()) if len(ou_correct) else np.nan
    if d["home_ml"].notna().any():
        m = d[d["home_ml"].notna() & d["away_ml"].notna()]
        mk = np.array([no_vig(h, a)[0] for h, a in zip(m["home_ml"], m["away_ml"])])
        hw = (m["h_points"] > m["a_points"]).to_numpy(dtype=float)
        res["brier_model"] = float(np.mean((m["p_home"].to_numpy() - hw) ** 2))
        res["brier_market"] = float(np.mean((mk - hw) ** 2))
    else:
        res["brier_model"] = float(np.mean((d["p_home"].to_numpy() - home_won.to_numpy(dtype=float)) ** 2))
    return res


def accuracy_by_confidence(df: pd.DataFrame, bins=(0.5, 0.6, 0.7, 0.8, 0.9, 1.01)) -> pd.DataFrame:
    d = df[df["h_points"] != df["a_points"]]
    home_won = d["h_points"] > d["a_points"]
    pick_home = d["p_home"] >= 0.5
    conf = np.where(pick_home, d["p_home"], 1 - d["p_home"])
    out = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (conf >= lo) & (conf < hi)
        if m.sum():
            price = np.where(pick_home, d["home_ml"], d["away_ml"])[m]
            correct = (pick_home == home_won)[m].to_numpy()
            payout = np.where(price > 0, price / 100, 100 / np.abs(price))
            ok = ~np.isnan(price)
            roi = float(np.where(correct[ok], payout[ok], -1).mean()) if ok.any() else np.nan
            out.append({"confidence": f"{lo:.0%}-{min(hi, 1):.0%}", "picks": int(m.sum()),
                        "accuracy": float(correct.mean()), "roi": roi})
    return pd.DataFrame(out)
