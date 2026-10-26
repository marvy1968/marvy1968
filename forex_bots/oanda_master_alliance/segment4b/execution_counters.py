from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from segment4b.common import (
    atomic_write_json,
    load_segment4b_config,
    read_json,
    storage_path,
    utc_now_iso,
)


class ExecutionCounterManager:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(
                config,
                dict,
            )
            else load_segment4b_config()
        )

        safety = self.config.get(
            "safety",
            {},
        )

        self.safety = (
            safety
            if isinstance(
                safety,
                dict,
            )
            else {}
        )

        self.hourly_path = storage_path(
            self.config,
            "hourly_counter",
        )

        self.daily_path = storage_path(
            self.config,
            "daily_counter",
        )

    @staticmethod
    def _hour_key() -> str:
        return datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%dT%H"
        )

    @staticmethod
    def _day_key() -> str:
        return datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d"
        )

    def _load_counter(
        self,
        path,
        key_name: str,
        current_key: str,
    ) -> dict[str, Any]:
        payload = read_json(
            path,
            default={},
        )

        if not isinstance(
            payload,
            dict,
        ):
            payload = {}

        if payload.get(
            key_name
        ) != current_key:
            payload = {
                key_name: current_key,
                "count": 0,
                "records": [],
                "updated_at": (
                    utc_now_iso()
                ),
            }

        if not isinstance(
            payload.get(
                "records"
            ),
            list,
        ):
            payload[
                "records"
            ] = []

        try:
            payload[
                "count"
            ] = int(
                payload.get(
                    "count",
                    0,
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            payload[
                "count"
            ] = 0

        return payload

    def snapshot(
        self,
    ) -> dict[str, Any]:
        hourly = self._load_counter(
            self.hourly_path,
            "hour",
            self._hour_key(),
        )

        daily = self._load_counter(
            self.daily_path,
            "day",
            self._day_key(),
        )

        atomic_write_json(
            self.hourly_path,
            hourly,
        )

        atomic_write_json(
            self.daily_path,
            daily,
        )

        return {
            "timestamp": utc_now_iso(),
            "hourly_count": int(
                hourly.get(
                    "count",
                    0,
                )
            ),
            "daily_count": int(
                daily.get(
                    "count",
                    0,
                )
            ),
            "maximum_orders_per_hour": int(
                self.safety.get(
                    "maximum_orders_per_hour",
                    4,
                )
            ),
            "maximum_orders_per_day": int(
                self.safety.get(
                    "maximum_orders_per_day",
                    8,
                )
            ),
            "automatic_execution": False,
            "order_submission": False,
        }

    def evaluate(
        self,
        *,
        cycle_count: int,
    ) -> dict[str, Any]:
        snapshot = self.snapshot()

        reasons: list[str] = []

        maximum_cycle = int(
            self.safety.get(
                "maximum_orders_per_cycle",
                2,
            )
        )

        if cycle_count >= maximum_cycle:
            reasons.append(
                "cycle_order_limit_reached"
            )

        if (
            snapshot[
                "hourly_count"
            ]
            >= snapshot[
                "maximum_orders_per_hour"
            ]
        ):
            reasons.append(
                "hourly_order_limit_reached"
            )

        if (
            snapshot[
                "daily_count"
            ]
            >= snapshot[
                "maximum_orders_per_day"
            ]
        ):
            reasons.append(
                "daily_order_limit_reached"
            )

        return {
            **snapshot,
            "passed": (
                len(
                    reasons
                )
                == 0
            ),
            "cycle_count": cycle_count,
            "maximum_orders_per_cycle": (
                maximum_cycle
            ),
            "reasons": reasons,
        }

    def record_simulation(
        self,
        *,
        intent_id: str,
        instrument: str,
        side: str,
    ) -> dict[str, Any]:
        hourly = self._load_counter(
            self.hourly_path,
            "hour",
            self._hour_key(),
        )

        daily = self._load_counter(
            self.daily_path,
            "day",
            self._day_key(),
        )

        record = {
            "timestamp": utc_now_iso(),
            "intent_id": intent_id,
            "instrument": instrument,
            "side": side,
            "mode": "simulation_only",
            "submitted": False,
        }

        hourly[
            "count"
        ] = int(
            hourly.get(
                "count",
                0,
            )
        ) + 1

        daily[
            "count"
        ] = int(
            daily.get(
                "count",
                0,
            )
        ) + 1

        hourly[
            "records"
        ].append(
            record
        )

        daily[
            "records"
        ].append(
            record
        )

        hourly[
            "updated_at"
        ] = utc_now_iso()

        daily[
            "updated_at"
        ] = utc_now_iso()

        atomic_write_json(
            self.hourly_path,
            hourly,
        )

        atomic_write_json(
            self.daily_path,
            daily,
        )

        return record
