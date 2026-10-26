from __future__ import annotations

from typing import Any

from segment4b2.common import (
    parse_timestamp,
    safe_float,
    utc_now,
    utc_now_iso,
)


class ShadowSettlementEngine:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        self.config = config

        monitoring = config.get(
            "monitoring",
            {},
        )

        self.monitoring = (
            monitoring
            if isinstance(
                monitoring,
                dict,
            )
            else {}
        )

    @staticmethod
    def _unrealized_r(
        *,
        side: str,
        entry: float,
        current: float,
        risk_distance: float,
    ) -> float:
        if risk_distance <= 0:
            return 0.0

        movement = (
            current
            - entry
        )

        if side == "sell":
            movement = -movement

        return (
            movement
            / risk_distance
        )

    def evaluate(
        self,
        trade: dict[str, Any],
        quote: dict[str, Any],
    ) -> tuple[
        dict[str, Any],
        bool,
    ]:
        updated = dict(
            trade
        )

        side = str(
            updated.get(
                "side",
                "",
            )
        ).lower()

        entry = safe_float(
            updated.get(
                "entry_price"
            )
        )

        stop = safe_float(
            updated.get(
                "stop_price"
            )
        )

        target = safe_float(
            updated.get(
                "target_price"
            )
        )

        risk_distance = safe_float(
            updated.get(
                "initial_risk_distance"
            )
        )

        bid = safe_float(
            quote.get(
                "bid"
            )
        )

        ask = safe_float(
            quote.get(
                "ask"
            )
        )

        current = (
            bid
            if side == "buy"
            else ask
        )

        if current <= 0:
            return (
                updated,
                False,
            )

        updated[
            "current_price"
        ] = current

        updated[
            "last_checked_at"
        ] = utc_now_iso()

        updated[
            "bars_held"
        ] = int(
            updated.get(
                "bars_held",
                0,
            )
        ) + 1

        highest = max(
            safe_float(
                updated.get(
                    "highest_price",
                    entry,
                )
            ),
            current,
        )

        lowest = min(
            safe_float(
                updated.get(
                    "lowest_price",
                    entry,
                )
            ),
            current,
        )

        updated[
            "highest_price"
        ] = highest

        updated[
            "lowest_price"
        ] = lowest

        unrealized_r = self._unrealized_r(
            side=side,
            entry=entry,
            current=current,
            risk_distance=(
                risk_distance
            ),
        )

        updated[
            "unrealized_r"
        ] = unrealized_r

        maximum_favorable = safe_float(
            updated.get(
                (
                    "maximum_favorable_"
                    "excursion_r"
                ),
                0.0,
            )
        )

        maximum_adverse = safe_float(
            updated.get(
                (
                    "maximum_adverse_"
                    "excursion_r"
                ),
                0.0,
            )
        )

        if side == "buy":
            favorable = (
                highest
                - entry
            ) / risk_distance

            adverse = (
                lowest
                - entry
            ) / risk_distance

        else:
            favorable = (
                entry
                - lowest
            ) / risk_distance

            adverse = (
                entry
                - highest
            ) / risk_distance

        updated[
            (
                "maximum_favorable_"
                "excursion_r"
            )
        ] = max(
            maximum_favorable,
            favorable,
        )

        updated[
            (
                "maximum_adverse_"
                "excursion_r"
            )
        ] = min(
            maximum_adverse,
            adverse,
        )

        stop_hit = False
        target_hit = False

        if side == "buy":
            stop_hit = (
                current <= stop
            )

            target_hit = (
                current >= target
            )

        elif side == "sell":
            stop_hit = (
                current >= stop
            )

            target_hit = (
                current <= target
            )

        exit_reason = ""
        close_price = 0.0
        realized_r = 0.0
        outcome = ""

        if (
            stop_hit
            and bool(
                self.monitoring.get(
                    "close_on_stop",
                    True,
                )
            )
        ):
            exit_reason = "stop"
            close_price = stop
            realized_r = -1.0
            outcome = "loss"

        elif (
            target_hit
            and bool(
                self.monitoring.get(
                    "close_on_target",
                    True,
                )
            )
        ):
            exit_reason = "target"
            close_price = target
            realized_r = safe_float(
                updated.get(
                    "reward_risk",
                    0.0,
                )
            )
            outcome = "win"

        opened_at = parse_timestamp(
            updated.get(
                "opened_at"
            )
        )

        if (
            not exit_reason
            and opened_at is not None
        ):
            age_hours = (
                utc_now()
                - opened_at
            ).total_seconds() / 3600.0

            maximum_hours = safe_float(
                self.monitoring.get(
                    "maximum_trade_age_hours",
                    72,
                )
            )

            if age_hours >= maximum_hours:
                exit_reason = "maximum_age"
                close_price = current
                realized_r = unrealized_r

                if realized_r > 0:
                    outcome = "win"

                elif realized_r < 0:
                    outcome = "loss"

                else:
                    outcome = "push"

        if not exit_reason:
            return (
                updated,
                False,
            )

        updated[
            "status"
        ] = "closed"

        updated[
            "close_price"
        ] = close_price

        updated[
            "closed_at"
        ] = utc_now_iso()

        updated[
            "outcome"
        ] = outcome

        updated[
            "exit_reason"
        ] = exit_reason

        updated[
            "realized_r"
        ] = realized_r

        updated[
            "unrealized_r"
        ] = 0.0

        return (
            updated,
            True,
        )
