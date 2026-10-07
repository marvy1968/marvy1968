"""Opponent-adjusted offense/defense ratings and collegiate pace factors."""

from dataclasses import dataclass, field

FCS = "__FCS__"  # every non-FBS opponent shares one pooled rating
HOME_FIELD = 2.5  # points, split evenly between home offense and away offense
SHRINK_GAMES = 3.0  # ratings are shrunk by n / (n + SHRINK_GAMES) toward average
PACE_WEIGHT = 0.5  # ratings already capture some pace, so only half of it is applied again


def g(game: dict, camel: str, snake: str, default=None):
    """CFBD responses moved from snake_case to camelCase; accept either."""
    return game.get(camel, game.get(snake, default))


def is_fbs(game: dict, side: str) -> bool:
    cls = g(game, f"{side}Classification", f"{side}_division")
    return cls is None or str(cls).lower() == "fbs"


@dataclass
class Ratings:
    mean_points: float
    offense: dict[str, float] = field(default_factory=dict)
    defense: dict[str, float] = field(default_factory=dict)  # points allowed above average (higher = worse)
    games_played: dict[str, int] = field(default_factory=dict)
    pace: dict[str, float] = field(default_factory=dict)

    def team_key(self, team: str) -> str:
        return team if team in self.offense else FCS

    def strength(self, team: str) -> float:
        key = self.team_key(team)
        return self.offense.get(key, 0.0) - self.defense.get(key, 0.0)

    def game_pace(self, home: str, away: str) -> float:
        return (self.pace.get(home, 1.0) + self.pace.get(away, 1.0)) / 2

    def expected_points(self, home: str, away: str, neutral: bool) -> tuple[float, float]:
        h, a = self.team_key(home), self.team_key(away)
        hfa = 0.0 if neutral else HOME_FIELD / 2
        home_pts = self.mean_points + self.offense.get(h, 0) + self.defense.get(a, 0) + hfa
        away_pts = self.mean_points + self.offense.get(a, 0) + self.defense.get(h, 0) - hfa
        scale = 1 + PACE_WEIGHT * (self.game_pace(home, away) - 1)
        return max(3.0, home_pts * scale), max(3.0, away_pts * scale)


def _team(game: dict, side: str) -> str:
    return g(game, f"{side}Team", f"{side}_team") if is_fbs(game, side) else FCS


def fit_ratings(games: list[dict], iterations: int = 200) -> Ratings:
    """Fit points = mean + offense[team] + defense[opponent] +/- home field, by alternating averages."""
    rows = []  # (scoring team, opponent, points, home-field adjustment)
    for game in games:
        hp, ap = g(game, "homePoints", "home_points"), g(game, "awayPoints", "away_points")
        if hp is None or ap is None:
            continue
        home, away = _team(game, "home"), _team(game, "away")
        if home == FCS and away == FCS:
            continue
        hfa = 0.0 if g(game, "neutralSite", "neutral_site", False) else HOME_FIELD / 2
        rows.append((home, away, float(hp), hfa))
        rows.append((away, home, float(ap), -hfa))
    if not rows:
        return Ratings(mean_points=28.0)

    mean = sum(r[2] for r in rows) / len(rows)
    teams = {r[0] for r in rows}
    off = {t: 0.0 for t in teams}
    dfn = {t: 0.0 for t in teams}
    scored = {t: [] for t in teams}
    allowed = {t: [] for t in teams}
    for row in rows:
        scored[row[0]].append(row)
        allowed[row[1]].append(row)
    played = {t: len(scored[t]) for t in teams}

    for _ in range(iterations):
        for t in teams:
            resid = sum(pts - mean - dfn[opp] - hfa for _, opp, pts, hfa in scored[t])
            off[t] = resid / (len(scored[t]) + SHRINK_GAMES)
        for t in teams:
            resid = sum(pts - mean - off[team] - hfa for team, _, pts, hfa in allowed[t])
            dfn[t] = resid / (len(allowed[t]) + SHRINK_GAMES)
        off_mean = sum(off.values()) / len(off)
        dfn_mean = sum(dfn.values()) / len(dfn)
        off = {t: v - off_mean for t, v in off.items()}
        dfn = {t: v - dfn_mean for t, v in dfn.items()}

    return Ratings(mean_points=mean, offense=off, defense=dfn, games_played=played)


def pace_factors(season_stats: list[dict]) -> dict[str, float]:
    """Offensive plays per game relative to the league average (1.0 = average tempo)."""
    totals: dict[str, dict[str, float]] = {}
    for row in season_stats:
        name = g(row, "statName", "stat_name")
        if name in ("rushingAttempts", "passAttempts", "games"):
            totals.setdefault(row["team"], {})[name] = float(g(row, "statValue", "stat_value", 0) or 0)
    per_game = {
        team: (s.get("rushingAttempts", 0) + s.get("passAttempts", 0)) / s["games"]
        for team, s in totals.items()
        if s.get("games")
    }
    per_game = {t: v for t, v in per_game.items() if v > 0}
    if not per_game:
        return {}
    avg = sum(per_game.values()) / len(per_game)
    return {t: v / avg for t, v in per_game.items()}
