"""Separate a gate decision from a Telegram receipt.

sent=1 is written only after a transport receipt of http_200 is committed.
A weekday skip does not write sent=1 and does not enter the outbox.
A gate rejection is not feed-eligible.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .clock import as_ct, is_weekend

ELIGIBILITY = """
CREATE TABLE IF NOT EXISTS feed_eligibility (
  alert_id INTEGER PRIMARY KEY,
  decided_at TEXT NOT NULL,
  status TEXT NOT NULL,
  receipt TEXT
)
"""


@dataclass(frozen=True)
class Receipt:
    """What the transport actually observed. unknown is not a delivery."""

    code: str  # http_200, http_error, unknown

    @property
    def delivered(self) -> bool:
        return self.code == "http_200"


@dataclass(frozen=True)
class Outcome:
    eligible: bool
    sent: bool
    status: str
    posted: bool
    enqueued: bool


def ensure(con: sqlite3.Connection) -> None:
    con.execute(ELIGIBILITY)


def _status(con: sqlite3.Connection, alert_id: int) -> str | None:
    row = con.execute("SELECT status FROM feed_eligibility WHERE alert_id=?", (alert_id,)).fetchone()
    return None if row is None else row[0]


def _sent(con: sqlite3.Connection, alert_id: int) -> int:
    row = con.execute("SELECT sent FROM alerts WHERE id=?", (alert_id,)).fetchone()
    return 0 if row is None or row[0] is None else int(row[0])


def deliver_ordinary(con: sqlite3.Connection, *, now, alert_id: int, gates_passed: bool, transport) -> Outcome:
    """Post-gate tail of scanner.py and value_scan.py.

    gates_passed is true only after the source send() gates (track-only, league,
    veto, double-dip) have allowed a post. Holds, demotions, pauses, and dry runs
    pass gates_passed false and stay out of the feed.
    """
    ensure(con)
    if not gates_passed:
        return Outcome(False, _sent(con, alert_id) == 1, "not_eligible", False, False)
    existing = _status(con, alert_id)
    if existing in ("suppressed_weekday", "delivered", "failed", "unknown"):
        return Outcome(True, _sent(con, alert_id) == 1, existing, False, False)
    decided = as_ct(now).isoformat()
    if existing is None:
        con.execute("BEGIN")
        con.execute(
            "INSERT INTO feed_eligibility(alert_id, decided_at, status, receipt) VALUES (?,?,?,NULL)",
            (alert_id, decided, "pending"),
        )
        con.commit()
    if not is_weekend(now):
        con.execute(
            "UPDATE feed_eligibility SET status=?, receipt=NULL WHERE alert_id=? AND status='pending'",
            ("suppressed_weekday", alert_id),
        )
        con.commit()
        return Outcome(True, False, "suppressed_weekday", False, False)
    receipt = transport(alert_id)
    if not isinstance(receipt, Receipt):
        receipt = Receipt("unknown")
    if receipt.delivered:
        try:
            con.execute("BEGIN")
            con.execute("UPDATE alerts SET sent=1 WHERE id=?", (alert_id,))
            con.execute(
                "UPDATE feed_eligibility SET status='delivered', receipt=? WHERE alert_id=?",
                (receipt.code, alert_id),
            )
            con.commit()
        except sqlite3.Error:
            con.rollback()
            return Outcome(True, False, "pending", True, False)
        return Outcome(True, True, "delivered", True, False)
    status = "unknown" if receipt.code == "unknown" else "failed"
    con.execute("BEGIN")
    con.execute(
        "UPDATE feed_eligibility SET status=?, receipt=? WHERE alert_id=?",
        (status, receipt.code, alert_id),
    )
    con.execute(
        "INSERT INTO outbox(ts, chat_id, text, html, alert_ids, status) VALUES (?,?,?,?,?, 'pending')",
        (decided, "primary", "", 0, json.dumps([alert_id])),
    )
    con.commit()
    return Outcome(True, False, status, True, True)


def commit_retry_receipt(con: sqlite3.Connection, *, alert_id: int, receipt: Receipt) -> Outcome:
    """Outbox retry. sent=1 only when this attempt's receipt is http_200."""
    ensure(con)
    if _status(con, alert_id) is None:
        return Outcome(False, False, "not_eligible", False, False)
    if not receipt.delivered:
        status = "unknown" if receipt.code == "unknown" else "failed"
        con.execute(
            "UPDATE feed_eligibility SET status=?, receipt=? WHERE alert_id=? AND status!='delivered'",
            (status, receipt.code, alert_id),
        )
        con.commit()
        return Outcome(True, _sent(con, alert_id) == 1, status, True, False)
    con.execute("BEGIN")
    con.execute("UPDATE alerts SET sent=1 WHERE id=?", (alert_id,))
    con.execute(
        "UPDATE feed_eligibility SET status='delivered', receipt=? WHERE alert_id=?",
        (receipt.code, alert_id),
    )
    con.commit()
    return Outcome(True, True, "delivered", True, False)


def backing_ids(con: sqlite3.Connection, event_id: str, except_ids: tuple[int, ...]) -> list[int]:
    """Same-side protection sees a gate-passed alert, not only a delivered one."""
    ensure(con)
    marks = ",".join("?" * len(except_ids)) or "NULL"
    rows = con.execute(
        f"""SELECT id FROM alerts WHERE event_id=? AND id NOT IN ({marks})
            AND (sent=1 OR id IN (SELECT alert_id FROM feed_eligibility))""",
        (event_id, *except_ids),
    ).fetchall()
    return [row[0] for row in rows]
