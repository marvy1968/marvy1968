from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from segment4b.common import (
    atomic_write_json,
    load_segment4b_config,
    parse_datetime,
    read_json,
    storage_path,
    utc_now,
    utc_now_iso,
)


class ExecutionDuplicateRegistry:
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

        self.path = storage_path(
            self.config,
            "duplicate_registry",
        )

        duplicate_config = self.config.get(
            "duplicate_protection",
            {},
        )

        self.duplicate_config = (
            duplicate_config
            if isinstance(
                duplicate_config,
                dict,
            )
            else {}
        )

    def _load(
        self,
    ) -> dict[str, Any]:
        payload = read_json(
            self.path,
            default={
                "updated_at": None,
                "signals": {},
                "authorizations": {},
                "order_intents": {},
                "broker_orders": {},
            },
        )

        if not isinstance(
            payload,
            dict,
        ):
            payload = {}

        for key in (
            "signals",
            "authorizations",
            "order_intents",
            "broker_orders",
        ):
            if not isinstance(
                payload.get(
                    key
                ),
                dict,
            ):
                payload[
                    key
                ] = {}

        return payload

    @staticmethod
    def fingerprint(
        *values: Any,
    ) -> str:
        material = "|".join(
            str(
                value
            )
            for value in values
        )

        return hashlib.sha256(
            material.encode(
                "utf-8"
            )
        ).hexdigest()[:24]

    def _ttl_minutes(
        self,
        category: str,
    ) -> float:
        mapping = {
            "signals": (
                "signal_ttl_minutes"
            ),
            "authorizations": (
                "authorization_ttl_minutes"
            ),
            "order_intents": (
                "order_intent_ttl_minutes"
            ),
            "broker_orders": (
                "broker_order_ttl_minutes"
            ),
        }

        return float(
            self.duplicate_config.get(
                mapping[
                    category
                ],
                120,
            )
        )

    def _purge(
        self,
        payload: dict[str, Any],
    ) -> None:
        now = utc_now()

        for category in (
            "signals",
            "authorizations",
            "order_intents",
            "broker_orders",
        ):
            records = payload.get(
                category,
                {},
            )

            if not isinstance(
                records,
                dict,
            ):
                continue

            ttl = timedelta(
                minutes=self._ttl_minutes(
                    category
                )
            )

            expired: list[str] = []

            for key, record in records.items():
                if not isinstance(
                    record,
                    dict,
                ):
                    expired.append(
                        key
                    )
                    continue

                timestamp = parse_datetime(
                    record.get(
                        "timestamp"
                    )
                )

                if timestamp is None:
                    expired.append(
                        key
                    )
                    continue

                if (
                    now
                    - timestamp
                ) > ttl:
                    expired.append(
                        key
                    )

            for key in expired:
                records.pop(
                    key,
                    None,
                )

    def exists(
        self,
        category: str,
        key: str,
    ) -> bool:
        payload = self._load()
        self._purge(
            payload
        )

        records = payload.get(
            category,
            {},
        )

        atomic_write_json(
            self.path,
            payload,
        )

        return (
            isinstance(
                records,
                dict,
            )
            and key in records
        )

    def register(
        self,
        category: str,
        key: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        payload = self._load()
        self._purge(
            payload
        )

        records = payload.setdefault(
            category,
            {},
        )

        if not isinstance(
            records,
            dict,
        ):
            records = {}
            payload[
                category
            ] = records

        if key in records:
            atomic_write_json(
                self.path,
                payload,
            )

            return False

        records[
            key
        ] = {
            "timestamp": utc_now_iso(),
            "metadata": (
                metadata
                if isinstance(
                    metadata,
                    dict,
                )
                else {}
            ),
        }

        payload[
            "updated_at"
        ] = utc_now_iso()

        atomic_write_json(
            self.path,
            payload,
        )

        return True

    def summary(
        self,
    ) -> dict[str, Any]:
        payload = self._load()
        self._purge(
            payload
        )

        atomic_write_json(
            self.path,
            payload,
        )

        return {
            "timestamp": utc_now_iso(),
            "enabled": bool(
                self.duplicate_config.get(
                    "enabled",
                    True,
                )
            ),
            "signals": len(
                payload.get(
                    "signals",
                    {},
                )
            ),
            "authorizations": len(
                payload.get(
                    "authorizations",
                    {},
                )
            ),
            "order_intents": len(
                payload.get(
                    "order_intents",
                    {},
                )
            ),
            "broker_orders": len(
                payload.get(
                    "broker_orders",
                    {},
                )
            ),
        }
