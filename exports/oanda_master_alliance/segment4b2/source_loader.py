from __future__ import annotations

from pathlib import Path
from typing import Any

from core.common import ROOT
from segment4b2.common import (
    load_config,
    read_json,
    resolve_path,
)


class CandidateSourceLoader:
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

    def _source_paths(
        self,
    ) -> list[Path]:
        capture = self.config.get(
            "capture",
            {},
        )

        if not isinstance(
            capture,
            dict,
        ):
            capture = {}

        paths = [
            resolve_path(
                capture.get(
                    "source_status_file",
                    (
                        "state/execution/"
                        "segment3_status.json"
                    ),
                )
            )
        ]

        if bool(
            capture.get(
                (
                    "allow_segment4b1_"
                    "status_fallback"
                ),
                True,
            )
        ):
            paths.append(
                resolve_path(
                    capture.get(
                        (
                            "segment4b1_"
                            "status_file"
                        ),
                        (
                            "state/execution4b/"
                            "execution_status.json"
                        ),
                    )
                )
            )

        return paths

    @staticmethod
    def _candidate_records(
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        results = payload.get(
            "results"
        )

        if isinstance(
            results,
            list,
        ):
            return [
                record
                for record in results
                if isinstance(
                    record,
                    dict,
                )
            ]

        evaluations = payload.get(
            "evaluations"
        )

        if isinstance(
            evaluations,
            list,
        ):
            records: list[
                dict[str, Any]
            ] = []

            for item in evaluations:
                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                candidate = item.get(
                    "candidate"
                )

                if isinstance(
                    candidate,
                    dict,
                ):
                    records.append(
                        candidate
                    )
                    continue

                intent = item.get(
                    "intent"
                )

                if isinstance(
                    intent,
                    dict,
                ):
                    records.append(
                        {
                            **intent,
                            "risk": {
                                "approved": True,
                                "risk_pct": (
                                    intent.get(
                                        "metadata",
                                        {},
                                    ).get(
                                        "risk_pct",
                                        0.0,
                                    )
                                    if isinstance(
                                        intent.get(
                                            "metadata"
                                        ),
                                        dict,
                                    )
                                    else 0.0
                                ),
                                "position_size": {
                                    "units": intent.get(
                                        "units",
                                        0,
                                    ),
                                    "entry_price": intent.get(
                                        "entry_price",
                                        0.0,
                                    ),
                                    "stop_price": intent.get(
                                        "stop_price",
                                        0.0,
                                    ),
                                },
                            },
                            "authorization": {
                                "authorized": True,
                                "authorization_id": (
                                    intent.get(
                                        "authorization_id",
                                        "",
                                    )
                                ),
                                "timestamp": intent.get(
                                    "source_timestamp",
                                    intent.get(
                                        "timestamp",
                                        "",
                                    ),
                                ),
                            },
                        }
                    )

            return records

        shortlist = payload.get(
            "shortlist"
        )

        if isinstance(
            shortlist,
            list,
        ):
            return [
                record
                for record in shortlist
                if isinstance(
                    record,
                    dict,
                )
            ]

        return []

    def load(
        self,
    ) -> tuple[
        list[dict[str, Any]],
        Path | None,
    ]:
        for path in self._source_paths():
            payload = read_json(
                path,
                default={},
            )

            if not isinstance(
                payload,
                dict,
            ):
                continue

            records = self._candidate_records(
                payload
            )

            if records:
                return (
                    records,
                    path,
                )

        return (
            [],
            None,
        )
