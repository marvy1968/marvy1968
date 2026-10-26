from __future__ import annotations

from typing import Any

from segment4b.common import (
    append_jsonl,
    atomic_write_json,
    load_segment4b_config,
    storage_path,
    utc_now_iso,
)


class RejectionRecorder:
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

    def record(
        self,
        *,
        stage: str,
        instrument: str,
        side: str,
        reasons: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "timestamp": utc_now_iso(),
            "stage": stage,
            "instrument": instrument,
            "side": side,
            "reasons": reasons,
            "metadata": (
                metadata
                if isinstance(
                    metadata,
                    dict,
                )
                else {}
            ),
            "automatic_execution": False,
            "order_submission": False,
        }

        rejection_id = (
            payload[
                "timestamp"
            ]
            .replace(
                ":",
                ""
            )
            .replace(
                "+",
                "_"
            )
            + "_"
            + instrument
            + "_"
            + stage
        )

        rejection_path = (
            storage_path(
                self.config,
                "execution_status",
            ).parent
            / "rejections"
            / (
                f"{rejection_id}.json"
            )
        )

        atomic_write_json(
            rejection_path,
            payload,
        )

        append_jsonl(
            storage_path(
                self.config,
                "rejection_history",
            ),
            payload,
        )

        return payload
