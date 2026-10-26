from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from core.common import ROOT


CONFIG_FILE = (
    ROOT
    / "config/segment4b.yaml"
)


def utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


def utc_now_iso() -> str:
    return utc_now().isoformat()


def load_segment4b_config() -> dict[str, Any]:
    payload = yaml.safe_load(
        CONFIG_FILE.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "Segment 4B configuration is invalid."
        )

    config = payload.get(
        "segment4b",
        {},
    )

    if not isinstance(
        config,
        dict,
    ):
        raise ValueError(
            "Missing segment4b configuration."
        )

    return config


def read_json(
    path: Path,
    *,
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
        json.JSONDecodeError,
        OSError,
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

    descriptor, temporary_name = (
        tempfile.mkstemp(
            dir=str(
                path.parent
            ),
            prefix=(
                f".{path.name}."
            ),
            suffix=".tmp",
        )
    )

    temporary_path = Path(
        temporary_name
    )

    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
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

            handle.flush()
            os.fsync(
                handle.fileno()
            )

        temporary_path.replace(
            path
        )

    finally:
        temporary_path.unlink(
            missing_ok=True
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


def parse_datetime(
    value: Any,
) -> datetime | None:
    if not isinstance(
        value,
        str,
    ):
        return None

    normalized = value.strip()

    if not normalized:
        return None

    if normalized.endswith(
        "Z"
    ):
        normalized = (
            normalized[:-1]
            + "+00:00"
        )

    try:
        result = datetime.fromisoformat(
            normalized
        )

    except ValueError:
        return None

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=timezone.utc
        )

    return result


def age_minutes(
    value: Any,
) -> float | None:
    parsed = parse_datetime(
        value
    )

    if parsed is None:
        return None

    return max(
        0.0,
        (
            utc_now()
            - parsed.astimezone(
                timezone.utc
            )
        ).total_seconds()
        / 60.0,
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
        raise ValueError(
            "Segment 4B storage configuration is invalid."
        )

    relative_path = storage.get(
        key
    )

    if not isinstance(
        relative_path,
        str,
    ) or not relative_path.strip():
        raise KeyError(
            f"Missing Segment 4B storage path: {key}"
        )

    return (
        ROOT
        / relative_path
    )


def event_log(
    config: dict[str, Any],
) -> Path:
    logging_config = config.get(
        "logging",
        {},
    )

    return (
        ROOT
        / str(
            logging_config.get(
                "event_log",
                "logs/segment4b_events.jsonl",
            )
        )
    )


def error_log(
    config: dict[str, Any],
) -> Path:
    logging_config = config.get(
        "logging",
        {},
    )

    return (
        ROOT
        / str(
            logging_config.get(
                "error_log",
                "logs/segment4b_errors.jsonl",
            )
        )
    )
