"""Opponent-adjusted offense/defense ratings shared by every sport.

Two forms:
  * additive (football, basketball):   points = mean + off[team] + def[opp] +/- hfa/2
  * multiplicative (hockey, baseball, soccer): goals = mean * att[team] * def[opp] * home^(+/-1/2)

Ratings are shrunk toward average by SHRINK pseudo-games, recent games weigh more
(exponential time decay) and home advantage is fitted from the data with a prior.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime

from .models import Game


@dataclass
class RatingParams:
    multiplicative: bool
    home_adv: float  # prior: points (additive) or home/away scoring ratio (multiplicative)
    shrink: float = 3.0  # pseudo-games of average performance added to every team
    half_life_days: float | None = None  # None = all games weigh the same
    home_prior_games: float = 200.0
    iterations: int = 50
    mov_cap: float | None = None  # margins beyond this are square-root dampened (college blowouts)


@dataclass
class Ratings:
    params: RatingParams
    mean: float
    home_adv: float
    offense: dict[str, float] = field(default_factory=dict)
    defense: dict[str, float] = field(default_factory=dict)
    games_played: dict[str, int] = field(default_factory=dict)

    def strength(self, team: str) -> float:
        if self.params.multiplicative:
            return math.log(self.offense.get(team, 1.0)) - math.log(self.defense.get(team, 1.0))
        return self.offense.get(team, 0.0) - self.defense.get(team, 0.0)

    def expected(self, home: str, away: str, neutral: bool = False) -> tuple[float, float]:
        if self.params.multiplicative:
            h = 1.0 if neutral else math.sqrt(self.home_adv)
            return (self.mean * self.offense.get(home, 1.0) * self.defense.get(away, 1.0) * h,
                    self.mean * self.offense.get(away, 1.0) * self.defense.get(home, 1.0) / h)
        h = 0.0 if neutral else self.home_adv / 2
        return (self.mean + self.offense.get(home, 0.0) + self.defense.get(away, 0.0) + h,
                self.mean + self.offense.get(away, 0.0) + self.defense.get(home, 0.0) - h)


def fit_ratings(games: list[Game], params: RatingParams, as_of: datetime | None = None,
                team_filter=None) -> Ratings:
    """Fit on completed games. team_filter(name) -> pooled name lets callers lump minor teams together."""
    rows = []  # (team, opp, score, home sign, weight)
    played: dict[str, int] = {}
    for gm in games:
        if not gm.completed or gm.home_score is None or gm.away_score is None:
            continue
        home = team_filter(gm.home) if team_filter else gm.home
        away = team_filter(gm.away) if team_filter else gm.away
        w = 1.0
        if params.half_life_days and as_of:
            age = max(0.0, (as_of - gm.start).total_seconds() / 86400)
            w = 0.5 ** (age / params.half_life_days)
        s = 0 if gm.neutral else 1
        hs, as_ = float(gm.home_score), float(gm.away_score)
        if params.mov_cap is not None and abs(hs - as_) > params.mov_cap:
            # Running up the score past the cap only counts with diminishing returns.
            margin = math.copysign(params.mov_cap + math.sqrt(abs(hs - as_) - params.mov_cap), hs - as_)
            total = hs + as_
            hs, as_ = (total + margin) / 2, (total - margin) / 2
        rows.append((home, away, hs, s, w))
        rows.append((away, home, as_, -s, w))
        played[home] = played.get(home, 0) + 1
        played[away] = played.get(away, 0) + 1

    neutral_value = 1.0 if params.multiplicative else 0.0
    if not rows:
        return Ratings(params, mean=0.0, home_adv=params.home_adv)

    total_w = sum(r[4] for r in rows)
    mean = sum(r[2] * r[4] for r in rows) / total_w
    teams = set(played)
    scored = {t: [] for t in teams}
    allowed = {t: [] for t in teams}
    for row in rows:
        scored[row[0]].append(row)
        allowed[row[1]].append(row)
    off = dict.fromkeys(teams, neutral_value)
    dfn = dict.fromkeys(teams, neutral_value)
    hfa = params.home_adv
    k = params.shrink

    for _ in range(params.iterations):
        if params.multiplicative:
            hh = math.sqrt(hfa)
            for t in teams:
                num = sum(w * pts for _, _, pts, _, w in scored[t]) + k * mean
                den = sum(w * mean * dfn[o] * hh ** s for _, o, _, s, w in scored[t]) + k * mean
                off[t] = num / den
            for t in teams:
                num = sum(w * pts for _, _, pts, _, w in allowed[t]) + k * mean
                den = sum(w * mean * off[tm] * hh ** s for tm, _, _, s, w in allowed[t]) + k * mean
                dfn[t] = num / den
            g_off = math.exp(sum(math.log(v) for v in off.values()) / len(off))
            g_def = math.exp(sum(math.log(v) for v in dfn.values()) / len(dfn))
            off = {t: v / g_off for t, v in off.items()}
            dfn = {t: v / g_def for t, v in dfn.items()}
            obs = {1: 0.0, -1: 0.0}
            exp = {1: 0.0, -1: 0.0}
            for tm, o, pts, s, w in rows:
                if s:
                    obs[s] += w * pts
                    exp[s] += w * mean * off[tm] * dfn[o]
            n_home = sum(r[4] for r in rows if r[3] == 1)
            if n_home and exp[1] and exp[-1] and obs[-1]:
                data = (obs[1] / exp[1]) / (obs[-1] / exp[-1])
                m = params.home_prior_games
                hfa = math.exp((math.log(data) * n_home + math.log(params.home_adv) * m) / (n_home + m))
        else:
            for t in teams:
                resid = sum(w * (pts - mean - dfn[o] - s * hfa / 2) for _, o, pts, s, w in scored[t])
                off[t] = resid / (sum(r[4] for r in scored[t]) + k)
            for t in teams:
                resid = sum(w * (pts - mean - off[tm] - s * hfa / 2) for tm, _, pts, s, w in allowed[t])
                dfn[t] = resid / (sum(r[4] for r in allowed[t]) + k)
            off_mean = sum(off.values()) / len(off)
            dfn_mean = sum(dfn.values()) / len(dfn)
            off = {t: v - off_mean for t, v in off.items()}
            dfn = {t: v - dfn_mean for t, v in dfn.items()}
            num = sum(w * s * (pts - mean - off[tm] - dfn[o]) for tm, o, pts, s, w in rows if s)
            n_home = sum(r[4] for r in rows if r[3] == 1)
            m = params.home_prior_games
            # each home game contributes +h/2 and -h/2 rows, so the data estimate is num / n_home
            hfa = (num + params.home_adv * m) / (n_home + m) if n_home else params.home_adv

    return Ratings(params, mean=mean, home_adv=hfa, offense=off, defense=dfn, games_played=played)
