"""Proposed loss notices. Disabled unless a review explicitly turns them on.

The 5 percent steps are a Monitor proposal, not a user-approved live threshold.
No stake, bankroll, or risk stop is changed here.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .summary import venue_day

NOTICE = """
CREATE TABLE IF NOT EXISTS loss_notice (
  dedupe_key TEXT PRIMARY KEY,
  ct_date TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL
)
"""


def evaluate(con: sqlite3.Connection, *, now, policy: dict, venue: str, rows: list[dict], day_opening) -> list[dict]:
    if not policy.get("enabled"):
        return []
    from .clock import ct_date
    day = ct_date(now).isoformat()
    book = venue_day(rows, day)
    notices = []
    con.execute(NOTICE)
    base = policy.get("baseline_bankroll", {}).get(venue, "UNKNOWN")
    opening_known = day_opening not in (None, "UNKNOWN")
    baseline_known = base not in (None, "UNKNOWN")
    if not opening_known:
        notices.append(_once(con, day, venue, "day_opening_unknown", "day opening bankroll UNKNOWN"))
    else:
        step = float(policy.get("day_loss_fraction_of_opening", 0.05))
        loss = -book["pnl"] if book["pnl"] < 0 else 0.0
        ratio = loss / float(day_opening) if day_opening else 0.0
        bands = 0
        while step > 0 and ratio + 1e-12 >= step * (bands + 1):
            bands += 1
        for band in range(1, bands + 1):
            notices.append(_once(
                con, day, venue, f"day_loss_{band}",
                f"proposal only: day loss crossed {band * step:.0%} of the verified day opening",
            ))
    if not baseline_known or not opening_known:
        notices.append(_once(con, day, venue, "baseline_unknown", "baseline bankroll UNKNOWN"))
    else:
        opening = float(day_opening)
        settled_now = opening + book["pnl"]
        if opening >= float(base) and settled_now < float(base):
            notices.append(_once(con, day, venue, "baseline_red", "settled bankroll crossed below the verified baseline"))
        elif opening < float(base):
            notices.append(_once(con, day, venue, "already_below_at_open", "day opened already below baseline; not a new crossing"))
    con.commit()
    return [note for note in notices if note]
def _once(con, day, venue, kind, detail):
    key = f"{day}:{venue}:{kind}"
    if con.execute("SELECT 1 FROM loss_notice WHERE dedupe_key=?", (key,)).fetchone():
        return None
    con.execute(
        "INSERT INTO loss_notice(dedupe_key, ct_date, kind, detail) VALUES (?,?,?,?)",
        (key, day, kind, detail),
    )
    return {"venue": venue, "kind": kind, "detail": detail}


def read_policy(path: Path) -> dict:
    return json.loads(path.read_text())
