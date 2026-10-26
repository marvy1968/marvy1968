from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_segment4b4_config() -> dict[str, Any]:
    path = ROOT / "config/segment4b4.yaml"

    payload = yaml.safe_load(
        path.read_text(encoding="utf-8")
    )

    config = payload.get("segment4b4", payload)

    if not isinstance(config, dict):
        raise ValueError(
            "Segment 4B-4 configuration is invalid."
        )

    required_safety = {
        "research_only": True,
        "practice_only": True,
        "live_environment_allowed": False,
        "automatic_execution": False,
        "practice_order_submission_enabled": False,
        "broker_order_submission_enabled": False,
    }

    failures = [
        key
        for key, expected in required_safety.items()
        if config.get(key) is not expected
    ]

    if failures:
        raise RuntimeError(
            "Unsafe Segment 4B-4 configuration: "
            + ", ".join(failures)
        )

    return config


def resolve_path(
    config: dict[str, Any],
    key: str,
) -> Path:
    files = config.get("files", {})

    if not isinstance(files, dict):
        raise ValueError(
            "Segment 4B-4 file configuration is invalid."
        )

    value = str(files.get(key, "")).strip()

    if not value:
        raise KeyError(
            f"Missing Segment 4B-4 file path: {key}"
        )

    path = Path(value)

    if not path.is_absolute():
        path = ROOT / path

    return path


def read_json(
    path: Path,
    *,
    default: Any,
) -> Any:
    if not path.exists():
        return default

    try:
        return json.loads(
            path.read_text(encoding="utf-8")
        )
    except (
        json.JSONDecodeError,
        OSError,
        TypeError,
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

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    temporary_path = Path(temporary_name)

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
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        temporary_path.replace(path)
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
