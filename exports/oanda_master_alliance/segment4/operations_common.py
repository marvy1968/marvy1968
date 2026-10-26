from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from segment4.common import (
    ROOT,
    age_seconds,
    append_jsonl,
    atomic_write_json,
    process_exists,
    read_json,
    resolve_path,
    utc_now_iso,
)


def load_operations_config() -> dict[str, Any]:
    path = (
        ROOT
        / "config/segment4.yaml"
    )

    payload = yaml.safe_load(
        path.read_text(
            encoding="utf-8",
        )
    )

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "config/segment4.yaml must be a mapping"
        )

    operations = payload.get(
        "operations",
        {},
    )

    if not isinstance(
        operations,
        dict,
    ):
        raise ValueError(
            "config/segment4.yaml operations must be a mapping"
        )

    return operations


def operations_paths() -> dict[str, Path]:
    config = load_operations_config()

    files = dict(
        config.get(
            "files",
            {},
        )
    )

    defaults = {
        "watchdog_status": (
            "state/segment4/"
            "watchdog_status.json"
        ),
        "watchdog_pid": (
            "state/segment4/"
            "watchdog.pid"
        ),
        "watchdog_lock": (
            "state/segment4/"
            "watchdog.lock"
        ),
        "watchdog_shutdown": (
            "state/segment4/"
            "WATCHDOG_SHUTDOWN_REQUEST"
        ),
        "health_status": (
            "state/segment4/"
            "health_status.json"
        ),
        "health_history": (
            "logs/"
            "segment4_health.jsonl"
        ),
        "recovery_status": (
            "state/recovery/"
            "recovery_status.json"
        ),
        "recovery_history": (
            "logs/"
            "segment4_recovery.jsonl"
        ),
        "runtime_checkpoint": (
            "state/recovery/"
            "runtime_checkpoint.json"
        ),
        "unresolved_recovery": (
            "state/recovery/"
            "UNRESOLVED_RECOVERY"
        ),
        "duplicate_registry": (
            "state/recovery/"
            "duplicate_registry.json"
        ),
        "restart_history": (
            "state/recovery/"
            "restart_history.json"
        ),
    }

    return {
        name: resolve_path(
            str(
                files.get(
                    name,
                    default,
                )
            )
        )
        for name, default in defaults.items()
    }


def read_pid_file(
    path: Path,
) -> int:
    if not path.exists():
        return 0

    try:
        return int(
            path.read_text(
                encoding="utf-8",
            ).strip()
        )

    except (
        OSError,
        ValueError,
    ):
        return 0


def file_age_seconds(
    path: Path,
) -> float | None:
    if not path.exists():
        return None

    try:
        modified = datetime.fromtimestamp(
            path.stat().st_mtime
        ).astimezone()

    except OSError:
        return None

    return max(
        0.0,
        (
            datetime.now().astimezone()
            - modified
        ).total_seconds(),
    )


def stable_identifier(
    *parts: object,
    length: int = 24,
) -> str:
    text = "|".join(
        str(
            part
        ).strip()
        for part in parts
    )

    digest = hashlib.sha256(
        text.encode(
            "utf-8",
        )
    ).hexdigest()

    return digest[
        :max(
            8,
            int(
                length
            ),
        )
    ]


def directory_size_bytes(
    path: Path,
) -> int:
    if not path.exists():
        return 0

    total = 0

    for item in path.rglob(
        "*"
    ):
        if not item.is_file():
            continue

        try:
            total += item.stat().st_size

        except OSError:
            continue

    return total


def safe_hostname() -> str:
    try:
        return socket.gethostname()

    except OSError:
        return "unknown"


def load_jsonl(
    path: Path,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    records: list[
        dict[str, Any]
    ] = []

    for line in path.read_text(
        encoding="utf-8",
    ).splitlines():
        if not line.strip():
            continue

        try:
            payload = json.loads(
                line
            )

        except json.JSONDecodeError:
            continue

        if isinstance(
            payload,
            dict,
        ):
            records.append(
                payload
            )

    return records


def write_checkpoint(
    payload: dict[str, Any],
) -> Path:
    path = operations_paths()[
        "runtime_checkpoint"
    ]

    checkpoint = {
        "timestamp": utc_now_iso(),
        "hostname": safe_hostname(),
        "pid": os.getpid(),
        **payload,
    }

    atomic_write_json(
        path,
        checkpoint,
    )

    return path
