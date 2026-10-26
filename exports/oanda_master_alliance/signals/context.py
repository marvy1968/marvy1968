from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from core.common import ROOT


MARKET_FILE = ROOT / "state" / "market_snapshot.json"
REGIME_FILE = ROOT / "state" / "regime_snapshot.json"
STRENGTH_FILE = ROOT / "state" / "currency_strength.json"
CANDLE_DIRECTORY = ROOT / "state" / "candles"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(
        value.replace(
            "Z",
            "+00:00",
        )
    )

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(
        timezone.utc
    )


def normalize_side(direction: str) -> str:
    value = str(direction).lower().strip()

    if value == "up":
        return "buy"

    if value == "down":
        return "sell"

    return "flat"


def split_instrument(
    instrument: str,
) -> tuple[str, str]:
    base, quote = instrument.split(
        "_",
        1,
    )

    return base, quote


@dataclass
class MarketContext:
    instrument: str
    session: str
    active_sessions: list[str]
    regime: str
    direction: str
    confidence: float
    confirmations: int
    timeframe_results: list[dict[str, Any]]
    strength_values: dict[str, float]
    pair_momentum: float
    candles: dict[str, pd.DataFrame]
    snapshot_timestamp: datetime

    @property
    def side(self) -> str:
        return normalize_side(
            self.direction
        )

    @property
    def base_currency(self) -> str:
        return split_instrument(
            self.instrument
        )[0]

    @property
    def quote_currency(self) -> str:
        return split_instrument(
            self.instrument
        )[1]

    @property
    def strength_difference(self) -> float:
        return float(
            self.strength_values.get(
                self.base_currency,
                0.0,
            )
            - self.strength_values.get(
                self.quote_currency,
                0.0,
            )
        )

    def timeframe(
        self,
        name: str,
    ) -> dict[str, Any]:
        for result in self.timeframe_results:
            if result.get("timeframe") == name:
                return result

        return {}

    def frame(
        self,
        timeframe: str,
    ) -> pd.DataFrame:
        return self.candles.get(
            timeframe,
            pd.DataFrame(),
        )

    def latest_price(self) -> float:
        for timeframe in [
            "M5",
            "M15",
            "H1",
            "H4",
        ]:
            result = self.timeframe(
                timeframe
            )

            if result.get("close") is not None:
                return float(
                    result["close"]
                )

        raise ValueError(
            f"No latest price for {self.instrument}"
        )

    def latest_atr(self) -> float:
        for timeframe in [
            "M15",
            "M5",
            "H1",
        ]:
            result = self.timeframe(
                timeframe
            )

            if result.get("atr") is not None:
                return float(
                    result["atr"]
                )

        raise ValueError(
            f"No ATR for {self.instrument}"
        )


def load_candle_frame(
    instrument: str,
    timeframe: str,
) -> pd.DataFrame:
    path = (
        CANDLE_DIRECTORY
        / f"{instrument}_{timeframe}.csv"
    )

    if not path.exists():
        raise FileNotFoundError(
            str(path)
        )

    frame = pd.read_csv(
        path
    )

    if frame.empty:
        raise ValueError(
            f"{path.name} is empty"
        )

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(
                frame[column],
                errors="coerce",
            )

    timestamp_column = None

    for candidate in [
        "timestamp",
        "time",
        "datetime",
    ]:
        if candidate in frame.columns:
            timestamp_column = candidate
            break

    if timestamp_column:
        frame[timestamp_column] = pd.to_datetime(
            frame[timestamp_column],
            utc=True,
            errors="coerce",
        )

    return frame.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    ).reset_index(
        drop=True
    )


def load_contexts(
    maximum_age_minutes: int,
) -> dict[str, MarketContext]:
    market = read_json(
        MARKET_FILE
    )

    regimes = read_json(
        REGIME_FILE
    )

    strength = read_json(
        STRENGTH_FILE
    )

    timestamp = parse_timestamp(
        str(
            market.get(
                "timestamp",
                regimes.get(
                    "timestamp",
                    "",
                ),
            )
        )
    )

    age_minutes = (
        utc_now()
        - timestamp
    ).total_seconds() / 60.0

    if age_minutes > maximum_age_minutes:
        raise ValueError(
            "Foundation snapshot is stale: "
            f"{age_minutes:.1f} minutes old"
        )

    regime_payload = regimes.get(
        "regimes",
        regimes,
    )

    contexts: dict[
        str,
        MarketContext,
    ] = {}

    for instrument, result in (
        regime_payload.items()
    ):
        if not isinstance(
            result,
            dict,
        ):
            continue

        frames: dict[
            str,
            pd.DataFrame,
        ] = {}

        for timeframe in [
            "H4",
            "H1",
            "M15",
            "M5",
        ]:
            frames[timeframe] = (
                load_candle_frame(
                    instrument,
                    timeframe,
                )
            )

        contexts[instrument] = (
            MarketContext(
                instrument=instrument,
                session=str(
                    market.get(
                        "session",
                        "unknown",
                    )
                ),
                active_sessions=list(
                    market.get(
                        "active_sessions",
                        [],
                    )
                ),
                regime=str(
                    result.get(
                        "regime",
                        "mixed",
                    )
                ),
                direction=str(
                    result.get(
                        "direction",
                        "mixed",
                    )
                ),
                confidence=float(
                    result.get(
                        "confidence",
                        0.0,
                    )
                ),
                confirmations=int(
                    result.get(
                        "confirmations",
                        0,
                    )
                ),
                timeframe_results=list(
                    result.get(
                        "timeframe_results",
                        [],
                    )
                ),
                strength_values=dict(
                    strength.get(
                        "values",
                        {},
                    )
                ),
                pair_momentum=float(
                    strength.get(
                        "pair_momentum",
                        {},
                    ).get(
                        instrument,
                        0.0,
                    )
                ),
                candles=frames,
                snapshot_timestamp=timestamp,
            )
        )

    return contexts
