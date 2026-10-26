from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from segment4b.broker_adapter import (
    ReadOnlyOandaAdapter,
)
from segment4b.common import (
    load_segment4b_config,
    utc_now_iso,
)
from segment4b.models import (
    OrderIntent,
)


@dataclass
class MarketValidationResult:
    timestamp: str
    passed: bool
    instrument: str
    side: str
    bid: float
    ask: float
    mid: float
    spread_pips: float
    maximum_spread_pips: float
    intended_entry: float
    price_deviation_pips: float
    maximum_price_deviation_pips: float
    reasons: list[str]
    warnings: list[str]
    pricing_payload: dict[str, Any]
    automatic_execution: bool = False
    order_submission: bool = False

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


def pip_size(
    instrument: str,
) -> float:
    return (
        0.01
        if instrument.endswith(
            "_JPY"
        )
        else 0.0001
    )


class MarketConditionValidator:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        adapter: ReadOnlyOandaAdapter | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(
                config,
                dict,
            )
            else load_segment4b_config()
        )

        self.adapter = (
            adapter
            if adapter is not None
            else ReadOnlyOandaAdapter()
        )

    @staticmethod
    def _first_price(
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        prices = payload.get(
            "prices",
            [],
        )

        if not isinstance(
            prices,
            list,
        ) or not prices:
            raise ValueError(
                "OANDA pricing response contained no prices."
            )

        price = prices[0]

        if not isinstance(
            price,
            dict,
        ):
            raise ValueError(
                "OANDA pricing record is invalid."
            )

        return price

    @staticmethod
    def _best_price(
        price: dict[str, Any],
        key: str,
    ) -> float:
        records = price.get(
            key,
            [],
        )

        if not isinstance(
            records,
            list,
        ) or not records:
            raise ValueError(
                f"OANDA pricing record contains no {key}."
            )

        first = records[0]

        if not isinstance(
            first,
            dict,
        ):
            raise ValueError(
                f"OANDA {key} record is invalid."
            )

        return float(
            first.get(
                "price",
                0.0,
            )
        )

    def validate(
        self,
        intent: OrderIntent,
    ) -> MarketValidationResult:
        order_policy = self.config.get(
            "order_policy",
            {},
        )

        if not isinstance(
            order_policy,
            dict,
        ):
            order_policy = {}

        pricing_payload = self.adapter.pricing(
            [
                intent.instrument
            ]
        )

        price = self._first_price(
            pricing_payload
        )

        status = str(
            price.get(
                "status",
                "tradeable",
            )
        ).strip().lower()

        reasons: list[str] = []
        warnings: list[str] = []

        if status not in {
            "tradeable",
            "tradable",
        }:
            reasons.append(
                "instrument_not_tradeable"
            )

        bid = self._best_price(
            price,
            "bids",
        )

        ask = self._best_price(
            price,
            "asks",
        )

        if bid <= 0 or ask <= 0:
            reasons.append(
                "invalid_live_price"
            )

        if ask < bid:
            reasons.append(
                "inverted_live_market"
            )

        mid = (
            bid
            + ask
        ) / 2.0

        pair_pip = pip_size(
            intent.instrument
        )

        spread_pips = (
            ask
            - bid
        ) / pair_pip

        maximum_spread = float(
            order_policy.get(
                "maximum_spread_pips",
                3.0,
            )
        )

        if spread_pips > maximum_spread:
            reasons.append(
                "spread_above_maximum"
            )

        live_entry = (
            ask
            if intent.side == "buy"
            else bid
        )

        deviation_pips = abs(
            live_entry
            - intent.entry_price
        ) / pair_pip

        maximum_deviation = float(
            order_policy.get(
                (
                    "maximum_price_"
                    "deviation_pips"
                ),
                3.0,
            )
        )

        if deviation_pips > maximum_deviation:
            reasons.append(
                "price_deviation_above_maximum"
            )

        if spread_pips > (
            maximum_spread
            * 0.80
        ):
            warnings.append(
                "spread_near_maximum"
            )

        if deviation_pips > (
            maximum_deviation
            * 0.80
        ):
            warnings.append(
                "price_deviation_near_maximum"
            )

        return MarketValidationResult(
            timestamp=utc_now_iso(),
            passed=(
                len(
                    reasons
                )
                == 0
            ),
            instrument=(
                intent.instrument
            ),
            side=intent.side,
            bid=bid,
            ask=ask,
            mid=mid,
            spread_pips=spread_pips,
            maximum_spread_pips=(
                maximum_spread
            ),
            intended_entry=(
                intent.entry_price
            ),
            price_deviation_pips=(
                deviation_pips
            ),
            maximum_price_deviation_pips=(
                maximum_deviation
            ),
            reasons=reasons,
            warnings=warnings,
            pricing_payload=pricing_payload,
            automatic_execution=False,
            order_submission=False,
        )
