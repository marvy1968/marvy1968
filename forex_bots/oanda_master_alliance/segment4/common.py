from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

CONFIG_PATH = (
    ROOT
    / "config/segment4.yaml"
)


def utc_now() -> datetime:
    return datetime.now().astimezone()


def utc_now_iso() -> str:
    return utc_now().isoformat()


def load_segment4_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Missing Segment 4 configuration: {CONFIG_PATH}"
        )

    payload = yaml.safe_load(
        CONFIG_PATH.read_text(
            encoding="utf-8",
        )
    )

    if not isinstance(payload, dict):
        raise ValueError(
            "Segment 4 configuration root must be a mapping."
        )

    config = payload.get(
        "segment4",
    )

    if not isinstance(config, dict):
        raise ValueError(
            "config/segment4.yaml must contain a segment4 mapping."
        )

    return config


def resolve_path(
    configured_path: str,
) -> Path:
    path = Path(
        configured_path
    )

    if not path.is_absolute():
        path = ROOT / path

    return path


def ensure_parent(
    path: Path,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )


def atomic_write_text(
    path: Path,
    text: str,
) -> None:
    ensure_parent(
        path
    )

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=(
            f".{path.name}."
        ),
        suffix=".tmp",
        dir=str(
            path.parent
        ),
    )

    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write(
                text
            )

            handle.flush()
            os.fsync(
                handle.fileno()
            )

        Path(
            temporary_name
        ).replace(
            path
        )

    finally:
        temporary_path = Path(
            temporary_name
        )

        if temporary_path.exists():
            temporary_path.unlink(
                missing_ok=True
            )


def atomic_write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:
    atomic_write_text(
        path,
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def read_json(
    path: Path,
    *,
    default: Any = None,
) -> Any:
    if not path.exists():
        return default

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )

    except (
        json.JSONDecodeError,
        OSError,
    ):
        return default


def append_jsonl(
    path: Path,
    payload: dict[str, Any],
) -> None:
    ensure_parent(
        path
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


def process_exists(
    process_id: int,
) -> bool:
    if process_id <= 0:
        return False

    try:
        os.kill(
            process_id,
            0,
        )

    except ProcessLookupError:
        return False

    except PermissionError:
        return True

    return True


def age_seconds(
    timestamp_text: str | None,
) -> float | None:
    if not timestamp_text:
        return None

    try:
        timestamp = datetime.fromisoformat(
            timestamp_text
        )

    except ValueError:
        return None

    if timestamp.tzinfo is None:
        timestamp = timestamp.astimezone()

    return max(
        0.0,
        (
            utc_now()
            - timestamp
        ).total_seconds(),
    )
