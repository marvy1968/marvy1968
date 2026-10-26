from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.common import ROOT


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def append_jsonl(
    path: Path,
    payload: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(
            json.dumps(
                payload,
                default=str,
            )
            + "\n"
        )


class OrderRouter:
    def __init__(
        self,
        *,
        client: Any,
        config: dict[str, Any],
    ) -> None:
        self.client = client
        self.config = config
        self.segment3 = config["segment3"]
        self.execution = self.segment3[
            "execution"
        ]
        self.files = self.segment3["files"]

    def path(
        self,
        key: str,
    ) -> Path:
        return ROOT / self.files[key]

    @staticmethod
    def price_precision(
        instrument: str,
    ) -> int:
        return (
            3
            if instrument.endswith(
                "_JPY"
            )
            else 5
        )

    def order_payload(
        self,
        *,
        candidate: dict[str, Any],
        signed_units: int,
    ) -> dict[str, Any]:
        instrument = str(
            candidate["instrument"]
        )
        precision = self.price_precision(
            instrument
        )

        stop_price = round(
            float(
                candidate["stop_price"]
            ),
            precision,
        )

        target_price = round(
            float(
                candidate["target_price"]
            ),
            precision,
        )

        return {
            "order": {
                "instrument": instrument,
                "units": str(
                    int(signed_units)
                ),
                "type": "MARKET",
                "timeInForce": str(
                    self.execution.get(
                        "time_in_force",
                        "FOK",
                    )
                ),
                "positionFill": str(
                    self.execution.get(
                        "position_fill",
                        "DEFAULT",
                    )
                ),
                "stopLossOnFill": {
                    "price": (
                        f"{stop_price:.{precision}f}"
                    ),
                    "timeInForce": "GTC",
                },
                "takeProfitOnFill": {
                    "price": (
                        f"{target_price:.{precision}f}"
                    ),
                    "timeInForce": "GTC",
                },
            }
        }

    def locate_submit_method(
        self,
    ) -> Callable[..., Any]:
        candidates = [
            "create_order",
            "order_create",
            "place_order",
            "submit_order",
        ]

        for name in candidates:
            method = getattr(
                self.client,
                name,
                None,
            )

            if callable(method):
                return method

        raise RuntimeError(
            "OANDA client has no compatible "
            "order submission method."
        )

    def submit(
        self,
        *,
        candidate: dict[str, Any],
        signed_units: int,
        authorization_id: str,
    ) -> dict[str, Any]:
        payload = self.order_payload(
            candidate=candidate,
            signed_units=signed_units,
        )

        record = {
            "timestamp": utc_now_iso(),
            "authorization_id": (
                authorization_id
            ),
            "instrument": (
                candidate["instrument"]
            ),
            "side": candidate["side"],
            "signed_units": signed_units,
            "payload": payload,
            "environment": "practice",
        }

        method = self.locate_submit_method()

        try:
            try:
                response = method(
                    payload
                )
            except TypeError:
                response = method(
                    order=payload["order"]
                )

            record.update(
                {
                    "status": "submitted",
                    "response": response,
                }
            )

            append_jsonl(
                self.path("order_log"),
                record,
            )

            return record

        except Exception as exc:
            record.update(
                {
                    "status": "error",
                    "error": (
                        f"{type(exc).__name__}:"
                        f"{exc}"
                    ),
                }
            )

            append_jsonl(
                self.path("error_log"),
                record,
            )

            raise
