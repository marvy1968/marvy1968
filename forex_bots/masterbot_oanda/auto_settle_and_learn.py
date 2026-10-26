
#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENV_PYTHON = BASE_DIR / "venv" / "bin" / "python3"


def get_python() -> str:
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    return sys.executable


def run_step(name: str, script_name: str, python_bin: str) -> int:
    script_path = BASE_DIR / script_name
    print(f"\n=== {name} ===")
    print("cmd:", python_bin, str(script_path))
    result = subprocess.run([python_bin, str(script_path)], cwd=str(BASE_DIR))
    print(f"exit_code={result.returncode}")
    return result.returncode


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    print("=== AUTO SETTLE + LEARN START ===")
    print("utc:", utc_now())
    print("base_dir:", BASE_DIR)

    if not BASE_DIR.exists():
        print(f"base dir not found: {BASE_DIR}")
        return 2

    python_bin = get_python()
    print("python_bin:", python_bin)

    rc1 = run_step("SETTLEMENT", "settlement_engine.py", python_bin)
    if rc1 != 0:
        print("settlement step failed; stopping before learning")
        return rc1

    rc2 = run_step("LEARNING", "learning_engine.py", python_bin)
    if rc2 != 0:
        print("learning step failed")
        return rc2

    print("\n=== AUTO SETTLE + LEARN COMPLETE ===")
    print("utc:", utc_now())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
