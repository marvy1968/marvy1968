from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from segment4.common import (
    atomic_write_json,
    read_json,
    utc_now,
    utc_now_iso,
)
from segment4.operations_common import (
    load_operations_config,
    operations_paths,
    stable_identifier,
)


class DuplicateRegistry:
    def __init__(
        self,
    ) -> None:
        self.config = (
            load_operations_config()
        )

        self.duplicate_config = dict(
            self.config.get(
                "duplicate_protection",
                {},
            )
        )

        self.path = operations_paths()[
            "duplicate_registry"
        ]

    def _load(
        self,
    ) -> dict[str, Any]:
        payload = read_json(
            self.path,
            default={},
        )

        if not isinstance(
            payload,
            dict,
        ):
            payload = {}

        payload.setdefault(
            "signals",
            {}
        )

        payload.setdefault(
            "authorizations",
            {}
        )

        payload.setdefault(
            "orders",
            {}
        )

        return payload

    @staticmethod
    def _timestamp_age_minutes(
        timestamp_text: str,
    ) -> float | None:
        try:
            timestamp = datetime.fromisoformat(
                timestamp_text
            )

        except ValueError:
            return None

        if timestamp.tzinfo is None:
            timestamp = timestamp.astimezone()

        return max(
            0.0,
            (
                utc_now()
                - timestamp
            ).total_seconds()
            / 60.0,
        )

    def _ttl(
        self,
        category: str,
    ) -> float:
        key = {
            "signals": (
                "signal_ttl_minutes"
            ),
            "authorizations": (
                "authorization_ttl_minutes"
            ),
            "orders": (
                "order_ttl_minutes"
            ),
        }[
            category
        ]

        return float(
            self.duplicate_config.get(
                key,
                120,
            )
        )

    def prune(
        self,
    ) -> dict[str, int]:
        payload = self._load()

        removed = {
            "signals": 0,
            "authorizations": 0,
            "orders": 0,
        }

        for category in removed:
            ttl = self._ttl(
                category
            )

            records = dict(
                payload.get(
                    category,
                    {},
                )
            )

            for identifier, record in list(
                records.items()
            ):
                timestamp = str(
                    record.get(
                        "timestamp",
                        "",
                    )
                )

                age = (
                    self._timestamp_age_minutes(
                        timestamp
                    )
                )

                if (
                    age is None
                    or age > ttl
                ):
                    records.pop(
                        identifier,
                        None,
                    )

                    removed[
                        category
                    ] += 1

            payload[
                category
            ] = records

        payload[
            "updated_at"
        ] = utc_now_iso()

        atomic_write_json(
            self.path,
            payload,
        )

        return removed

    def make_signal_id(
        self,
        *,
        instrument: str,
        side: str,
        setup: str,
        entry_price: float,
        source_timestamp: str,
    ) -> str:
        return stable_identifier(
            "signal",
            instrument,
            side,
            setup,
            round(
                float(
                    entry_price
                ),
                7,
            ),
            source_timestamp,
        )

    def make_authorization_id(
        self,
        *,
        instrument: str,
        side: str,
        signal_id: str,
        risk_units: int,
    ) -> str:
        return stable_identifier(
            "authorization",
            instrument,
            side,
            signal_id,
            risk_units,
        )

    def make_order_id(
        self,
        *,
        instrument: str,
        side: str,
        authorization_id: str,
        client_order_id: str,
    ) -> str:
        return stable_identifier(
            "order",
            instrument,
            side,
            authorization_id,
            client_order_id,
        )

    def seen(
        self,
        *,
        category: str,
        identifier: str,
    ) -> bool:
        if not bool(
            self.duplicate_config.get(
                "enabled",
                True,
            )
        ):
            return False

        if category not in {
            "signals",
            "authorizations",
            "orders",
        }:
            raise ValueError(
                f"Unknown duplicate category: {category}"
            )

        self.prune()

        payload = self._load()

        return identifier in payload[
            category
        ]

    def register(
        self,
        *,
        category: str,
        identifier: str,
        metadata: dict[str, Any],
    ) -> None:
        if category not in {
            "signals",
            "authorizations",
            "orders",
        }:
            raise ValueError(
                f"Unknown duplicate category: {category}"
            )

        payload = self._load()

        payload[
            category
        ][
            identifier
        ] = {
            "timestamp": utc_now_iso(),
            "metadata": metadata,
        }

        payload[
            "updated_at"
        ] = utc_now_iso()

        atomic_write_json(
            self.path,
            payload,
        )

    def summary(
        self,
    ) -> dict[str, Any]:
        self.prune()

        payload = self._load()

        return {
            "timestamp": utc_now_iso(),
            "enabled": bool(
                self.duplicate_config.get(
                    "enabled",
                    True,
                )
            ),
            "signals": len(
                payload[
                    "signals"
                ]
            ),
            "authorizations": len(
                payload[
                    "authorizations"
                ]
            ),
            "orders": len(
                payload[
                    "orders"
                ]
            ),
        }
