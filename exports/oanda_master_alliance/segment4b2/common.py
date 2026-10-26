from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from core.common import ROOT


CONFIG_PATH = (
    ROOT
    / "config/segment4b2.yaml"
)


def utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_timestamp(
    value: Any,
) -> datetime | None:
    if not value:
        return None

    text = str(
        value
    ).strip()

    if not text:
        return None

    if text.endswith(
        "Z"
    ):
        text = (
            text[:-1]
            + "+00:00"
        )

    try:
        result = datetime.fromisoformat(
            text
        )

    except ValueError:
        return None

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=timezone.utc
        )

    return result.astimezone(
        timezone.utc
    )


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
            "Segment 4B-2 configuration is invalid."
        )

    config = payload.get(
        "segment4b2",
        {},
    )

    if not isinstance(
        config,
        dict,
    ):
        raise ValueError(
            "Missing segment4b2 configuration."
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

    return (
        ROOT
        / path
    )


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
            f"Missing storage path: {key}"
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
            f"Missing log path: {key}"
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
            )
            + "\n"
        )


def pip_size(
    instrument: str,
) -> float:
    return (
        0.01
        if instrument.upper().endswith(
            "_JPY"
        )
        else 0.0001
    )


def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        return float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return default


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
