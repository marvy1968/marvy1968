from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_environment() -> None:
    load_dotenv(
        ROOT / ".env",
        override=True,
    )


def load_config(
    path: str | Path | None = None,
) -> dict[str, Any]:
    config_path = (
        Path(path)
        if path is not None
        else ROOT / "config" / "master_ai.yaml"
    )

    with config_path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        config = yaml.safe_load(handle)

    if not isinstance(config, dict):
        raise ValueError(
            f"Invalid configuration: {config_path}"
        )

    return config


def ensure_directories() -> None:
    directories = [
        ROOT / "state",
        ROOT / "state" / "candles",
        ROOT / "state" / "snapshots",
        ROOT / "logs",
        ROOT / "reports",
    ]

    for directory in directories:
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )


def atomic_write_text(
    path: str | Path,
    text: str,
) -> None:
    destination = Path(path)
    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=str(destination.parent),
    )

    temporary_path = Path(temporary_name)

    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

        temporary_path.replace(destination)

    finally:
        temporary_path.unlink(
            missing_ok=True,
        )


def atomic_write_json(
    path: str | Path,
    payload: Any,
) -> None:
    atomic_write_text(
        path,
        json.dumps(
            payload,
            indent=2,
            default=str,
            sort_keys=True,
        )
        + "\n",
    )


def read_json(
    path: str | Path,
    default: Any,
) -> Any:
    source = Path(path)

    if not source.exists():
        return default

    try:
        return json.loads(
            source.read_text(
                encoding="utf-8",
            )
        )

    except (
        json.JSONDecodeError,
        OSError,
    ):
        return default


def append_jsonl(
    path: str | Path,
    payload: dict[str, Any],
) -> None:
    destination = Path(path)
    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with destination.open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(
            json.dumps(
                payload,
                default=str,
                sort_keys=True,
            )
            + "\n"
        )


def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        number = float(value)

        if not math.isfinite(number):
            return default

        return number

    except (
        TypeError,
        ValueError,
    ):
        return default


def environment_flag(
    name: str,
    default: bool = False,
) -> bool:
    raw = os.getenv(
        name,
        str(default),
    ).strip().lower()

    return raw in {
        "1",
        "true",
        "yes",
        "on",
    }


def redact(
    value: str,
    visible: int = 4,
) -> str:
    if not value:
        return ""

    if len(value) <= visible:
        return "*" * len(value)

    return (
        "*" * (len(value) - visible)
        + value[-visible:]
    )
