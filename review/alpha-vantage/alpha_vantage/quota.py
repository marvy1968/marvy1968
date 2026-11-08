"""Durable cross-process reservation ledger.

An attempt is counted when it is reserved, before the HTTP call, and it is not
removed if the call fails. The same symbol and function is not reserved again
on that UTC day unless the caller explicitly passes ``allow_repeat``. Repeating
still consumes another of the 25 slots. There is no automatic retry.

The day boundary is this file's UTC calendar day. It is not a claim about when
Alpha Vantage resets. Other users of the key are outside this file.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import fcntl

from .errors import LocalRefusal, LockTimeout, QuotaExceeded, RepeatAttemptRefused
from .spec import FREE_DAILY_QUOTA, SHARED_QUOTA_CAVEAT
from .validate import SeriesRequest

_OUTCOMES = {"reserved", "success", "network", "http_status", "provider_message", "validation", "failure"}
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_GUARD = threading.Lock()
_KEEP_DAYS = 14


@dataclass(frozen=True)
class Reservation:
    token: str
    day: str
    attempt_number: int
    symbol: str
    function: str
    outputsize: str | None


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _THREAD_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


class QuotaLedger:
    def __init__(self, directory: Path, *, wait_seconds: float = 10.0):
        self.directory = Path(directory)
        self.path = self.directory / "quota-ledger.json"
        self.lock_path = self.directory / "quota-ledger.lock"
        self.wait_seconds = wait_seconds

    def reserve(self, request: SeriesRequest, *, now: datetime, allow_repeat: bool = False) -> Reservation:
        moment = _utc(now)
        day = moment.date().isoformat()
        with self._locked():
            data = self._read()
            self._prune(data, moment.date())
            bucket = data["days"].setdefault(day, {"attempts": []})
            attempts = bucket["attempts"]
            identity = (request.symbol, request.function, request.outputsize)
            if not allow_repeat and any(_identity(item) == identity for item in attempts):
                raise RepeatAttemptRefused(
                    "this symbol and function already has an attempt on this UTC day; not retrying"
                )
            if len(attempts) >= FREE_DAILY_QUOTA:
                raise QuotaExceeded(
                    f"local UTC-day ledger already has {FREE_DAILY_QUOTA} attempts; no request was sent"
                )
            token = uuid.uuid4().hex
            attempts.append(
                {
                    "token": token,
                    "symbol": request.symbol,
                    "function": request.function,
                    "outputsize": request.outputsize,
                    "reserved_at_utc": moment.isoformat(),
                    "outcome": "reserved",
                }
            )
            self._write(data)
            return Reservation(token, day, len(attempts), request.symbol, request.function, request.outputsize)

    def complete(self, token: str, outcome: str, *, now: datetime) -> None:
        if outcome not in _OUTCOMES:
            outcome = "failure"
        moment = _utc(now)
        with self._locked():
            data = self._read()
            for bucket in data["days"].values():
                for attempt in bucket["attempts"]:
                    if attempt.get("token") == token:
                        attempt["outcome"] = outcome
                        attempt["completed_at_utc"] = moment.isoformat()
                        self._write(data)
                        return
            raise LocalRefusal("unknown reservation")

    def attempt_count(self, now: datetime) -> int:
        moment = _utc(now)
        with self._locked():
            data = self._read()
            bucket = data["days"].get(moment.date().isoformat(), {"attempts": []})
            return len(bucket["attempts"])

    @contextmanager
    def _locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        thread_lock = _thread_lock(self.lock_path)
        thread_lock.acquire()
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        acquired = False
        try:
            deadline = time.monotonic() + self.wait_seconds
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LockTimeout("quota ledger lock timed out; no request was reserved") from None
                    time.sleep(0.02)
            yield
        finally:
            if acquired:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            thread_lock.release()

    def _read(self) -> dict:
        if not self.path.exists():
            return _empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LocalRefusal("quota ledger is unreadable; refusing to reserve a request") from exc
        if not isinstance(data, dict) or data.get("format") != 1 or not isinstance(data.get("days"), dict):
            raise LocalRefusal("quota ledger format is not recognized; refusing to reserve a request")
        for day, bucket in data["days"].items():
            try:
                date.fromisoformat(day)
            except ValueError:
                raise LocalRefusal("quota ledger format is not recognized; refusing to reserve a request") from None
            if not isinstance(bucket, dict) or not isinstance(bucket.get("attempts"), list):
                raise LocalRefusal("quota ledger format is not recognized; refusing to reserve a request")
            for attempt in bucket["attempts"]:
                if not isinstance(attempt, dict) or "token" not in attempt or "outcome" not in attempt:
                    raise LocalRefusal("quota ledger format is not recognized; refusing to reserve a request")
        return data

    def _write(self, data: dict) -> None:
        data["format"] = 1
        data["limit"] = FREE_DAILY_QUOTA
        data["window"] = "utc_calendar_day"
        data["note"] = SHARED_QUOTA_CAVEAT
        _atomic_json(self.path, data)

    def _prune(self, data: dict, today: date) -> None:
        kept = {}
        for day, bucket in data["days"].items():
            if (today - date.fromisoformat(day)) <= timedelta(days=_KEEP_DAYS):
                kept[day] = bucket
        data["days"] = kept


def _empty() -> dict:
    return {"format": 1, "limit": FREE_DAILY_QUOTA, "window": "utc_calendar_day", "note": SHARED_QUOTA_CAVEAT, "days": {}}


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise LocalRefusal("ledger clock must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _identity(attempt: dict) -> tuple:
    return (attempt.get("symbol"), attempt.get("function"), attempt.get("outputsize"))


def _atomic_json(path: Path, payload: dict) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
