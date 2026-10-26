from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from broker.oanda_v20 import OandaClient
from core.common import (
    ROOT,
    append_jsonl,
    safe_float,
    utc_now,
)
from core.indicators import normalize_frame
from core.models import PriceQuote
from core.storage import save_candles


def pip_size(
    instrument: str,
) -> float:
    if instrument.endswith("_JPY"):
        return 0.01

    return 0.0001


def parse_candle_payload(
    payload: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for candle in payload.get(
        "candles",
        [],
    ):
        if not candle.get(
            "complete",
            False,
        ):
            continue

        price = (
            candle.get("mid")
            or candle.get("bid")
            or candle.get("ask")
            or {}
        )

        rows.append(
            {
                "timestamp": candle.get(
                    "time"
                ),
                "open": safe_float(
                    price.get("o")
                ),
                "high": safe_float(
                    price.get("h")
                ),
                "low": safe_float(
                    price.get("l")
                ),
                "close": safe_float(
                    price.get("c")
                ),
                "volume": safe_float(
                    candle.get("volume")
                ),
            }
        )

    if not rows:
        raise ValueError(
            "OANDA returned no complete candles"
        )

    return normalize_frame(
        pd.DataFrame(rows)
    )


def parse_pricing_payload(
    payload: dict[str, Any],
) -> dict[str, PriceQuote]:
    quotes: dict[
        str,
        PriceQuote,
    ] = {}

    for item in payload.get(
        "prices",
        [],
    ):
        instrument = str(
            item.get(
                "instrument",
                "",
            )
        )

        if not instrument:
            continue

        bids = item.get(
            "bids",
            [],
        )

        asks = item.get(
            "asks",
            [],
        )

        if not bids or not asks:
            continue

        bid = safe_float(
            bids[0].get("price")
        )

        ask = safe_float(
            asks[0].get("price")
        )

        midpoint = (
            bid
            + ask
        ) / 2

        spread = (
            ask
            - bid
        )

        spread_pips = (
            spread
            / pip_size(instrument)
        )

        quotes[instrument] = (
            PriceQuote(
                instrument=instrument,
                timestamp=str(
                    item.get(
                        "time",
                        "",
                    )
                ),
                bid=bid,
                ask=ask,
                midpoint=midpoint,
                spread=spread,
                spread_pips=(
                    spread_pips
                ),
                tradeable=bool(
                    item.get(
                        "tradeable",
                        False,
                    )
                ),
            )
        )

    return quotes


class MarketDataEngine:
    def __init__(
        self,
        client: OandaClient,
        config: dict[str, Any],
    ) -> None:
        self.client = client
        self.config = config

        event_path = (
            config["logging"][
                "event_log"
            ]
        )

        self.event_log = (
            ROOT
            / event_path
        )

    def download_candles(
        self,
        instrument: str,
        timeframe: str,
    ) -> pd.DataFrame:
        oanda_config = self.config[
            "oanda"
        ]

        count = int(
            self.config[
                "candle_counts"
            ][timeframe]
        )

        payload = self.client.candles(
            instrument,
            granularity=timeframe,
            count=count,
            price=str(
                oanda_config.get(
                    "price_component",
                    "M",
                )
            ),
            daily_alignment=int(
                oanda_config.get(
                    "daily_alignment",
                    17,
                )
            ),
            alignment_timezone=str(
                oanda_config.get(
                    "alignment_timezone",
                    "America/New_York",
                )
            ),
        )

        frame = parse_candle_payload(
            payload
        )

        save_candles(
            instrument,
            timeframe,
            frame,
        )

        append_jsonl(
            self.event_log,
            {
                "timestamp": (
                    utc_now().isoformat()
                ),
                "event": (
                    "candles_refreshed"
                ),
                "instrument": instrument,
                "timeframe": timeframe,
                "bars": len(frame),
                "latest_bar": (
                    frame[
                        "timestamp"
                    ]
                    .max()
                    .isoformat()
                ),
            },
        )

        return frame

    def refresh_all(
        self,
    ) -> dict[
        str,
        dict[str, pd.DataFrame],
    ]:
        datasets: dict[
            str,
            dict[str, pd.DataFrame],
        ] = {}

        timeframes = list(
            dict.fromkeys(
                self.config[
                    "timeframes"
                ].values()
            )
        )

        for instrument in self.config[
            "pairs"
        ]:
            datasets[instrument] = {}

            for timeframe in timeframes:
                datasets[
                    instrument
                ][timeframe] = (
                    self.download_candles(
                        instrument,
                        timeframe,
                    )
                )

        return datasets

    def current_quotes(
        self,
    ) -> dict[str, PriceQuote]:
        payload = self.client.pricing(
            self.config["pairs"]
        )

        quotes = parse_pricing_payload(
            payload
        )

        append_jsonl(
            self.event_log,
            {
                "timestamp": (
                    utc_now().isoformat()
                ),
                "event": (
                    "pricing_refreshed"
                ),
                "quotes": len(quotes),
            },
        )

        return quotes

    def data_age_seconds(
        self,
        frame: pd.DataFrame,
    ) -> float:
        latest = pd.to_datetime(
            frame["timestamp"].max(),
            utc=True,
        )

        now = pd.Timestamp(
            datetime.now(
                latest.tz,
            )
        )

        return float(
            (
                now
                - latest
            ).total_seconds()
        )
