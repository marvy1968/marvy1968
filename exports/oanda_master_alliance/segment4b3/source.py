from __future__ import annotations

from typing import Any

from segment4b3.common import (
    load_config,
    read_json,
    resolve_path,
)


class ShadowResultSource:
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
            else load_config()
        )

    def _source_path(
        self,
        key: str,
    ):
        source = self.config.get(
            "source",
            {},
        )

        if not isinstance(
            source,
            dict,
        ):
            source = {}

        value = source.get(
            key
        )

        if not value:
            raise KeyError(
                f"Missing Segment 4B-3 source path: {key}"
            )

        return resolve_path(
            str(
                value
            )
        )

    def closed_trades(
        self,
    ) -> list[dict[str, Any]]:
        payload = read_json(
            self._source_path(
                "closed_shadow_trades"
            ),
            default=[],
        )

        if not isinstance(
            payload,
            list,
        ):
            return []

        records = [
            record
            for record in payload
            if isinstance(
                record,
                dict,
            )
            and record.get(
                "status"
            ) == "closed"
        ]

        return sorted(
            records,
            key=lambda record: str(
                record.get(
                    "closed_at",
                    record.get(
                        "opened_at",
                        "",
                    ),
                )
            ),
        )

    def open_trades(
        self,
    ) -> list[dict[str, Any]]:
        payload = read_json(
            self._source_path(
                "open_shadow_trades"
            ),
            default=[],
        )

        if not isinstance(
            payload,
            list,
        ):
            return []

        return [
            record
            for record in payload
            if isinstance(
                record,
                dict,
            )
        ]
