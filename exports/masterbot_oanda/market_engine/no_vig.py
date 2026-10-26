from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class NoVigOutcome:
    side: str
    offered_american: int
    implied_prob_raw: float
    fair_prob: float


@dataclass
class NoVigResult:
    market_type: str
    outcomes: List[NoVigOutcome]
    overround: float
    is_valid: bool
    reason: str


def american_to_implied_prob(odds: int) -> float:
    if odds == 0:
        raise ValueError("American odds cannot be zero.")
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def remove_vig_two_way(
    market_type: str,
    side_a: str,
    odds_a: int,
    side_b: str,
    odds_b: int,
) -> NoVigResult:
    p_a = american_to_implied_prob(odds_a)
    p_b = american_to_implied_prob(odds_b)

    overround = p_a + p_b
    if overround <= 0:
        return NoVigResult(
            market_type=market_type,
            outcomes=[],
            overround=overround,
            is_valid=False,
            reason="Invalid overround; cannot normalize.",
        )

    fair_a = p_a / overround
    fair_b = p_b / overround

    return NoVigResult(
        market_type=market_type,
        outcomes=[
            NoVigOutcome(
                side=side_a,
                offered_american=odds_a,
                implied_prob_raw=p_a,
                fair_prob=fair_a,
            ),
            NoVigOutcome(
                side=side_b,
                offered_american=odds_b,
                implied_prob_raw=p_b,
                fair_prob=fair_b,
            ),
        ],
        overround=overround,
        is_valid=True,
        reason="Two-way no-vig probabilities computed successfully.",
    )


def find_fair_prob_for_side(result: NoVigResult, side: str) -> Optional[float]:
    target = side.strip().lower()
    for outcome in result.outcomes:
        if outcome.side.strip().lower() == target:
            return outcome.fair_prob
    return None
