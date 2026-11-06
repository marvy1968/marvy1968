"""One run at a time. A second process exits instead of overlapping orders."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path

from .errors import AlreadyRunning


@contextmanager
def run_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise AlreadyRunning(str(path)) from exc
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
