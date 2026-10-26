from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from core.common import ROOT


CONFIG_PATH = ROOT / "config/segment4b3.yaml"


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def load_config() -> dict[str, Any]:
    payload = yaml.safe_load(
        CONFIG_PATH.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "Segment 4B-3 configuration is invalid."
        )

    config = payload.get(
        "segment4b3",
        {},
    )

    if not isinstance(
        config,
        dict,
    ):
        raise ValueError(
            "Missing segment4b3 configuration."
        )

    return config


def resolve_path(
    value: str | Path,
) -> Path:
    path = Path(
        value
    )

    if path.is_absolute():
        return path

    return ROOT / path


def storage_path(
    config: dict[str, Any],
    key: str,
) -> Path:
    storage = config.get(
        "storage",
        {},
    )

    if not isinstance(
        storage,
        dict,
    ):
        storage = {}

    value = storage.get(
        key
    )

    if not value:
        raise KeyError(
            f"Missing Segment 4B-3 storage path: {key}"
        )

    return resolve_path(
        str(
            value
        )
    )


def log_path(
    config: dict[str, Any],
    key: str,
) -> Path:
    logs = config.get(
        "logs",
        {},
    )

    if not isinstance(
        logs,
        dict,
    ):
        logs = {}

    value = logs.get(
        key
    )

    if not value:
        raise KeyError(
            f"Missing Segment 4B-3 log path: {key}"
        )

    return resolve_path(
        str(
            value
        )
    )


def read_json(
    path: Path,
    default: Any,
) -> Any:
    if not path.exists():
        return default

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except (
        OSError,
        json.JSONDecodeError,
    ):
        return default


def atomic_write_json(
    path: Path,
    payload: Any,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(
            path.parent
        ),
        delete=False,
    ) as handle:
        json.dump(
            payload,
            handle,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )

        handle.write(
            "\n"
        )

        temporary_path = Path(
            handle.name
        )

    os.replace(
        temporary_path,
        path,
    )


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
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )


def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        number = float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return default

    if number != number:
        return default

    if number == float(
        "inf"
    ):
        return 999999.0

    if number == float(
        "-inf"
    ):
        return -999999.0

    return number


def safe_int(
    value: Any,
    default: int = 0,
) -> int:
    try:
        return int(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return default


def percentile(
    values: list[float],
    percent: float,
) -> float:
    if not values:
        return 0.0

    ordered = sorted(
        values
    )

    if len(
        ordered
    ) == 1:
        return ordered[0]

    position = (
        len(
            ordered
        )
        - 1
    ) * (
        percent
        / 100.0
    )

    lower_index = int(
        position
    )

    upper_index = min(
        lower_index + 1,
        len(
            ordered
        )
        - 1,
    )

    fraction = (
        position
        - lower_index
    )

    return (
        ordered[
            lower_index
        ]
        * (
            1.0
            - fraction
        )
        + ordered[
            upper_index
        ]
        * fraction
    )
