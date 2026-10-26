from __future__ import annotations

from typing import Any

from segment4b.broker_adapter import (
    ReadOnlyOandaAdapter,
)
from segment4b2.common import (
    safe_float,
    utc_now_iso,
)


class ShadowPricingReader:
    def __init__(
        self,
    ) -> None:
        self.adapter = (
            ReadOnlyOandaAdapter()
        )

    @staticmethod
    def _price(
        records: Any,
    ) -> float:
        if not isinstance(
            records,
            list,
        ) or not records:
            return 0.0

        first = records[0]

        if not isinstance(
            first,
            dict,
        ):
            return 0.0

        return safe_float(
            first.get(
                "price",
                0.0,
            )
        )

    def quotes(
        self,
        instruments: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not instruments:
            return {}

        payload = self.adapter.pricing(
            instruments
        )

        prices = payload.get(
            "prices",
            [],
        )

        if not isinstance(
            prices,
            list,
        ):
            prices = []

        result: dict[
            str,
            dict[str, Any],
        ] = {}

        for record in prices:
            if not isinstance(
                record,
                dict,
            ):
                continue

            instrument = str(
                record.get(
                    "instrument",
                    "",
                )
            ).strip().upper()

            if not instrument:
                continue

            bid = self._price(
                record.get(
                    "bids"
                )
            )

            ask = self._price(
                record.get(
                    "asks"
                )
            )

            mid = (
                bid
                + ask
            ) / 2.0

            result[
                instrument
            ] = {
                "timestamp": str(
                    record.get(
                        "time",
                        utc_now_iso(),
                    )
                ),
                "instrument": instrument,
                "status": record.get(
                    "status",
                    "",
                ),
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "raw": record,
            }

        return result
