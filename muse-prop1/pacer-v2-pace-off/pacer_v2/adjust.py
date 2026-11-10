"""Minutes, timing, opponent defense and pace.

These adjustments move the v1 probability. They do not place a bet by themselves.
Missing inputs leave the multiplier at 1.0 and record a gap. See ``pacer_v2.GAPS``.
"""

from __future__ import annotations

import math

from .parse import BASKETBALL_STATS, FOOTBALL_STATS, Row

# Logit shift is delta_stat / scale. Scales are remaining-production units, not fitted edges.
STAT_SCALE = {
    "pts": 6.0,
    "reb": 2.5,
    "ast": 2.0,
    "pra": 8.0,
    "3pm": 1.2,
    "py": 45.0,
    "ry": 18.0,
    "recy": 16.0,
    "rec": 1.5,
    "td": 0.45,
}

# season_rate at or below this is per minute; above it is per game.
PER_MIN_CAP = {
    "pts": 1.5,
    "reb": 0.8,
    "ast": 0.7,
    "pra": 2.5,
    "3pm": 0.35,
    "py": 12.0,
    "ry": 8.0,
    "recy": 8.0,
    "rec": 0.6,
    "td": 0.15,
}

B2B_FACTOR = 0.92
COLLEGE_LEAGUES = frozenset({"ncaab", "ncaaw", "cbb", "wbb", "wncaab", "ncaa"})


def sport_of(row: Row) -> str:
    if row.stat in FOOTBALL_STATS:
        return "football"
    if row.stat in BASKETBALL_STATS:
        return "basketball"
    league = (row.league or "").lower()
    if any(tok in league for tok in ("nfl", "cfb", "football", "ncaaf")):
        return "football"
    return "basketball"


def regulation_minutes(row: Row, sport: str) -> float:
    if row.regulation_minutes and row.regulation_minutes > 0:
        return float(row.regulation_minutes)
    if sport == "football":
        return 60.0
    if (row.league or "").lower() in COLLEGE_LEAGUES:
        return 40.0
    return 48.0


def period_length(row: Row, sport: str, regulation: float) -> float:
    if sport == "football":
        return 15.0
    if abs(regulation - 40.0) < 0.1:
        return 20.0
    return 12.0


def foul_limit_of(row: Row, sport: str, regulation: float) -> int:
    if row.foul_limit and row.foul_limit > 0:
        return int(row.foul_limit)
    if sport != "basketball":
        return 6
    if abs(regulation - 40.0) < 0.1 or (row.league or "").lower() in COLLEGE_LEAGUES:
        return 5
    return 6


def game_clock(row: Row, sport: str) -> dict:
    """Elapsed and remaining game minutes. Unknown clock does not invent a time.

    A period label of ``ot``, or a period number past regulation (5th NBA period,
    3rd college half, 5th football quarter), is overtime. Overtime length is a flat
    5 minutes in basketball and 10 in football.
    """
    gaps: list[str] = []
    reg = regulation_minutes(row, sport)
    plen = period_length(row, sport, reg)
    reg_periods = max(1, int(round(reg / plen))) if plen else 1
    clock = row.clock_remaining_sec
    ot = (row.period_label or "") == "ot" or (
        row.period is not None and row.period > reg_periods
    )
    if ot:
        ot_len = 5.0 if sport == "basketball" else 10.0
        extra = 1
        if row.period is not None and row.period > reg_periods:
            extra = row.period - reg_periods
        if clock is None:
            gaps.append("ot_clock_missing")
            remaining = ot_len
            elapsed = reg + (extra - 1) * ot_len
        else:
            remaining = max(0.0, min(clock / 60.0, ot_len))
            elapsed = reg + (extra - 1) * ot_len + (ot_len - remaining)
        return {"elapsed": elapsed, "remaining": remaining, "ot": True, "plen": ot_len,
                "reg": reg, "gaps": gaps}
    if row.period is not None and clock is not None:
        elapsed = (row.period - 1) * plen + (plen - clock / 60.0)
        elapsed = min(max(0.0, elapsed), reg)
        remaining = max(0.0, reg - elapsed)
        return {"elapsed": elapsed, "remaining": remaining, "ot": False, "plen": plen,
                "reg": reg, "gaps": gaps}
    # Player minutes are not the game clock in basketball (a starter can have 10 minutes
    # played at the 8-minute mark of the second quarter, or 10 minutes at the half).
    if sport == "football" and row.minutes_played is not None:
        gaps.append("elapsed_from_minutes_played")
        elapsed = min(max(0.0, row.minutes_played), reg)
        return {"elapsed": elapsed, "remaining": max(0.0, reg - elapsed), "ot": False,
                "plen": plen, "reg": reg, "gaps": gaps}
    gaps.append("game_clock_unknown")
    return {"elapsed": None, "remaining": None, "ot": False, "plen": plen, "reg": reg, "gaps": gaps}


def timing_reason(clock: dict, early_min: float, late_min: float) -> str | None:
    """Skip the first ~6 game-minutes and the last ~3. Unknown clock does not skip."""
    if clock["elapsed"] is None:
        return None
    if not clock["ot"] and clock["elapsed"] < early_min:
        return "timing_early"
    if clock["remaining"] is not None and clock["remaining"] < late_min:
        return "timing_late"
    return None


def blowout_factor(row: Row, clock: dict, sport: str) -> tuple[float, str | None]:
    """Large lead late in the game cuts the minutes a primary player is expected to play.

    Late means 75% of regulation, or any overtime. Basketball: |margin| >= 25 -> 0.40,
    >= 18 -> 0.65. Football: |margin| >= 28 -> 0.35, >= 17 -> 0.50.
    ``role=backup`` does not get the cut (garbage time is not modeled either way).
    """
    if row.role == "backup":
        return 1.0, "blowout_skipped_backup_role"
    if row.margin is None:
        return 1.0, "margin_missing"
    if clock["elapsed"] is None or not clock["reg"]:
        return 1.0, None
    progress = clock["elapsed"] / clock["reg"]
    late = bool(clock["ot"]) or progress >= 0.75
    if not late:
        return 1.0, None
    mag = abs(row.margin)
    if sport == "basketball":
        if mag >= 25:
            return 0.40, None
        if mag >= 18:
            return 0.65, None
        return 1.0, None
    if mag >= 28:
        return 0.35, None
    if mag >= 17:
        return 0.50, None
    return 1.0, None


def foul_factor(row: Row, clock: dict, sport: str, limit: int) -> tuple[float, str | None]:
    """Basketball only. Football fouls are not player fouls; they are ignored.

    fouls >= limit: 0.15 (fouled out). One under the limit before 90% of the game: 0.50.
    Two under the limit before half: 0.80.
    """
    if sport != "basketball":
        if row.fouls is not None:
            return 1.0, "fouls_ignored_football"
        return 1.0, None
    if row.fouls is None:
        return 1.0, "fouls_missing"
    progress = None
    if clock["elapsed"] is not None and clock["reg"]:
        progress = clock["elapsed"] / clock["reg"]
    if row.fouls >= limit:
        return 0.15, None
    if row.fouls >= limit - 1 and (progress is None or progress < 0.90):
        return 0.50, None
    if row.fouls >= limit - 2 and progress is not None and progress < 0.50:
        return 0.80, None
    return 1.0, None


def load_team_stats(path: str | None) -> dict:
    if not path:
        return {}
    from .parse import load_json
    data = load_json(path)
    return data


def context_multipliers(row: Row, stats: dict | None) -> tuple[float, float, bool, bool, list[str]]:
    """Return def_mult, pace_mult, b2b, b2b_known, gaps.

    Defense is the opponent's factor for this stat (< 1 suppresses production).
    Pace is the game override, else the mean of the listed team paces, else 1.
    """
    gaps: list[str] = []
    stats = stats or {}
    if not stats:
        gaps.append("no_team_stats")
        b2b, known = _b2b(row, {})
        if not known:
            gaps.append("b2b_not_flagged")
        return 1.0, 1.0, b2b, known, gaps

    teams = stats.get("teams") or {}
    games = stats.get("games") or {}
    game = {}
    if row.game_id:
        game = games.get(row.game_id) or games.get(str(row.game_id)) or {}
    opp = teams.get(row.opponent) if row.opponent else None
    own = teams.get(row.team) if row.team else None
    opp = opp or {}
    own = own or {}

    def_mult, def_found = _def_mult(row, game, opp)
    if not def_found:
        gaps.append("def_default_1")

    pace, pace_found = _pace_mult(game, own, opp)
    if not pace_found:
        gaps.append("pace_default_1")

    b2b, known = _b2b(row, stats)
    if not known:
        gaps.append("b2b_not_flagged")
    return def_mult, pace, b2b, known, gaps


def _def_mult(row: Row, game: dict, opp: dict) -> tuple[float, bool]:
    od = game.get("opp_def", game.get("def"))
    if isinstance(od, (int, float)) and not isinstance(od, bool):
        return float(od), True
    if isinstance(od, dict) and row.stat in od and _is_num(od[row.stat]):
        return float(od[row.stat]), True
    block = opp.get("def")
    if isinstance(block, dict) and row.stat in block and _is_num(block[row.stat]):
        return float(block[row.stat]), True
    if _is_num(opp.get("def_factor")):
        return float(opp["def_factor"]), True
    if _is_num(block):
        return float(block), True
    return 1.0, False


def _pace_mult(game: dict, own: dict, opp: dict) -> tuple[float, bool]:
    if _is_num(game.get("pace")):
        return float(game["pace"]), True
    paces = []
    for src in (own, opp):
        if _is_num(src.get("pace")):
            paces.append(float(src["pace"]))
    if paces:
        return sum(paces) / len(paces), True
    return 1.0, False


def _b2b(row: Row, stats: dict) -> tuple[bool, bool]:
    if row.b2b is not None:
        return bool(row.b2b), True
    teams = set(stats.get("b2b_teams") or [])
    players = set(stats.get("b2b_players") or [])
    if row.team and row.team in teams:
        return True, True
    if row.player and row.player in players:
        return True, True
    if teams or players:
        return False, True  # list was provided and this row is not on it
    return False, False


def per_minute_rate(row: Row, sport: str, gaps: list[str]) -> float | None:
    if row.season_rate is None:
        if row.cur is not None and row.minutes_played and row.minutes_played > 0:
            gaps.append("rate_from_minutes_so_far")
            return row.cur / row.minutes_played
        gaps.append("no_rate")
        return None
    unit = (row.rate_unit or "").lower()
    if unit in {"per_game", "game"}:
        gaps.append("season_rate_per_game")
        return row.season_rate / (32.0 if sport == "basketball" else 60.0)
    if unit in {"per_min", "minute", "per_minute"}:
        return row.season_rate
    cap = PER_MIN_CAP.get(row.stat or "", 2.0)
    if row.season_rate <= cap:
        return row.season_rate
    gaps.append("season_rate_treated_as_per_game")
    return row.season_rate / (32.0 if sport == "basketball" else 60.0)


def production_shift(row: Row, clock: dict, rate: float | None, minute_ratio: float,
                     def_mult: float, pace_mult: float) -> tuple[float, float | None]:
    """Logit shift applied to P(over), and the stat-unit delta when a rate exists.

    With a rate and a clock, delta = rate * remaining * (minute_ratio * def * pace - 1)
    and the shift is delta / stat scale. Without them, the shift is log(minute_ratio * def * pace),
    which is zero when every factor is 1.
    """
    context = minute_ratio * def_mult * pace_mult
    base = clock["remaining"]
    scale = STAT_SCALE.get(row.stat or "", 5.0)
    if rate is not None and base is not None:
        delta = rate * base * (context - 1.0)
        return delta / scale, delta
    if abs(context - 1.0) < 1e-12:
        return 0.0, None
    return math.log(max(context, 1e-3)), None


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)
