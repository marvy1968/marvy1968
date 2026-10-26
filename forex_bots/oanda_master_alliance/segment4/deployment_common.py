from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from core.common import ROOT


CONFIG_PATH = (
    ROOT
    / "config/segment4a3.yaml"
)


def utc_now() -> datetime:
    return datetime.now().astimezone()


def utc_now_iso() -> str:
    return utc_now().isoformat()


def load_segment4a3_config() -> dict[str, Any]:
    payload = yaml.safe_load(
        CONFIG_PATH.read_text(
            encoding="utf-8",
        )
    )

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "config/segment4a3.yaml must be a mapping"
        )

    config = payload.get(
        "segment4a3",
        {},
    )

    if not isinstance(
        config,
        dict,
    ):
        raise ValueError(
            "segment4a3 configuration must be a mapping"
        )

    return config


def resolve_root_path(
    value: str | Path,
) -> Path:
    path = Path(
        str(
            value
        )
    ).expanduser()

    if path.is_absolute():
        return path

    return (
        ROOT
        / path
    )


def deployment_paths() -> dict[str, Path]:
    config = load_segment4a3_config()

    files = config.get(
        "files",
        {},
    )

    if not isinstance(
        files,
        dict,
    ):
        files = {}

    defaults = {
        "deployment_status": (
            "state/segment4a3/"
            "deployment_status.json"
        ),
        "service_status": (
            "state/segment4a3/"
            "service_status.json"
        ),
        "maintenance_status": (
            "state/maintenance/"
            "maintenance_status.json"
        ),
        "maintenance_history": (
            "logs/"
            "segment4a3_maintenance.jsonl"
        ),
        "backup_status": (
            "state/backups/"
            "backup_status.json"
        ),
        "backup_history": (
            "logs/"
            "segment4a3_backups.jsonl"
        ),
        "alert_status": (
            "state/alerts/"
            "alert_status.json"
        ),
        "alert_history": (
            "logs/"
            "segment4a3_alerts.jsonl"
        ),
        "alert_registry": (
            "state/alerts/"
            "alert_registry.json"
        ),
        "report": (
            "reports/segment4/"
            "segment4a3_report.md"
        ),
    }

    return {
        name: resolve_root_path(
            files.get(
                name,
                default,
            )
        )
        for name, default in defaults.items()
    }


def atomic_write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(
        path.suffix
        + ".tmp"
    )

    temporary.write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary.replace(
        path
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


def read_json(
    path: Path,
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
        OSError,
        json.JSONDecodeError,
    ):
        return default


def free_disk_mb(
    path: Path = ROOT,
) -> float:
    usage = shutil.disk_usage(
        path
    )

    return (
        usage.free
        / 1024
        / 1024
    )


def command_result(
    command: list[str],
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(
                ROOT
            ),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

        return {
            "command": command,
            "return_code": (
                completed.returncode
            ),
            "passed": (
                completed.returncode
                == 0
            ),
            "stdout": (
                completed.stdout[-4000:]
            ),
            "stderr": (
                completed.stderr[-4000:]
            ),
        }

    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "return_code": 124,
            "passed": False,
            "stdout": str(
                exc.stdout
                or ""
            )[-4000:],
            "stderr": (
                "Command timed out."
            ),
        }

    except OSError as exc:
        return {
            "command": command,
            "return_code": 127,
            "passed": False,
            "stdout": "",
            "stderr": str(
                exc
            ),
        }


def hostname() -> str:
    try:
        return socket.gethostname()

    except OSError:
        return "unknown"


def safety_configuration() -> dict[str, Any]:
    config = load_segment4a3_config()

    checks = {
        "practice_only": (
            config.get(
                "practice_only"
            )
            is True
        ),
        "live_environment_blocked": (
            config.get(
                "live_environment_allowed"
            )
            is False
        ),
        "automatic_execution_disabled": (
            config.get(
                "automatic_execution"
            )
            is False
        ),
        "order_submission_disabled": (
            config.get(
                (
                    "practice_order_"
                    "submission_enabled"
                )
            )
            is False
        ),
    }

    return {
        "passed": all(
            checks.values()
        ),
        "checks": checks,
    }


def process_environment() -> dict[str, Any]:
    return {
        "user": os.getenv(
            "USER",
            "",
        ),
        "home": os.getenv(
            "HOME",
            "",
        ),
        "hostname": hostname(),
        "python": str(
            ROOT
            / ".venv/bin/python"
        ),
        "root": str(
            ROOT
        ),
    }
