from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EdgeResult:
    fair_prob: float
    offered_american: int
    decimal_odds: float
    expected_value: float
    edge_pct: float
    raw_kelly_fraction: float
    kelly_fraction: float
    recommended_stake_fraction: float
    recommended_stake_amount: float
    is_bet_recommended: bool
    reason: str


def american_to_decimal(odds: int) -> float:
    if odds == 0:
        raise ValueError("American odds cannot be zero.")
    if odds > 0:
        return (odds / 100.0) + 1.0
    return (100.0 / abs(odds)) + 1.0


def calculate_ev_and_kelly(
    fair_prob: float,
    offered_american: int,
    bankroll: float = 1.0,
    edge_floor: float = 0.02,
    kelly_multiplier: float = 0.25,
    max_kelly: float = 0.08,
) -> EdgeResult:
    fair_prob = max(0.0, min(float(fair_prob), 1.0))
    decimal_odds = american_to_decimal(offered_american)

    expected_value = (fair_prob * decimal_odds) - 1.0
    edge_pct = expected_value

    b = decimal_odds - 1.0
    q = 1.0 - fair_prob

    raw_kelly = ((b * fair_prob) - q) / b if b > 0 else 0.0
    adjusted_kelly = raw_kelly * kelly_multiplier
    final_kelly = max(0.0, min(adjusted_kelly, max_kelly))
    stake_amount = bankroll * final_kelly

    if fair_prob <= 0.0:
        is_rec = False
        reason = "Fair probability is zero after validation."
    elif expected_value < edge_floor:
        is_rec = False
        reason = f"EV ({expected_value:.2%}) is below floor ({edge_floor:.2%})."
    elif final_kelly <= 0.0:
        is_rec = False
        reason = "Zero or negative Kelly fraction; no positive edge."
    else:
        is_rec = True
        reason = (
            f"Recommended bet | EV={expected_value:.2%} | "
            f"Kelly={final_kelly:.2%} | Stake={stake_amount:.4f}"
        )

    return EdgeResult(
        fair_prob=fair_prob,
        offered_american=offered_american,
        decimal_odds=decimal_odds,
        expected_value=expected_value,
        edge_pct=edge_pct,
        raw_kelly_fraction=raw_kelly,
        kelly_fraction=final_kelly,
        recommended_stake_fraction=final_kelly,
        recommended_stake_amount=stake_amount,
        is_bet_recommended=is_rec,
        reason=reason,
    )
