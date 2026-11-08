"""Guarded review ledger. Fills and audits change only inside the authorization window.

A second sqlite connection opened by some other code is outside this guard. The
package refuses a file that already has a fills table but is not a review ledger,
so it will not migrate or rewrite an existing paper database.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from contextvars import ContextVar

_purpose: ContextVar[str | None] = ContextVar("poly_paper_purpose", default=None)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

FORBIDDEN_ROOTS = (
    "/opt/polymarket-paper",
    "/opt/marv-bot",
    "/opt/marv-feed",
    "/opt/kalshi-demo",
)
SCHEMA_ID = "review-poly-paper-20261108"

_FILLS = """
create table if not exists fills (
  id integer primary key,
  ts text,
  date text,
  key text,
  pick text,
  slug text,
  side text,
  contracts integer,
  price real,
  fee real,
  cost real,
  fair real,
  edge real,
  status text,
  pnl real,
  result text,
  payout real,
  settle_px real,
  settled_ts text,
  settle_src text,
  me_type text,
  me_alert_id integer,
  lane text,
  sport_canon text,
  event_id text,
  event_slug text,
  event_date text,
  participants_json text,
  outcome_json text,
  token_id text,
  token_status text,
  input_sha256 text,
  snapshot_sha256 text,
  policy_sha256 text,
  source_version text,
  source_tree_sha256 text,
  audit_id integer,
  unique(date, key)
)
"""

_AUDIT = """
create table if not exists fill_audit (
  id integer primary key,
  ts text not null,
  action text not null,
  reasons_json text not null,
  fill_id integer,
  writer text,
  caller text,
  input_json text not null,
  input_sha256 text not null,
  snapshot_json text,
  snapshot_sha256 text,
  sport text,
  event_id text,
  event_slug text,
  event_date text,
  participants_json text,
  outcome_json text,
  token_id text,
  token_status text,
  requested_side text,
  policy_id text,
  policy_sha256 text,
  effective_policy_json text,
  decision_json text not null,
  source_version text not null,
  source_tree_sha256 text not null
)
"""

_META = "create table if not exists review_meta (k text primary key, v text not null)"

IDENTITY_COLUMNS = (
    "date", "key", "pick", "slug", "side", "contracts", "price", "fee", "cost", "fair", "edge",
    "me_type", "me_alert_id", "lane", "sport_canon", "event_id", "event_slug", "event_date",
    "participants_json", "outcome_json", "token_id", "token_status", "input_sha256",
    "snapshot_sha256", "policy_sha256", "source_version", "source_tree_sha256",
)


class LedgerRefusal(RuntimeError):
    pass


def _lock_for(path: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(path, threading.Lock())


@contextmanager
def purpose(name: str):
    token = _purpose.set(name)
    try:
        yield
    finally:
        _purpose.reset(token)


def _authorizer(action, arg1, arg2, dbname, source):
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    table = arg1 if isinstance(arg1, str) else ""
    current = _purpose.get()
    if action == sqlite3.SQLITE_INSERT and table == "fills":
        return sqlite3.SQLITE_OK if current == "fill" else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_INSERT and table == "fill_audit":
        return sqlite3.SQLITE_OK if current == "audit" else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_INSERT and table == "review_meta":
        return sqlite3.SQLITE_OK if current == "init" else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_UPDATE and table == "fills":
        return sqlite3.SQLITE_OK if current == "settle" else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_UPDATE and table in ("fill_audit", "review_meta"):
        return sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_DELETE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_ALTER_TABLE):
        if table in ("fills", "fill_audit", "review_meta"):
            return sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX) and table in ("fills", "fill_audit", "review_meta", "sqlite_autoindex_fills_1"):
        return sqlite3.SQLITE_OK if current == "init" else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _check_path(path: str) -> str:
    real = os.path.realpath(path)
    for root in FORBIDDEN_ROOTS:
        if real == root or real.startswith(root + os.sep):
            raise LedgerRefusal(f"refusing ledger path under {root}")
    return real


def _existing_kind(path: str) -> str:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return "new"
    probe = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = {row[0] for row in probe.execute("select name from sqlite_master where type='table'")}
        if "fills" in names and "review_meta" not in names:
            return "foreign"
        if "review_meta" in names:
            row = probe.execute("select v from review_meta where k='schema_id'").fetchone()
            if not row or row[0] != SCHEMA_ID:
                return "foreign"
            return "review"
        if names - {"sqlite_sequence"}:
            return "foreign"
        return "new"
    finally:
        probe.close()


class GuardedConnection:
    def __init__(self, path: str):
        self.path = path
        self._raw = sqlite3.connect(path, timeout=5, check_same_thread=False, isolation_level=None)
        self._raw.row_factory = sqlite3.Row
        self._raw.set_authorizer(_authorizer)
        try:
            self._raw.enable_load_extension(False)
        except (AttributeError, sqlite3.Error):
            pass

    def execute(self, sql, params=()):
        if _protected_mutation(sql):
            self.record_blocked(sql)
            raise PermissionError("direct fill or audit mutation is not allowed")
        try:
            return self._raw.execute(sql, params)
        except sqlite3.DatabaseError as exc:
            if "not authorized" in str(exc).lower():
                self.record_blocked(sql)
                raise PermissionError("direct fill or audit mutation is not allowed") from exc
            raise

    def executemany(self, sql, seq):
        if _protected_mutation(sql):
            self.record_blocked(sql)
            raise PermissionError("direct fill or audit mutation is not allowed")
        return self._raw.executemany(sql, seq)

    def executescript(self, sql):
        self.record_blocked(sql)
        raise PermissionError("scripts are not allowed on the review ledger")

    def commit(self):
        if self._raw.in_transaction:
            self._raw.execute("COMMIT")

    def rollback(self):
        if self._raw.in_transaction:
            self._raw.execute("ROLLBACK")

    def close(self):
        self._raw.close()

    def record_blocked(self, sql: str):
        from .canonical import sha256_text
        from datetime import datetime
        from zoneinfo import ZoneInfo
        payload = sql[:500]
        digest = sha256_text(payload)
        try:
            with purpose("audit"):
                self._raw.execute(
                    """insert into fill_audit(
                        ts, action, reasons_json, fill_id, writer, caller, input_json, input_sha256,
                        snapshot_json, snapshot_sha256, decision_json, source_version, source_tree_sha256
                    ) values (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        datetime.now(ZoneInfo("America/Chicago")).isoformat(),
                        "fail_closed",
                        "[\"direct_insert_blocked\"]",
                        None,
                        None,
                        "direct",
                        payload,
                        digest,
                        None,
                        None,
                        "{\"reasons\":[\"direct_insert_blocked\"]}",
                        "review-poly-paper-20261108",
                        "blocked",
                    ),
                )
        except sqlite3.Error:
            return


def connect(path: str) -> GuardedConnection:
    real = _check_path(path)
    kind = _existing_kind(real)
    if kind == "foreign":
        raise LedgerRefusal("refusing to open a ledger that is not this review schema")
    parent = os.path.dirname(real)
    if parent:
        os.makedirs(parent, exist_ok=True)
    con = GuardedConnection(real)
    if kind == "new":
        with purpose("init"):
            con._raw.execute(_FILLS)
            con._raw.execute(_AUDIT)
            con._raw.execute(_META)
            con._raw.execute(
                "insert into review_meta(k, v) values('schema_id', ?)",
                (SCHEMA_ID,),
            )
    return con


@contextmanager
def transaction(con: GuardedConnection):
    con._raw.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        if con._raw.in_transaction:
            con._raw.execute("ROLLBACK")
        raise
    else:
        con._raw.execute("COMMIT")


@contextmanager
def locked(path: str):
    real = _check_path(path)
    lock = _lock_for(real)
    with lock:
        yield real


def _strip_sql(sql: str) -> str:
    import re
    text = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    text = re.sub(r"--[^\n]*", " ", text)
    text = text.replace('"', " ").replace("'", " ").replace("[", " ").replace("]", " ").replace("`", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def _protected_mutation(sql: str) -> bool:
    import re
    text = _strip_sql(sql)
    pattern = r"\b(insert|replace|update|delete|drop|alter)\b[\s\S]*\b(fills|fill_audit|review_meta)\b"
    if re.search(pattern, text):
        return True
    if re.search(r"\b(attach|detach)\b", text):
        return True
    return False
