from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from core.common import utc_now
from core.models import CurrencyStrength


def split_pair(
    instrument: str,
) -> tuple[str, str]:
    components = instrument.split("_")

    if len(components) != 2:
        raise ValueError(
            f"Invalid pair: {instrument}"
        )

    return (
        components[0],
        components[1],
    )


class CurrencyStrengthEngine:
    def __init__(
        self,
        lookback_bars: int = 12,
    ) -> None:
        self.lookback_bars = max(
            2,
            int(lookback_bars),
        )

    def calculate(
        self,
        datasets: dict[
            str,
            dict[str, pd.DataFrame],
        ],
        timeframe: str = "H1",
    ) -> CurrencyStrength:
        totals: dict[
            str,
            float,
        ] = defaultdict(float)

        counts: dict[
            str,
            int,
        ] = defaultdict(int)

        pair_momentum: dict[
            str,
            float,
        ] = {}

        for instrument, frames in (
            datasets.items()
        ):
            frame = frames.get(timeframe)

            if (
                frame is None
                or len(frame)
                <= self.lookback_bars
            ):
                continue

            close = pd.to_numeric(
                frame["close"],
                errors="coerce",
            ).dropna()

            if (
                len(close)
                <= self.lookback_bars
            ):
                continue

            current = float(
                close.iloc[-1]
            )

            previous = float(
                close.iloc[
                    -1
                    - self.lookback_bars
                ]
            )

            if previous == 0:
                continue

            momentum = (
                current
                / previous
                - 1
            )

            pair_momentum[
                instrument
            ] = float(momentum)

            base, quote = split_pair(
                instrument
            )

            totals[base] += momentum
            totals[quote] -= momentum

            counts[base] += 1
            counts[quote] += 1

        values = {
            currency: (
                totals[currency]
                / counts[currency]
            )
            for currency
            in totals
            if counts[currency] > 0
        }

        if values:
            raw_values = np.array(
                list(values.values()),
                dtype=float,
            )

            mean = float(
                raw_values.mean()
            )

            standard_deviation = float(
                raw_values.std()
            )

            if standard_deviation > 0:
                values = {
                    currency: (
                        value
                        - mean
                    )
                    / standard_deviation
                    for currency, value
                    in values.items()
                }

        rankings = [
            currency
            for currency, _
            in sorted(
                values.items(),
                key=lambda item: item[1],
                reverse=True,
            )
        ]

        return CurrencyStrength(
            timestamp=utc_now().isoformat(),
            values=values,
            rankings=rankings,
            pair_momentum=pair_momentum,
        )
