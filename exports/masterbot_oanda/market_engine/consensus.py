from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Dict, List, Optional, Tuple


@dataclass
class ConsensusQuote:
    sportsbook_key: str
    market_type: str
    side: str
    line: Optional[float]
    price_american: Optional[int]
    source_updated_at_utc: Optional[str] = None


@dataclass
class ConsensusResult:
    market_type: str
    side: str
    book_count: int
    consensus_line: Optional[float]
    consensus_price_american: Optional[int]
    median_line: Optional[float]
    median_price_american: Optional[int]
    min_line: Optional[float]
    max_line: Optional[float]
    spread_points: Optional[float]
    is_stable: bool
    instability_reason: Optional[str]
    included_books: List[str]


def _median_int(values: List[int]) -> int:
    return int(round(median(values)))


def build_consensus(
    quotes: List[ConsensusQuote],
    min_books: int = 2,
    max_book_spread_points: float = 3.0,
) -> List[ConsensusResult]:
    grouped: Dict[Tuple[str, str], List[ConsensusQuote]] = {}

    for q in quotes:
        key = (q.market_type, q.side)
        grouped.setdefault(key, []).append(q)

    results: List[ConsensusResult] = []

    for (market_type, side), bucket in grouped.items():
        line_quotes = [q for q in bucket if q.line is not None]
        price_quotes = [q for q in bucket if q.price_american is not None]

        line_values = [float(q.line) for q in line_quotes]
        price_values = [int(q.price_american) for q in price_quotes]
        included_books = sorted({q.sportsbook_key for q in bucket})

        if len(line_values) < min_books:
            results.append(
                ConsensusResult(
                    market_type=market_type,
                    side=side,
                    book_count=len(line_values),
                    consensus_line=None,
                    consensus_price_american=_median_int(price_values) if price_values else None,
                    median_line=None,
                    median_price_american=_median_int(price_values) if price_values else None,
                    min_line=None,
                    max_line=None,
                    spread_points=None,
                    is_stable=False,
                    instability_reason=f"Only {len(line_values)} book(s) with valid lines; need {min_books}.",
                    included_books=included_books,
                )
            )
            continue

        min_line = min(line_values)
        max_line = max(line_values)
        spread_points = max_line - min_line
        median_line = float(median(line_values))
        median_price = _median_int(price_values) if price_values else None

        is_stable = spread_points <= max_book_spread_points
        instability_reason = None
        if not is_stable:
            instability_reason = (
                f"Book disagreement too wide: spread={spread_points:.2f}, "
                f"max_allowed={max_book_spread_points:.2f}"
            )

        results.append(
            ConsensusResult(
                market_type=market_type,
                side=side,
                book_count=len(line_values),
                consensus_line=median_line if is_stable else None,
                consensus_price_american=median_price,
                median_line=median_line,
                median_price_american=median_price,
                min_line=min_line,
                max_line=max_line,
                spread_points=spread_points,
                is_stable=is_stable,
                instability_reason=instability_reason,
                included_books=included_books,
            )
        )

    return results


def consensus_index(results: List[ConsensusResult]) -> Dict[Tuple[str, str], ConsensusResult]:
    return {(r.market_type, r.side): r for r in results}
