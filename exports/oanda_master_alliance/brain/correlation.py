from __future__ import annotations

from collections import Counter

from signals.context import (
    split_instrument,
)
from signals.models import (
    AllianceDecision,
)


def signed_currency_exposure(
    decision: AllianceDecision,
) -> dict[str, int]:
    base, quote = split_instrument(
        decision.instrument
    )

    if decision.side == "buy":
        return {
            base: 1,
            quote: -1,
        }

    if decision.side == "sell":
        return {
            base: -1,
            quote: 1,
        }

    return {}


def filter_correlated_decisions(
    decisions: list[AllianceDecision],
    maximum_currency_exposure: int,
    maximum_positions: int,
) -> list[AllianceDecision]:
    selected: list[
        AllianceDecision
    ] = []

    exposure: Counter = Counter()

    ordered = sorted(
        decisions,
        key=lambda item: (
            item.alliance_score,
            item.confirming_experts,
            item.reward_risk,
        ),
        reverse=True,
    )

    for decision in ordered:
        if len(selected) >= maximum_positions:
            break

        proposed = (
            signed_currency_exposure(
                decision
            )
        )

        blocked = False

        for currency, amount in (
            proposed.items()
        ):
            future = (
                exposure[currency]
                + amount
            )

            if abs(
                future
            ) > maximum_currency_exposure:
                blocked = True
                break

        if blocked:
            decision.decision = (
                "blocked_correlation"
            )

            decision.rejection_reasons.append(
                "currency_exposure_limit"
            )

            continue

        selected.append(
            decision
        )

        exposure.update(
            proposed
        )

    return selected
