#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class GameState:
    game: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    clock: str
    period: int
    game_type: str
    phase: str
    leader: str
    score_diff: int
    pace: str
    pressure: str
    one_possession_game: bool
    two_possession_game: bool
    comeback_window: bool
    foul_window: bool
    blowout_flag: bool
    summary: str


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def detect_game_type(payload: Dict[str, Any]) -> str:
    sport = str(payload.get("sport_key", "")).lower()
    if "wncaab" in sport or "women" in sport:
        return "women"
    return "men"


def _leader_and_diff(home_team: str, away_team: str, home_score: int, away_score: int) -> tuple[str, int]:
    if home_score > away_score:
        return home_team, home_score - away_score
    if away_score > home_score:
        return away_team, away_score - home_score
    return "tied", 0


def _phase_and_flags(
    game_type: str,
    period: int,
    clock: str,
    score_diff: int,
) -> tuple[str, str, str, bool, bool]:
    """
    Returns:
      phase, pace_placeholder, pressure, comeback_window, foul_window
    """
    try:
        mins_left = int(str(clock).split(":")[0])
    except Exception:
        mins_left = 0

    if game_type == "women":
        if period <= 1:
            phase = "early_q1"
        elif period == 2:
            phase = "mid_q2" if mins_left > 5 else "late_q2"
        elif period == 3:
            phase = "mid_q3" if mins_left > 5 else "late_q3"
        elif period >= 4:
            if mins_left <= 2:
                phase = "closing_time"
            elif mins_left <= 5:
                phase = "late_q4"
            else:
                phase = "mid_q4"
        else:
            phase = "unknown"

        comeback_window = period >= 4 and score_diff <= 10
        foul_window = period >= 4 and mins_left <= 2 and score_diff <= 8
        pressure = "high" if (period >= 4 and score_diff <= 8) else "normal"

    else:
        if period <= 1:
            if mins_left > 12:
                phase = "early_1h"
            elif mins_left > 6:
                phase = "mid_1h"
            else:
                phase = "late_1h"
        elif period >= 2:
            if mins_left <= 2:
                phase = "closing_time"
            elif mins_left <= 6:
                phase = "late_2h"
            elif mins_left <= 12:
                phase = "mid_2h"
            else:
                phase = "early_2h"
        else:
            phase = "unknown"

        comeback_window = period >= 2 and score_diff <= 10
        foul_window = period >= 2 and mins_left <= 2 and score_diff <= 8
        pressure = "high" if (period >= 2 and score_diff <= 8 and mins_left <= 6) else "normal"

    return phase, pressure, comeback_window, foul_window


def _pace_label(game_type: str, total_points: int, period: int) -> str:
    if game_type == "women":
        if period <= 2:
            if total_points < 40:
                return "slow"
            if total_points < 70:
                return "normal"
            return "fast"
        else:
            if total_points < 90:
                return "slow"
            if total_points < 130:
                return "normal"
            return "fast"

    # men
    if period <= 1:
        if total_points < 45:
            return "slow"
        if total_points < 75:
            return "normal"
        return "fast"
    else:
        if total_points < 110:
            return "slow"
        if total_points < 150:
            return "normal"
        return "fast"


def build_game_state(payload: Dict[str, Any]) -> GameState:
    home_team = str(payload.get("home_team", "Home"))
    away_team = str(payload.get("away_team", "Away"))

    home_score = _to_int(payload.get("home_score", 0))
    away_score = _to_int(payload.get("away_score", 0))

    clock = str(
        payload.get(
            "clock",
            f"{_to_int(payload.get('minutes_left', 0))}:{str(_to_int(payload.get('seconds_left', 0))).zfill(2)}",
        )
    )

    period = _to_int(payload.get("period", 0))
    if period == 0:
        # fallback from mode if explicit period missing
        mode = str(payload.get("mode", "")).lower()
        if mode == "1h":
            period = 1
        elif mode == "2h":
            period = 2
        else:
            period = 1

    game_type = detect_game_type(payload)

    leader, score_diff = _leader_and_diff(home_team, away_team, home_score, away_score)
    total_points = home_score + away_score

    phase, pressure, comeback_window, foul_window = _phase_and_flags(
        game_type=game_type,
        period=period,
        clock=clock,
        score_diff=score_diff,
    )

    pace = _pace_label(game_type, total_points, period)

    one_possession_game = score_diff <= 3
    two_possession_game = score_diff <= 6
    blowout_flag = score_diff >= 18

    # special late-game women chaos hint
    if game_type == "women" and period >= 4 and foul_window and pace == "fast":
        pace = "foul_chaos"

    # special late-game men chaos hint
    if game_type == "men" and period >= 2 and foul_window and score_diff <= 8:
        pace = "foul_chaos"

    leader_text = f"{leader} by {score_diff}" if leader != "tied" else "tied"
    phase_label = phase

    summary = (
        f"{away_team} {away_score} @ {home_team} {home_score} | "
        f"{clock} "
        f"{'Q'+str(period) if game_type == 'women' else ('1H' if period == 1 else '2H')} | "
        f"leader={leader_text} | phase={phase_label} | pressure={pressure} | pace={pace}"
    )

    return GameState(
        game=f"{away_team} @ {home_team}",
        home_team=home_team,
        away_team=away_team,
        home_score=home_score,
        away_score=away_score,
        clock=clock,
        period=period,
        game_type=game_type,
        phase=phase,
        leader=leader,
        score_diff=score_diff,
        pace=pace,
        pressure=pressure,
        one_possession_game=one_possession_game,
        two_possession_game=two_possession_game,
        comeback_window=comeback_window,
        foul_window=foul_window,
        blowout_flag=blowout_flag,
        summary=summary,
    )


if __name__ == "__main__":
    women_test = {
        "sport_key": "basketball_wncaab",
        "home_team": "UNC Women",
        "away_team": "Duke Women",
        "home_score": 32,
        "away_score": 28,
        "period": 2,
        "clock": "5:12",
    }

    men_test = {
        "sport_key": "basketball_ncaab",
        "home_team": "UNC",
        "away_team": "Duke",
        "home_score": 45,
        "away_score": 42,
        "period": 1,
        "clock": "3:10",
    }

    print("WOMEN TEST:")
    print(build_game_state(women_test))

    print("\nMEN TEST:")
    print(build_game_state(men_test))
