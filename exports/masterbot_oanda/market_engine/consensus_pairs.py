from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from market_engine.consensus import ConsensusResult


@dataclass
class ConsensusPair:
    market_type: str
    side_a: str
    side_b: str
    price_a: int
    price_b: int
    line_a: Optional[float]
    line_b: Optional[float]
    is_valid: bool
    reason: str


PAIR_MAP = {
    "totals": ("over", "under"),
    "spreads": ("home", "away"),
    "h2h": ("home", "away"),
}


def _normalize_side(side: str) -> str:
    return side.strip().lower()


def build_consensus_pairs(results: List[ConsensusResult]) -> List[ConsensusPair]:
    grouped: Dict[str, Dict[str, ConsensusResult]] = {}

    for result in results:
        market_type = result.market_type.strip().lower()
        side = _normalize_side(result.side)
        grouped.setdefault(market_type, {})[side] = result

    pairs: List[ConsensusPair] = []

    for market_type, expected_sides in PAIR_MAP.items():
        side_map = grouped.get(market_type, {})
        side_a, side_b = expected_sides

        a = side_map.get(side_a)
        b = side_map.get(side_b)

        if a is None or b is None:
            pairs.append(
                ConsensusPair(
                    market_type=market_type,
                    side_a=side_a,
                    side_b=side_b,
                    price_a=0,
                    price_b=0,
                    line_a=None,
                    line_b=None,
                    is_valid=False,
                    reason=f"Missing one side of pair for {market_type}.",
                )
            )
            continue

        if not a.is_stable or not b.is_stable:
            pairs.append(
                ConsensusPair(
                    market_type=market_type,
                    side_a=side_a,
                    side_b=side_b,
                    price_a=a.consensus_price_american or 0,
                    price_b=b.consensus_price_american or 0,
                    line_a=a.consensus_line,
                    line_b=b.consensus_line,
                    is_valid=False,
                    reason=f"Consensus unstable for {market_type}.",
                )
            )
            continue

        if a.consensus_price_american is None or b.consensus_price_american is None:
            pairs.append(
                ConsensusPair(
                    market_type=market_type,
                    side_a=side_a,
                    side_b=side_b,
                    price_a=0,
                    price_b=0,
                    line_a=a.consensus_line,
                    line_b=b.consensus_line,
                    is_valid=False,
                    reason=f"Missing consensus prices for {market_type}.",
                )
            )
            continue

        pairs.append(
            ConsensusPair(
                market_type=market_type,
                side_a=side_a,
                side_b=side_b,
                price_a=a.consensus_price_american,
                price_b=b.consensus_price_american,
                line_a=a.consensus_line,
                line_b=b.consensus_line,
                is_valid=True,
                reason="Consensus pair built successfully.",
            )
        )

    return pairs
