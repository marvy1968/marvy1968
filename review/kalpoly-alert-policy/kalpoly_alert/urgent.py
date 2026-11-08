"""Urgent notices bypass weekday suppression. A dead process cannot send one."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .clock import as_ct
from .delivery import Receipt

URGENT = """
CREATE TABLE IF NOT EXISTS urgent_notice (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  dedupe_key TEXT NOT NULL UNIQUE,
  decided_at TEXT NOT NULL,
  status TEXT NOT NULL,
  receipt TEXT
)
"""


@dataclass(frozen=True)
class UrgentOutcome:
    posted: bool
    delivered: bool
    status: str


def notify(con: sqlite3.Connection, *, now, kind: str, dedupe_key: str, transport) -> UrgentOutcome:
    con.execute(URGENT)
    row = con.execute("SELECT status, receipt FROM urgent_notice WHERE dedupe_key=?", (dedupe_key,)).fetchone()
    if row and row[0] == "delivered":
        return UrgentOutcome(False, True, "delivered")
    decided = as_ct(now).isoformat()
    if row is None:
        con.execute(
            "INSERT INTO urgent_notice(kind, dedupe_key, decided_at, status, receipt) VALUES (?,?,?,?,NULL)",
            (kind, dedupe_key, decided, "pending"),
        )
        con.commit()
    receipt = transport(kind)
    if not isinstance(receipt, Receipt):
        receipt = Receipt("unknown")
    status = "delivered" if receipt.delivered else ("unknown" if receipt.code == "unknown" else "failed")
    con.execute(
        "UPDATE urgent_notice SET status=?, receipt=? WHERE dedupe_key=?",
        (status, receipt.code, dedupe_key),
    )
    con.commit()
    return UrgentOutcome(True, receipt.delivered, status)
