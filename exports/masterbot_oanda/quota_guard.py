#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict

from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

QUOTA_STATE_PATH = os.getenv("QUOTA_STATE_PATH", "storage/quota_guard_state.json")
QUOTA_MIN_INTERVAL_SEC = float(os.getenv("QUOTA_MIN_INTERVAL_SEC", "60"))
ENABLE_QUOTA_GUARD = os.getenv("ENABLE_QUOTA_GUARD", "true").lower() == "true"


#############################################
# FILE HELPERS
#############################################

def _ensure_parent(path: str) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data: Any) -> None:
    _ensure_parent(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


#############################################
# STATE
#############################################

def _default_state() -> Dict[str, Any]:
    return {
        "last_api_hit_ts": 0.0,
        "last_api_hit_label": "",
        "api_hits": [],
    }


def load_quota_state() -> Dict[str, Any]:
    state = _load_json(QUOTA_STATE_PATH, _default_state())
    if not isinstance(state, dict):
        return _default_state()

    # fill missing keys safely
    state.setdefault("last_api_hit_ts", 0.0)
    state.setdefault("last_api_hit_label", "")
    state.setdefault("api_hits", [])
    return state


def save_quota_state(state: Dict[str, Any]) -> None:
    _save_json(QUOTA_STATE_PATH, state)


#############################################
# LOGGING
#############################################

def log_api_hit(label: str) -> None:
    now = time.time()
    ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))

    state = load_quota_state()
    state["last_api_hit_ts"] = now
    state["last_api_hit_label"] = label

    hits = state.get("api_hits", [])
    if not isinstance(hits, list):
        hits = []

    hits.append({
        "ts": now,
        "time_str": ts_str,
        "label": label,
    })

    # keep recent history only
    state["api_hits"] = hits[-200:]
    save_quota_state(state)

    print(f"[API CALL] {ts_str} -> {label}")


#############################################
# GUARD
#############################################

def get_required_sleep() -> float:
    if not ENABLE_QUOTA_GUARD:
        return 0.0

    state = load_quota_state()
    last_ts = float(state.get("last_api_hit_ts", 0.0) or 0.0)

    if last_ts <= 0:
        return 0.0

    elapsed = time.time() - last_ts
    remaining = QUOTA_MIN_INTERVAL_SEC - elapsed
    return max(0.0, remaining)


def enforce_quota_guard() -> None:
    """
    Backward-compatible sync version.
    Avoid this inside async code when possible.
    """
    sleep_time = get_required_sleep()
    if sleep_time > 0:
        print(f"[QUOTA GUARD] Sleeping {round(sleep_time, 1)}s to protect API usage...")
        time.sleep(sleep_time)


async def enforce_quota_guard_async() -> None:
    """
    Async-safe version for use inside async pipelines.
    """
    sleep_time = get_required_sleep()
    if sleep_time > 0:
        print(f"[QUOTA GUARD] Sleeping {round(sleep_time, 1)}s to protect API usage...")
        await asyncio.sleep(sleep_time)


#############################################
# DEBUG
#############################################

if __name__ == "__main__":
    print("QUOTA GUARD TEST")
    print("enabled:", ENABLE_QUOTA_GUARD)
    print("min_interval_sec:", QUOTA_MIN_INTERVAL_SEC)
    print("required_sleep_now:", round(get_required_sleep(), 2))

    print("\nLogging fake API hit...")
    log_api_hit("test_call")

    print("required_sleep_after_hit:", round(get_required_sleep(), 2))
