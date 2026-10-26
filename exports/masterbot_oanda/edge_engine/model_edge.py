from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelEdgeResult:
    side: str
    market_line: float
    fair_line: float
    line_gap: float
    is_bet_recommended: bool
    reason: str


def evaluate_total_line_edge(
    side: str,
    market_line: float,
    fair_line: float,
    min_gap: float = 2.0,
) -> ModelEdgeResult:
    """
    For totals:
    - Over is good when fair_line is meaningfully above market_line
    - Under is good when fair_line is meaningfully below market_line
    """
    normalized_side = side.strip().lower()
    line_gap = fair_line - market_line

    if normalized_side == "over":
        is_rec = line_gap >= min_gap
        reason = (
            f"OVER edge: fair_line={fair_line:.2f}, market_line={market_line:.2f}, "
            f"gap={line_gap:+.2f}"
        )
    elif normalized_side == "under":
        is_rec = (-line_gap) >= min_gap
        reason = (
            f"UNDER edge: fair_line={fair_line:.2f}, market_line={market_line:.2f}, "
            f"gap={line_gap:+.2f}"
        )
    else:
        is_rec = False
        reason = f"Unsupported side '{side}' for totals model edge."

    return ModelEdgeResult(
        side=side,
        market_line=market_line,
        fair_line=fair_line,
        line_gap=line_gap,
        is_bet_recommended=is_rec,
        reason=reason,
    )
