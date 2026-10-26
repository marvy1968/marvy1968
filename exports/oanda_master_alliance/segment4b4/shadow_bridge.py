from __future__ import annotations

from pathlib import Path
from typing import Any

from segment4b4.common import (
    ROOT,
    read_json,
)


class ShadowStateBridge:
    def __init__(self) -> None:
        self.status_candidates = [
            ROOT
            / "state/shadow/"
            "shadow_status.json",
            ROOT
            / "state/shadow/"
            "latest_cycle.json",
            ROOT
            / "state/shadow/"
            "segment4b2_status.json",
        ]

        self.open_candidates = [
            ROOT
            / "state/shadow/"
            "open_trades.json",
            ROOT
            / "state/shadow/"
            "shadow_open_trades.json",
        ]

        self.closed_candidates = [
            ROOT
            / "state/shadow/"
            "closed_trades.json",
            ROOT
            / "state/shadow/"
            "shadow_closed_trades.json",
        ]

    @staticmethod
    def _first_existing(
        candidates: list[Path],
    ) -> Path | None:
        for path in candidates:
            if path.exists():
                return path

        return None

    def status(self) -> dict[str, Any]:
        path = self._first_existing(
            self.status_candidates
        )

        if path is None:
            return {}

        payload = read_json(
            path,
            default={},
        )

        if not isinstance(
            payload,
            dict,
        ):
            return {}

        return payload

    def _load_trade_records(
        self,
        candidates: list[Path],
        keys: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        path = self._first_existing(
            candidates
        )

        if path is None:
            return []

        payload = read_json(
            path,
            default=[],
        )

        if isinstance(payload, list):
            return [
                item
                for item in payload
                if isinstance(
                    item,
                    dict,
                )
            ]

        if isinstance(payload, dict):
            for key in keys:
                records = payload.get(
                    key
                )

                if isinstance(
                    records,
                    list,
                ):
                    return [
                        item
                        for item in records
                        if isinstance(
                            item,
                            dict,
                        )
                    ]

        return []

    def open_trades(
        self,
    ) -> list[dict[str, Any]]:
        return self._load_trade_records(
            self.open_candidates,
            (
                "trades",
                "open_trades",
                "records",
            ),
        )

    def closed_trades(
        self,
    ) -> list[dict[str, Any]]:
        return self._load_trade_records(
            self.closed_candidates,
            (
                "trades",
                "closed_trades",
                "records",
            ),
        )

    @staticmethod
    def _safe_count(
        value: Any,
        fallback: int,
    ) -> int:
        if isinstance(value, bool):
            return fallback

        if isinstance(value, list):
            return len(value)

        try:
            return max(
                0,
                int(value),
            )
        except (
            TypeError,
            ValueError,
        ):
            return fallback

    def summary(self) -> dict[str, Any]:
        status = self.status()
        open_records = self.open_trades()
        closed_records = self.closed_trades()

        open_value = status.get(
            "open_trade_count",
            status.get(
                "open_trades",
                len(open_records),
            ),
        )

        closed_value = status.get(
            "closed_trade_count",
            status.get(
                "closed_trades",
                len(closed_records),
            ),
        )

        open_count = self._safe_count(
            open_value,
            len(open_records),
        )

        closed_count = self._safe_count(
            closed_value,
            len(closed_records),
        )

        return {
            "status_present": bool(status),
            "open_trade_count": open_count,
            "closed_trade_count": (
                closed_count
            ),
            "open_records_found": len(
                open_records
            ),
            "closed_records_found": len(
                closed_records
            ),
            "automatic_execution": False,
            "order_submission": False,
        }
