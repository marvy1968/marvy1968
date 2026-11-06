"""Read ALPACA_API_KEY and ALPACA_SECRET_KEY from the file in COS1_ENV_FILE.

Values are never logged.
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import MissingCredentials


def env_file_path() -> str:
    path = os.environ.get("COS1_ENV_FILE", "").strip()
    if not path:
        raise MissingCredentials("COS1_ENV_FILE is not set")
    return path


def load_keys(path: str | None = None) -> tuple[str, str]:
    file_path = Path(path or env_file_path())
    if not file_path.is_file():
        raise MissingCredentials("env file is missing")
    vals: dict[str, str] = {}
    for line in file_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        vals[key.strip()] = value.strip().strip('"').strip("'")
    api_key = vals.get("ALPACA_API_KEY", "")
    secret = vals.get("ALPACA_SECRET_KEY", "")
    if not api_key or not secret:
        raise MissingCredentials("ALPACA_API_KEY and ALPACA_SECRET_KEY are required")
    return api_key, secret
