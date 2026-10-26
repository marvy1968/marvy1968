from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FoulExtensionInput:
    universe: str
    seconds_remaining: int
    score_diff: int
    trailing_team_fouls: int
    leading_team_fouls: int
    trailing_team_timeouts: int
    leading_team_timeouts: int
    possession_team: Optional[str]   # "trailing", "leading", or None
    trailing_team_ft_pct: float
    leading_team_ft_pct: float
    recent_possessions_per_min: float
    baseline_possessions_per_min: float
    recent_points_per_possession: float
    baseline_points_per_possession: float


@dataclass
class FoulExtensionResult:
    is_active: bool
    inflation_points: float
    confidence: float
    foul_risk_score: float
    pace_lag_score: float
    reason: str


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def _bonus_state_score(universe: str, trailing_team_fouls: int) -> float:
    if universe == "mens":
        if trailing_team_fouls >= 10:
            return 1.0
        if trailing_team_fouls >= 7:
            return 0.75
        if trailing_team_fouls >= 5:
            return 0.35
        return 0.0

    if universe == "womens":
        if trailing_team_fouls >= 5:
            return 1.0
        if trailing_team_fouls >= 4:
            return 0.65
        if trailing_team_fouls >= 3:
            return 0.30
        return 0.0

    return 0.0


def _time_pressure_score(universe: str, seconds_remaining: int) -> float:
    if universe == "mens":
        if seconds_remaining <= 90:
            return 1.0
        if seconds_remaining <= 150:
            return 0.9
        if seconds_remaining <= 210:
            return 0.75
        if seconds_remaining <= 270:
            return 0.55
        return 0.20

    if universe == "womens":
        if seconds_remaining <= 75:
            return 1.0
        if seconds_remaining <= 135:
            return 0.88
        if seconds_remaining <= 195:
            return 0.70
        if seconds_remaining <= 240:
            return 0.50
        return 0.20

    return 0.0


def _margin_score(score_diff: int) -> float:
    if score_diff <= 2:
        return 0.70
    if score_diff <= 4:
        return 1.00
    if score_diff <= 6:
        return 0.90
    if score_diff <= 8:
        return 0.72
    if score_diff <= 10:
        return 0.48
    return 0.10


def _timeout_score(trailing_team_timeouts: int) -> float:
    if trailing_team_timeouts >= 3:
        return 1.0
    if trailing_team_timeouts == 2:
        return 0.75
    if trailing_team_timeouts == 1:
        return 0.45
    return 0.15


def _ft_conversion_score(leading_team_ft_pct: float) -> float:
    ft = _clamp(leading_team_ft_pct, 0.0, 1.0)
    if ft >= 0.80:
        return 1.0
    if ft >= 0.75:
        return 0.85
    if ft >= 0.70:
        return 0.65
    if ft >= 0.65:
        return 0.45
    return 0.20


def _pace_lag_score(
    recent_possessions_per_min: float,
    baseline_possessions_per_min: float,
    recent_points_per_possession: float,
    baseline_points_per_possession: float,
) -> float:
    pace_ratio = _safe_div(
        recent_possessions_per_min,
        baseline_possessions_per_min,
        default=1.0,
    )
    ppp_ratio = _safe_div(
        recent_points_per_possession,
        baseline_points_per_possession,
        default=1.0,
    )

    pace_drop = _clamp(1.0 - pace_ratio, 0.0, 1.0)
    scoring_drop = _clamp(1.0 - ppp_ratio, 0.0, 1.0)

    return _clamp((0.60 * pace_drop) + (0.40 * scoring_drop), 0.0, 1.0)


def evaluate_foul_extension(data: FoulExtensionInput) -> FoulExtensionResult:
    universe = data.universe.lower().strip()
    if universe not in {"mens", "womens"}:
        return FoulExtensionResult(
            is_active=False,
            inflation_points=0.0,
            confidence=0.0,
            foul_risk_score=0.0,
            pace_lag_score=0.0,
            reason="Unsupported universe.",
        )

    if data.seconds_remaining > 300 or data.score_diff > 12:
        return FoulExtensionResult(
            is_active=False,
            inflation_points=0.0,
            confidence=0.0,
            foul_risk_score=0.0,
            pace_lag_score=0.0,
            reason="Outside foul-extension window.",
        )

    bonus_score = _bonus_state_score(universe, data.trailing_team_fouls)
    time_score = _time_pressure_score(universe, data.seconds_remaining)
    margin_score = _margin_score(data.score_diff)
    timeout_score = _timeout_score(data.trailing_team_timeouts)
    ft_score = _ft_conversion_score(data.leading_team_ft_pct)

    possession_modifier = 1.0
    if data.possession_team == "leading":
        possession_modifier = 1.05
    elif data.possession_team == "trailing":
        possession_modifier = 0.95

    foul_risk_score = _clamp(
        (
            0.30 * bonus_score +
            0.25 * time_score +
            0.20 * margin_score +
            0.15 * timeout_score +
            0.10 * ft_score
        ) * possession_modifier,
        0.0,
        1.0,
    )

    pace_lag = _pace_lag_score(
        recent_possessions_per_min=data.recent_possessions_per_min,
        baseline_possessions_per_min=data.baseline_possessions_per_min,
        recent_points_per_possession=data.recent_points_per_possession,
        baseline_points_per_possession=data.baseline_points_per_possession,
    )

    confidence = _clamp((0.68 * foul_risk_score) + (0.32 * pace_lag), 0.0, 1.0)

    inflation_points = 0.0
    if confidence >= 0.55:
        if universe == "mens":
            inflation_points = (4.8 * foul_risk_score) + (2.4 * pace_lag)
        else:
            inflation_points = (4.2 * foul_risk_score) + (2.1 * pace_lag)

        if data.seconds_remaining <= 120 and data.score_diff <= 5:
            inflation_points += 0.8

        if bonus_score >= 0.75 and data.trailing_team_timeouts >= 2:
            inflation_points += 0.6

    inflation_points = round(_clamp(inflation_points, 0.0, 8.0), 2)
    is_active = confidence >= 0.55 and inflation_points > 0.0

    if not is_active:
        return FoulExtensionResult(
            is_active=False,
            inflation_points=0.0,
            confidence=round(confidence, 3),
            foul_risk_score=round(foul_risk_score, 3),
            pace_lag_score=round(pace_lag, 3),
            reason="Foul-extension conditions not strong enough.",
        )

    reason = (
        f"Late close-game foul inflation active | "
        f"foul_risk={foul_risk_score:.2f} | "
        f"pace_lag={pace_lag:.2f} | "
        f"adj=+{inflation_points:.2f}"
    )

    return FoulExtensionResult(
        is_active=True,
        inflation_points=inflation_points,
        confidence=round(confidence, 3),
        foul_risk_score=round(foul_risk_score, 3),
        pace_lag_score=round(pace_lag, 3),
        reason=reason,
    )
