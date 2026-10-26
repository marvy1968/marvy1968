from __future__ import annotations

from datetime import timedelta
from typing import Any

from segment4b2.common import (
    atomic_write_json,
    parse_timestamp,
    read_json,
    storage_path,
    utc_now,
    utc_now_iso,
)


class ShadowDuplicateRegistry:
    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        self.config = config
        self.path = storage_path(
            config,
            "duplicate_registry",
        )

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

        records = payload.get(
            "records"
        )

        if not isinstance(
            records,
            dict,
        ):
            payload[
                "records"
            ] = {}

        return payload

    def prune(
        self,
    ) -> dict[str, Any]:
        payload = self._load()

        capture = self.config.get(
            "capture",
            {},
        )

        if not isinstance(
            capture,
            dict,
        ):
            capture = {}

        window_hours = float(
            capture.get(
                "duplicate_window_hours",
                12,
            )
        )

        cutoff = utc_now() - timedelta(
            hours=window_hours
        )

        records = payload.get(
            "records",
            {},
        )

        kept: dict[str, Any] = {}

        for key, record in records.items():
            if not isinstance(
                record,
                dict,
            ):
                continue

            created = parse_timestamp(
                record.get(
                    "timestamp"
                )
            )

            if (
                created is not None
                and created >= cutoff
            ):
                kept[
                    key
                ] = record

        payload[
            "records"
        ] = kept

        payload[
            "updated_at"
        ] = utc_now_iso()

        atomic_write_json(
            self.path,
            payload,
        )

        return payload

    def exists(
        self,
        candidate_id: str,
    ) -> bool:
        payload = self.prune()

        records = payload.get(
            "records",
            {},
        )

        return candidate_id in records

    def register(
        self,
        candidate_id: str,
        metadata: dict[str, Any],
    ) -> bool:
        payload = self.prune()

        records = payload.get(
            "records",
            {},
        )

        if candidate_id in records:
            return False

        records[
            candidate_id
        ] = {
            "timestamp": utc_now_iso(),
            **metadata,
        }

        payload[
            "records"
        ] = records

        payload[
            "updated_at"
        ] = utc_now_iso()

        atomic_write_json(
            self.path,
            payload,
        )

        return True
