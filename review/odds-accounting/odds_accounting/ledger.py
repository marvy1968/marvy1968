"""Cross-process observe-only SQLite ledger.

Writers in separate processes share one file. A row is one request attempt.
Inserts are idempotent on attempt_id. A later valid x-requests-last may fill
an unknown actual on that same attempt. It does not add a second attempt.

The ledger never reserves, spends, or blocks a provider call. A locked,
unwritable, or corrupt file returns an error code and writes nothing further.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS request_attempts (
  attempt_id TEXT PRIMARY KEY,
  logical_request_id TEXT NOT NULL,
  attempt_ordinal INTEGER NOT NULL,
  observed_at_utc TEXT NOT NULL,
  day_utc TEXT NOT NULL,
  month_utc TEXT NOT NULL,
  cycle_id TEXT NOT NULL,
  source_job TEXT NOT NULL,
  consumer_id TEXT NOT NULL,
  strategy TEXT,
  endpoint_template TEXT NOT NULL,
  sport TEXT,
  markets_json TEXT NOT NULL,
  regions_json TEXT NOT NULL,
  bookmakers_json TEXT NOT NULL,
  request_shape_json TEXT NOT NULL,
  account_label TEXT,
  http_status INTEGER,
  outcome TEXT NOT NULL,
  is_physical INTEGER NOT NULL,
  estimated_cost INTEGER,
  estimate_provenance TEXT NOT NULL,
  actual_header_cost INTEGER,
  actual_status TEXT NOT NULL,
  cost_basis TEXT NOT NULL,
  shared_used INTEGER,
  shared_used_status TEXT NOT NULL,
  shared_remaining INTEGER,
  shared_remaining_status TEXT NOT NULL,
  exception_type TEXT,
  exception_cause_type TEXT,
  shadow_reservation_id TEXT,
  legacy_import INTEGER NOT NULL,
  legacy_cost_unlabeled INTEGER,
  actual_filled_at_utc TEXT,
  inserted_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS global_counter_samples (
  sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_id TEXT UNIQUE,
  observed_at_utc TEXT NOT NULL,
  month_utc TEXT NOT NULL,
  account_label TEXT,
  source_job TEXT NOT NULL,
  consumer_id TEXT NOT NULL,
  shared_used INTEGER,
  shared_remaining INTEGER,
  used_status TEXT NOT NULL,
  remaining_status TEXT NOT NULL,
  attribution TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observation_errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at_utc TEXT NOT NULL,
  attempt_id TEXT,
  reason_code TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_day ON request_attempts (day_utc, account_label);
CREATE INDEX IF NOT EXISTS idx_attempts_job ON request_attempts (source_job, day_utc);
CREATE INDEX IF NOT EXISTS idx_attempts_logical ON request_attempts (logical_request_id);
"""

_BUSY_SECONDS = 0.075
_ATTRIBUTION = "global_snapshot_not_consumer_cost"
_UPGRADABLE = frozenset({"missing", "unparseable"})


def new_id() -> str:
    return uuid.uuid4().hex


def classify_sqlite_error(exc: BaseException) -> str:
    text = str(exc).lower()
    if "locked" in text or "busy" in text:
        return "locked"
    if "malformed" in text or "not a database" in text or "corrupt" in text or "disk image" in text:
        return "corrupt"
    return "unwritable"


def ledger_path_from_env() -> Path | None:
    raw = os.environ.get("ODDS_ACCOUNTING_LEDGER", "").strip()
    if not raw:
        return None
    return Path(raw)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=_BUSY_SECONDS, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={int(_BUSY_SECONDS * 1000)}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def _ensure(conn: sqlite3.Connection) -> None:
    # executescript commits on its own. Schema statements stay outside the
    # attempt transaction so a rolled-back attempt cannot drop the tables.
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema', 'odds-accounting-observe-1')")


def insert_attempt(path: Path, row: dict) -> dict:
    """Insert or idempotently merge one attempt. Does not raise.

    A locked file is retried a few times. The retries are local writes, not
    provider requests, and the wait stays under a few hundred milliseconds.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {"ok": False, "stored": False, "reason": "unwritable", "attempt_id": row.get("attempt_id"),
                "duplicate": False}
    last = {"ok": False, "stored": False, "reason": "locked", "attempt_id": row.get("attempt_id"),
            "duplicate": False}
    for _ in range(4):
        last = _insert_once(path, row)
        if last["ok"] or last["reason"] != "locked":
            return last
    return last


def _insert_once(path: Path, row: dict) -> dict:
    conn = None
    try:
        conn = _connect(path)
        _ensure(conn)
        conn.execute("BEGIN IMMEDIATE")
        result = _merge(conn, row)
        _commit(conn)
        result.update({"ok": True, "reason": None})
        return result
    except Exception as exc:
        if conn is not None:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
        return {
            "ok": False,
            "stored": False,
            "reason": classify_sqlite_error(exc),
            "attempt_id": row.get("attempt_id"),
            "duplicate": False,
        }
    finally:
        if conn is not None:
            conn.close()


def _commit(conn: sqlite3.Connection) -> None:
    conn.execute("COMMIT")


def _merge(conn: sqlite3.Connection, row: dict) -> dict:
    existing = conn.execute(
        "SELECT * FROM request_attempts WHERE attempt_id=?", (row["attempt_id"],)).fetchone()
    now = row["inserted_at_utc"]
    if existing is None:
        columns = list(row)
        conn.execute(
            f"INSERT INTO request_attempts ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [row[name] for name in columns])
        _insert_sample(conn, row)
        return {"stored": True, "duplicate": False, "attempt_id": row["attempt_id"],
                "actual_header_cost": row["actual_header_cost"], "cost_basis": row["cost_basis"]}
    filled = _fill_later_header(conn, existing, row, now)
    return {"stored": True, "duplicate": True, "filled_later_header": filled,
            "attempt_id": row["attempt_id"], "actual_header_cost": existing["actual_header_cost"] if not filled else row["actual_header_cost"],
            "cost_basis": existing["cost_basis"] if not filled else "header"}


def _fill_later_header(conn, existing, row, now: str) -> bool:
    if existing["actual_status"] == "header" and row["actual_status"] == "header":
        if existing["actual_header_cost"] != row["actual_header_cost"]:
            _error(conn, now, row["attempt_id"], "duplicate_header_ignored")
        else:
            _error(conn, now, row["attempt_id"], "duplicate_ignored")
        _note_snapshot_conflict(conn, existing, row, now)
        return False
    if existing["actual_status"] in _UPGRADABLE and row["actual_status"] == "header":
        conn.execute(
            """UPDATE request_attempts
               SET actual_header_cost=?, actual_status='header', cost_basis='header',
                   actual_filled_at_utc=?
               WHERE attempt_id=? AND actual_status!= 'header'""",
            (row["actual_header_cost"], now, row["attempt_id"]))
        _fill_sample(conn, existing, row, now)
        return True
    _error(conn, now, row["attempt_id"], "duplicate_ignored")
    return False


def _note_snapshot_conflict(conn, existing, row, now: str) -> None:
    sample = conn.execute(
        "SELECT shared_used, used_status FROM global_counter_samples WHERE attempt_id=?",
        (row["attempt_id"],)).fetchone()
    if sample is None or sample["used_status"] != "header" or row["shared_used_status"] != "header":
        return
    if sample["shared_used"] != row["shared_used"]:
        _error(conn, now, row["attempt_id"], "duplicate_snapshot_ignored")


def _insert_sample(conn, row: dict) -> None:
    if row["is_physical"] != 1:
        return
    if row["shared_used_status"] == "missing" and row["shared_remaining_status"] == "missing":
        return
    conn.execute(
        """INSERT INTO global_counter_samples (
             attempt_id, observed_at_utc, month_utc, account_label, source_job, consumer_id,
             shared_used, shared_remaining, used_status, remaining_status, attribution)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (row["attempt_id"], row["observed_at_utc"], row["month_utc"], row["account_label"],
         row["source_job"], row["consumer_id"], row["shared_used"], row["shared_remaining"],
         row["shared_used_status"], row["shared_remaining_status"], _ATTRIBUTION))


def _fill_sample(conn, existing, row, now: str) -> None:
    sample = conn.execute(
        "SELECT sample_id, shared_used, used_status, shared_remaining, remaining_status FROM global_counter_samples WHERE attempt_id=?",
        (row["attempt_id"],)).fetchone()
    if sample is None:
        merged = dict(row)
        merged["observed_at_utc"] = existing["observed_at_utc"]
        _insert_sample(conn, merged)
        return
    used = sample["shared_used"]
    used_status = sample["used_status"]
    remaining = sample["shared_remaining"]
    remaining_status = sample["remaining_status"]
    if used_status != "header" and row["shared_used_status"] == "header":
        used = row["shared_used"]
        used_status = "header"
    elif used_status == "header" and row["shared_used_status"] == "header" and used != row["shared_used"]:
        _error(conn, now, row["attempt_id"], "duplicate_snapshot_ignored")
    if remaining_status != "header" and row["shared_remaining_status"] == "header":
        remaining = row["shared_remaining"]
        remaining_status = "header"
    conn.execute(
        """UPDATE global_counter_samples
           SET shared_used=?, used_status=?, shared_remaining=?, remaining_status=?
           WHERE attempt_id=?""",
        (used, used_status, remaining, remaining_status, row["attempt_id"]))


def _error(conn, now: str, attempt_id: str | None, code: str) -> None:
    conn.execute(
        "INSERT INTO observation_errors (at_utc, attempt_id, reason_code) VALUES (?,?,?)",
        (now, attempt_id, code))


def record_observation_error(path: Path | None, attempt_id: str | None, code: str) -> None:
    if path is None:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = None
    try:
        conn = _connect(path)
        _ensure(conn)
        conn.execute("BEGIN IMMEDIATE")
        _error(conn, now, attempt_id, code)
        _commit(conn)
    except Exception:
        if conn is not None:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
    finally:
        if conn is not None:
            conn.close()


def read_state(path: Path) -> dict:
    """Read attempts and global snapshots. A bad file returns an error object."""
    empty = {"ok": False, "reason": "unreadable", "attempts": [], "samples": [], "errors": []}
    try:
        conn = _connect(path)
    except Exception as exc:
        empty["reason"] = classify_sqlite_error(exc)
        return empty
    try:
        conn.execute("BEGIN")
        attempts = [_attempt_dict(row) for row in conn.execute(
            "SELECT * FROM request_attempts ORDER BY observed_at_utc, attempt_id")]
        samples = [dict(row) for row in conn.execute(
            "SELECT * FROM global_counter_samples ORDER BY observed_at_utc, sample_id")]
        errors = [dict(row) for row in conn.execute(
            "SELECT * FROM observation_errors ORDER BY id")]
        conn.execute("ROLLBACK")
        return {"ok": True, "reason": None, "attempts": attempts, "samples": samples, "errors": errors}
    except Exception as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        empty["reason"] = classify_sqlite_error(exc)
        return empty
    finally:
        conn.close()


def _attempt_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    for name in ("markets_json", "regions_json", "bookmakers_json", "request_shape_json"):
        item[name[:-5]] = json.loads(item.pop(name))
    return item
