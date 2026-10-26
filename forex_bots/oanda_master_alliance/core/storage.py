from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from core.common import (
    ROOT,
    atomic_write_json,
    read_json,
)


CANDLE_DIRECTORY = (
    ROOT
    / "state"
    / "candles"
)


def candle_path(
    instrument: str,
    timeframe: str,
) -> Path:
    return (
        CANDLE_DIRECTORY
        / f"{instrument}_{timeframe}.csv"
    )


def save_candles(
    instrument: str,
    timeframe: str,
    frame: pd.DataFrame,
) -> Path:
    destination = candle_path(
        instrument,
        timeframe,
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    frame.to_csv(
        destination,
        index=False,
    )

    return destination


def load_candles(
    instrument: str,
    timeframe: str,
) -> pd.DataFrame:
    source = candle_path(
        instrument,
        timeframe,
    )

    if not source.exists():
        raise FileNotFoundError(source)

    frame = pd.read_csv(source)

    if "timestamp" not in frame.columns:
        raise ValueError(
            f"{source.name} lacks timestamp"
        )

    frame["timestamp"] = pd.to_datetime(
        frame["timestamp"],
        utc=True,
        errors="coerce",
    )

    frame = (
        frame.dropna(
            subset=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
            ]
        )
        .sort_values("timestamp")
        .drop_duplicates(
            subset=["timestamp"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    return frame


def save_state(
    relative_path: str,
    payload: dict[str, Any],
) -> Path:
    destination = ROOT / relative_path
    atomic_write_json(
        destination,
        payload,
    )
    return destination


def load_state(
    relative_path: str,
    default: Any,
) -> Any:
    return read_json(
        ROOT / relative_path,
        default,
    )
