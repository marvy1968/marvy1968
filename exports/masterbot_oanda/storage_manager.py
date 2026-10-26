#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

#############################################
# CONFIG
#############################################

ROOT = Path.home() / "bots" / "oanda_fifo_practice" / "MarchEdge"
LOG_DIR = ROOT / "logs"
ARCHIVE_DIR = ROOT / "storage" / "archive"

MAX_LOG_SIZE_MB = 25
MAX_ARCHIVE_FILES = 20
MIN_FREE_MB = 750

ROTATE_TARGETS = [
    LOG_DIR / "marchedge_bets.jsonl",
    LOG_DIR / "marchedge_results.jsonl",
    LOG_DIR / "marchedge_bets.csv",
    LOG_DIR / "review_report.json",
    LOG_DIR / "review_report.csv",
]

#############################################
# HELPERS
#############################################

def bytes_to_mb(num_bytes: int) -> float:
    return round(num_bytes / (1024 * 1024), 2)


def get_free_mb(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return bytes_to_mb(usage.free)


def ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def file_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    return bytes_to_mb(path.stat().st_size)


def timestamp_suffix() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


#############################################
# ROTATION
#############################################

def rotate_file(path: Path) -> bool:
    if not path.exists():
        return False

    if file_size_mb(path) < MAX_LOG_SIZE_MB:
        return False

    archive_name = f"{path.stem}_{timestamp_suffix()}{path.suffix}"
    archive_path = ARCHIVE_DIR / archive_name
    shutil.move(str(path), str(archive_path))
    path.touch()

    return True


def prune_archive() -> int:
    files = sorted(
        [p for p in ARCHIVE_DIR.iterdir() if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    removed = 0
    for old_file in files[MAX_ARCHIVE_FILES:]:
        try:
            old_file.unlink()
            removed += 1
        except Exception:
            pass

    return removed


#############################################
# EMERGENCY CLEANUP
#############################################

def emergency_cleanup() -> dict[str, Any]:
    removed = []

    for target in ROTATE_TARGETS:
        if target.exists():
            try:
                target.unlink()
                removed.append(str(target))
            except Exception:
                pass

    return {
        "removed_files": removed,
        "free_mb_after": get_free_mb(ROOT),
    }


#############################################
# MAIN ENTRY
#############################################

def run_storage_manager() -> dict[str, Any]:
    ensure_dirs()

    free_before = get_free_mb(ROOT)
    rotated = []

    for target in ROTATE_TARGETS:
        if rotate_file(target):
            rotated.append(str(target))

    pruned = prune_archive()

    free_after_rotation = get_free_mb(ROOT)

    emergency = None
    if free_after_rotation < MIN_FREE_MB:
        emergency = emergency_cleanup()

    return {
        "free_mb_before": free_before,
        "rotated_files": rotated,
        "archive_pruned": pruned,
        "free_mb_after_rotation": free_after_rotation,
        "emergency_cleanup": emergency,
        "final_free_mb": get_free_mb(ROOT),
    }


if __name__ == "__main__":
    result = run_storage_manager()

    print("STORAGE MANAGER")
    for k, v in result.items():
        print(f"{k}: {v}")
