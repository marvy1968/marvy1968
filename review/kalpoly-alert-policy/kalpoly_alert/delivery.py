"""Receipts are not feed eligibility, and a caller flag is not a gate.

sent=1 stays the column the exported source writes only after send() returns
true. This review cannot call send(), so it does not write sent=1. Rows that
are held, demoted, vetoed, dry-run, or double-dipped are rejections. A missing
gate module is a different status and is not eligible.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .clock import as_ct

ELIGIBILITY = """
CREATE TABLE IF NOT EXISTS feed_eligibility (
  alert_id INTEGER PRIMARY KEY,
  decided_at TEXT NOT NULL,
  status TEXT NOT NULL,
  gate_proof TEXT NOT NULL,
  receipt TEXT,
  CHECK (gate_proof = 'not_executed'),
  CHECK (status IN (
    'held', 'demoted', 'vetoed', 'dry_run', 'double_dip', 'missing_dependency',
    'failed', 'unknown', 'pending'
  ))
)
"""

REJECTIONS = frozenset({"held", "demoted", "vetoed", "dry_run", "double_dip"})


@dataclass(frozen=True)
class Receipt:
    code: str

    @property
    def delivered(self) -> bool:
        return self.code == "http_200"


@dataclass(frozen=True)
class Outcome:
    eligible: bool
    sent: bool
    status: str
    posted: bool


def ensure(con: sqlite3.Connection) -> None:
    con.execute(ELIGIBILITY)


def _sent(con: sqlite3.Connection, alert_id: int) -> int:
    row = con.execute("SELECT sent FROM alerts WHERE id=?", (alert_id,)).fetchone()
    return 0 if row is None or row[0] is None else int(row[0])


def record_disposition(con: sqlite3.Connection, *, now, alert_id: int, status: str) -> Outcome:
    """Record a source-visible non-eligibility. Cannot record a pass."""
    if status not in REJECTIONS and status != "missing_dependency":
        raise ValueError("only a rejection or missing_dependency can be recorded")
    ensure(con)
    current = con.execute("SELECT status FROM feed_eligibility WHERE alert_id=?", (alert_id,)).fetchone()
    if current is None:
        con.execute(
            "INSERT INTO feed_eligibility(alert_id, decided_at, status, gate_proof, receipt) VALUES (?,?,?,?,NULL)",
            (alert_id, as_ct(now).isoformat(), status, "not_executed"),
        )
        con.commit()
    return Outcome(False, _sent(con, alert_id) == 1, current[0] if current else status, False)


def note_transport_without_send(con: sqlite3.Connection, *, now, alert_id: int, receipt: Receipt) -> Outcome:
    """A transport result without send() does not become sent=1 or eligibility."""
    record_disposition(con, now=now, alert_id=alert_id, status="missing_dependency")
    observed = "http_200" if receipt.delivered else ("unknown" if receipt.code == "unknown" else "failed")
    con.execute(
        "UPDATE feed_eligibility SET receipt=? WHERE alert_id=? AND status='missing_dependency'",
        (observed, alert_id),
    )
    con.commit()
    return Outcome(False, _sent(con, alert_id) == 1, "missing_dependency", False)


def backing_ids(con: sqlite3.Connection, event_id: str, except_ids: tuple[int, ...]) -> list[int]:
    """The exported double-dip query uses sent=1 only. Unproven rows do not count."""
    if except_ids:
        marks = ",".join("?" * len(except_ids))
        rows = con.execute(
            f"SELECT id FROM alerts WHERE event_id=? AND sent=1 AND id NOT IN ({marks})",
            (event_id, *except_ids),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT id FROM alerts WHERE event_id=? AND sent=1",
            (event_id,),
        ).fetchall()
    return [row[0] for row in rows]
