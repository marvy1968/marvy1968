from __future__ import annotations

from typing import Any

from segment4b3.common import (
    safe_float,
    safe_int,
    utc_now_iso,
)


class PromotionEngine:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        self.config = config

    def checkpoint(
        self,
        trades: int,
    ) -> tuple[str, int]:
        checkpoints = self.config.get(
            "checkpoints",
            {},
        )

        if not isinstance(
            checkpoints,
            dict,
        ):
            checkpoints = {}

        ordered = [
            (
                "production_review",
                safe_int(
                    checkpoints.get(
                        "production_review",
                        {},
                    ).get(
                        "minimum_trades",
                        250,
                    )
                    if isinstance(
                        checkpoints.get(
                            "production_review"
                        ),
                        dict,
                    )
                    else 250
                ),
            ),
            (
                "advanced",
                safe_int(
                    checkpoints.get(
                        "advanced",
                        {},
                    ).get(
                        "minimum_trades",
                        100,
                    )
                    if isinstance(
                        checkpoints.get(
                            "advanced"
                        ),
                        dict,
                    )
                    else 100
                ),
            ),
            (
                "provisional",
                safe_int(
                    checkpoints.get(
                        "provisional",
                        {},
                    ).get(
                        "minimum_trades",
                        50,
                    )
                    if isinstance(
                        checkpoints.get(
                            "provisional"
                        ),
                        dict,
                    )
                    else 50
                ),
            ),
            (
                "observation",
                safe_int(
                    checkpoints.get(
                        "observation",
                        {},
                    ).get(
                        "minimum_trades",
                        30,
                    )
                    if isinstance(
                        checkpoints.get(
                            "observation"
                        ),
                        dict,
                    )
                    else 30
                ),
            ),
        ]

        for name, minimum in ordered:
            if trades >= minimum:
                return (
                    name,
                    minimum,
                )

        minimum_observation = ordered[
            -1
        ][
            1
        ]

        return (
            "data_collection",
            minimum_observation,
        )

    def run(
        self,
        *,
        metrics: dict[str, Any],
        monte_carlo: dict[str, Any],
        walk_forward: dict[str, Any],
        stability: dict[str, Any],
    ) -> dict[str, Any]:
        minimums = self.config.get(
            "minimum_requirements",
            {},
        )

        if not isinstance(
            minimums,
            dict,
        ):
            minimums = {}

        trades = safe_int(
            metrics.get(
                "trades",
                0,
            )
        )

        checkpoint, minimum_trades = (
            self.checkpoint(
                trades
            )
        )

        reasons: list[str] = []

        if trades < minimum_trades:
            reasons.append(
                "insufficient_trades_for_observation_checkpoint"
            )

        if safe_float(
            metrics.get(
                "win_rate",
                0.0,
            )
        ) < safe_float(
            minimums.get(
                "minimum_win_rate",
                0.48,
            )
        ):
            reasons.append(
                "win_rate_below_minimum"
            )

        if safe_float(
            metrics.get(
                "profit_factor",
                0.0,
            )
        ) < safe_float(
            minimums.get(
                "minimum_profit_factor",
                1.20,
            )
        ):
            reasons.append(
                "profit_factor_below_minimum"
            )

        if safe_float(
            metrics.get(
                "expectancy_r",
                0.0,
            )
        ) < safe_float(
            minimums.get(
                "minimum_expectancy_r",
                0.10,
            )
        ):
            reasons.append(
                "expectancy_below_minimum"
            )

        if safe_float(
            metrics.get(
                "net_r",
                0.0,
            )
        ) < safe_float(
            minimums.get(
                "minimum_net_r",
                5.0,
            )
        ):
            reasons.append(
                "net_r_below_minimum"
            )

        if safe_float(
            metrics.get(
                "maximum_drawdown_r",
                0.0,
            )
        ) > safe_float(
            minimums.get(
                "maximum_drawdown_r",
                10.0,
            )
        ):
            reasons.append(
                "drawdown_above_maximum"
            )

        if safe_int(
            metrics.get(
                "longest_loss_streak",
                0,
            )
        ) > safe_int(
            minimums.get(
                "maximum_loss_streak",
                5,
            )
        ):
            reasons.append(
                "loss_streak_above_maximum"
            )

        if not bool(
            monte_carlo.get(
                "passed",
                False,
            )
        ):
            reasons.extend(
                monte_carlo.get(
                    "reasons",
                    [
                        "monte_carlo_not_passed"
                    ],
                )
            )

        if (
            walk_forward.get(
                "eligible",
                False,
            )
            and not walk_forward.get(
                "passed",
                False,
            )
        ):
            reasons.extend(
                walk_forward.get(
                    "reasons",
                    [
                        "walk_forward_not_passed"
                    ],
                )
            )

        elif not walk_forward.get(
            "eligible",
            False,
        ):
            reasons.append(
                "walk_forward_not_yet_eligible"
            )

        if stability.get(
            "status"
        ) == "critical":
            reasons.extend(
                stability.get(
                    "critical_reasons",
                    [
                        "critical_degradation"
                    ],
                )
            )

        reasons = sorted(
            set(
                reasons
            )
        )

        passed = (
            len(
                reasons
            )
            == 0
        )

        if passed:
            readiness = (
                "eligible_for_manual_review"
            )

        elif trades < minimum_trades:
            readiness = (
                "collecting_shadow_data"
            )

        elif stability.get(
            "status"
        ) == "critical":
            readiness = (
                "critical_degradation_hold"
            )

        else:
            readiness = (
                "research_hold"
            )

        return {
            "timestamp": utc_now_iso(),
            "checkpoint": checkpoint,
            "checkpoint_minimum_trades": (
                minimum_trades
            ),
            "current_trades": trades,
            "readiness": readiness,
            "passed": passed,
            "eligible_for_manual_review": (
                passed
            ),
            "reasons": reasons,
            "metrics": metrics,
            "monte_carlo_passed": bool(
                monte_carlo.get(
                    "passed",
                    False,
                )
            ),
            "walk_forward_eligible": bool(
                walk_forward.get(
                    "eligible",
                    False,
                )
            ),
            "walk_forward_passed": bool(
                walk_forward.get(
                    "passed",
                    False,
                )
            ),
            "stability_status": (
                stability.get(
                    "status",
                    "unknown",
                )
            ),
            "automatically_promoted": False,
            "automatic_execution": False,
            "order_submission": False,
            "message": (
                "Eligibility never enables trading automatically."
            ),
        }
