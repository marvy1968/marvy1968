from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List

from live_engine.foul_extension import FoulExtensionResult


@dataclass
class LiveGameState:
    seconds_remaining: int
    home_score: int
    away_score: int
    possessions_per_min: float
    points_per_possession: float


@dataclass
class SimulationResult:
    projected_total: float
    projected_home_score: float
    projected_away_score: float
    distribution: List[float]


def simulate_possession(points_per_possession: float) -> float:
    """
    Simple possession outcome model.
    """
    r = random.random()

    if r < 0.50:
        return 0
    if r < 0.75:
        return 2
    if r < 0.90:
        return 3
    return 1


def run_live_simulation(
    state: LiveGameState,
    foul_extension: FoulExtensionResult,
    simulations: int = 5000,
) -> SimulationResult:
    totals: List[float] = []

    for _ in range(simulations):
        home = state.home_score
        away = state.away_score
        seconds = state.seconds_remaining
        possessions_per_second = state.possessions_per_min / 60.0

        while seconds > 0:
            if random.random() < possessions_per_second:
                pts = simulate_possession(state.points_per_possession)
                if random.random() < 0.5:
                    home += pts
                else:
                    away += pts
            seconds -= 1

        total = home + away

        if foul_extension.is_active:
            total += foul_extension.inflation_points * random.uniform(0.7, 1.3)

        totals.append(total)

    projected_total = sum(totals) / len(totals)
    added_points = projected_total - (state.home_score + state.away_score)

    projected_home = state.home_score + (added_points / 2.0)
    projected_away = state.away_score + (added_points / 2.0)

    return SimulationResult(
        projected_total=round(projected_total, 2),
        projected_home_score=round(projected_home, 2),
        projected_away_score=round(projected_away, 2),
        distribution=totals,
    )
