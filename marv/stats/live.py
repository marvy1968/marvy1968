"""Live predictions from the stats experts: load box scores, add the upcoming slate, fit the
three experts on everything finished, project each upcoming game, then hand the consensus to
the shared engine (Monte Carlo + market + veto)."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..data.teams import similarity
from ..models import Game, Pick, Prediction
from .base import StatsModule, games_to_objects
from .experts import ExpertPanel
from .features import build_features, feature_columns

log = logging.getLogger(__name__)
TRAIN_YEARS = 6


@dataclass
class StatsRules:
    """Pick filters fitted by walk-forward backtests (see ANALYSIS.md)."""
    ml_min_prob: float = 0.0  # model confidence needed for a moneyline play
    ml_agree: bool = True  # all three experts must pick the same winner
    ou_min_edge: float = 0.05  # P(side) - 50% needed for an over/under play
    ou_agree: bool = True  # all three experts must sit on the same side of the total
    ou_enabled: bool = True  # False when backtests showed no over/under edge for this sport
    min_games: int = 3  # games of stats each team needs this season
    spread_enabled: bool = False  # tuned for moneyline and over/under only


@dataclass
class StatsProjection:
    home: float
    away: float
    experts: dict[str, tuple[float, float]]
    rules: StatsRules
    min_gp: int
    notes: list[str] = field(default_factory=list)
    game_vetoes: list[str] = field(default_factory=list)  # e.g. late key injuries

    def vetoes_for(self, pick: Pick, pred: Prediction) -> list[str]:
        out = list(self.game_vetoes)
        if self.min_gp < self.rules.min_games:
            out.append(f"only {self.min_gp} games of stats")
        margins = [h - a for h, a in self.experts.values()]
        totals = [h + a for h, a in self.experts.values()]
        if pick.market == "ml":
            home_side = pick.side == pred.game.home
            if self.rules.ml_agree and not all((m > 0) == home_side for m in margins):
                out.append("experts split on the winner")
            if pick.prob < self.rules.ml_min_prob:
                out.append(f"confidence {pick.prob:.0%} below {self.rules.ml_min_prob:.0%}")
        elif pick.market == "total":
            over = pick.side == "Over"
            if self.rules.ou_agree and not all((t > pick.line) == over for t in totals):
                out.append("experts split on the total")
            if not self.rules.ou_enabled:
                out.append("O/U has no proven edge in backtests")
            elif pick.prob - 0.5 < self.rules.ou_min_edge:
                out.append(f"O/U edge {pick.prob - 0.5:+.1%} below {self.rules.ou_min_edge:.0%}")
        elif pick.market == "spread" and not self.rules.spread_enabled:
            out.append("stats mode plays moneylines and totals only")
        elif pick.market == "spread":
            sign = 1 if pick.side == pred.game.home else -1
            if not all(sign * m + pick.line > 0 for m in margins):
                out.append("experts split on the cover")
        return out


def _match(slate_game: Game, games: pd.DataFrame) -> str | None:
    """Find the stats-source game id for a schedule game (same day, best team-name match)."""
    day = pd.Timestamp(slate_game.start).tz_localize(None).normalize() if pd.Timestamp(slate_game.start).tzinfo \
        else pd.Timestamp(slate_game.start).normalize()
    cand = games[(games["date"] - day).abs() <= pd.Timedelta(days=1)]
    best, best_score = None, 0.0
    for r in cand.itertuples():
        sc = min(similarity(slate_game.home, r.home), similarity(slate_game.away, r.away))
        if sc > best_score:
            best, best_score = r.game_id, sc
    return best if best_score >= 0.75 else None


def add_upcoming(games: pd.DataFrame, tg: pd.DataFrame, slate: list[Game]) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Make sure every slate game exists in the stats tables (stats NaN) and map slate id -> stats id."""
    id_map, new_games, new_rows = {}, [], []
    for g in slate:
        gid = _match(g, games)
        if gid is None:
            gid = f"live-{g.id}"
            day = pd.Timestamp(g.start).tz_convert(None).normalize() if pd.Timestamp(g.start).tzinfo else pd.Timestamp(g.start)
            season = int(games["season"].max())
            home, away = _canon(g.home, games["home"]), _canon(g.away, games["away"])
            new_games.append({"game_id": gid, "date": day, "season": season, "home": home, "away": away,
                              "neutral": g.neutral, "home_points": np.nan, "away_points": np.nan})
            for team, opp, h in ((home, away, 0.5 if g.neutral else 1.0), (away, home, 0.5 if g.neutral else 0.0)):
                new_rows.append({"game_id": gid, "date": day, "season": season, "team": team, "opp": opp,
                                 "home": h, "points": np.nan})
        id_map[g.id] = gid
    if new_games:
        games = pd.concat([games, pd.DataFrame(new_games)], ignore_index=True)
        tg = pd.concat([tg, pd.DataFrame(new_rows)], ignore_index=True)
    return games, tg, id_map


def _canon(name: str, known: pd.Series) -> str:
    names = known.dropna().unique()
    best = max(names, key=lambda n: similarity(name, n), default=name)
    return best if similarity(name, best) >= 0.75 else name


def project_slate(module: StatsModule, slate: list[Game], cache: Path, now: datetime,
                  rules: StatsRules) -> dict[str, StatsProjection]:
    season = module.season_of(pd.Timestamp(now))
    seasons = [s for s in range(season - TRAIN_YEARS, season + 1) if s >= module.first_season]
    games, tg = module.load(cache, seasons, current=season)
    games["date"] = pd.to_datetime(games["date"])
    tg["date"] = pd.to_datetime(tg["date"])
    if hasattr(module, "prepare_upcoming"):
        games, tg = module.prepare_upcoming(games, tg, slate, cache, now)
    games, tg, id_map = add_upcoming(games, tg, slate)

    model = build_features(tg, module.stat_columns(tg), module.halflife, extra_cols=getattr(module, "extra_cols", None))
    cols = [c for c in feature_columns(model) if not c.startswith(("mx_", "mxd_"))]
    today = pd.Timestamp(now).tz_localize(None) if pd.Timestamp(now).tzinfo else pd.Timestamp(now)
    train = model[(model["date"] < today.normalize()) & model["points"].notna()]
    history = [g for g in games_to_objects(games, module.key) if g.start < today.to_pydatetime()]
    panel = ExpertPanel(module.experts, cols, module.rating_params).fit(train, history, today.to_pydatetime())

    targets = model[model["game_id"].isin(set(id_map.values()))]
    targets = module.focus(targets, panel)
    if targets.empty:
        return {}
    proj = pd.concat([targets[["game_id", "team", "home", "gp"]], panel.project(targets)], axis=1)

    from ..data.injuries import injury_vetoes
    hurt = injury_vetoes(module.key, cache, season, [t for g in slate for t in (g.home, g.away)])

    out = {}
    lookup = games.set_index("game_id")
    for g in slate:
        gid = id_map.get(g.id)
        rows = proj[proj["game_id"] == gid]
        if len(rows) != 2:
            continue
        home_team = lookup.at[gid, "home"]
        h = rows[rows["team"] == home_team].iloc[0]
        a = rows[rows["team"] != home_team].iloc[0]
        experts = {e: (float(h[e]), float(a[e])) for e in ("formula", "forest", "ratings")}
        p = StatsProjection(home=float(h["consensus"]), away=float(a["consensus"]), experts=experts, rules=rules,
                            min_gp=int(min(h["gp"], a["gp"])))
        p.notes.append(" · ".join(f"{e} {hh:.0f}-{aa:.0f}" for e, (hh, aa) in experts.items()))
        p.game_vetoes += hurt.get(g.home, []) + hurt.get(g.away, [])
        out[g.id] = p
    return out
