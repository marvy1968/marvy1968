"""Read-only weekday evening summary. It does not settle open rows or call Telegram."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from .clock import as_ct, summary_slot

LEDGER = """
CREATE TABLE IF NOT EXISTS summary_slot (
  ct_date TEXT PRIMARY KEY,
  rendered_at TEXT NOT NULL,
  body TEXT NOT NULL
)
"""


def _ct_day(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        return ""
    return as_ct(parsed).date().isoformat()


def venue_day(rows: list[dict], ct_day: str) -> dict:
    """Fee-aware settled pnl. cost already includes the fee. Open rows are not marked to market."""
    settled = []
    unknown = []
    open_rows = []
    for row in rows:
        status = row.get("status")
        if status == "open" or row.get("settled_ts") in (None, ""):
            if status == "settled":
                unknown.append(row)
            else:
                open_rows.append(row)
            continue
        if _ct_day(row.get("settled_ts")) != ct_day:
            continue
        payout, cost = row.get("payout"), row.get("cost")
        if payout is None or cost is None:
            unknown.append(row)
            continue
        pnl = round(float(payout) - float(cost), 4)
        if row.get("pnl") is not None and round(float(row["pnl"]), 4) != pnl:
            unknown.append(row)
            continue
        settled.append({**row, "pnl": pnl})
    return {
        "record": _record(settled),
        "pnl": round(sum(row["pnl"] for row in settled), 4),
        "settled": len(settled),
        "open_cost": round(sum(float(row.get("cost") or 0) for row in open_rows), 4),
        "open_count": len(open_rows),
        "unknown_count": len(unknown),
        "open_is_mtm": False,
    }


def _record(rows: list[dict]) -> str:
    wins = sum(row.get("result") == "win" for row in rows)
    losses = sum(row.get("result") == "loss" for row in rows)
    pushes = sum(row.get("result") == "push" for row in rows)
    return f"{wins}-{losses}-{pushes}"


def render(con: sqlite3.Connection, *, now, poly_rows: list[dict], kalshi_rows: list[dict],
           scope: tuple[str, ...] = ("kalshi_demo", "polymarket_paper")) -> dict | None:
    if set(scope) != {"kalshi_demo", "polymarket_paper"}:
        raise ValueError("the daily record is Kalshi DEMO and Polymarket PAPER only")
    slot = summary_slot(now)
    if slot is None:
        return None
    con.execute(LEDGER)
    prior = con.execute("SELECT body FROM summary_slot WHERE ct_date=?", (slot,)).fetchone()
    if prior:
        return {"slot": slot, "created": False, "body": prior[0]}
    generated = as_ct(now).isoformat()
    poly = venue_day(poly_rows, slot)
    kalshi = venue_day(kalshi_rows, slot)
    body = "\n".join([
        f"Kalshi DEMO / Polymarket PAPER daily record {slot} 21:00 America/Chicago",
        f"generated {generated}",
        "labels: Kalshi DEMO, Polymarket PAPER. Read only. Not a live order.",
        f"Polymarket PAPER record {poly['record']} fee-aware pnl {poly['pnl']} settled {poly['settled']}",
        f"Polymarket PAPER open risk cost {poly['open_cost']} count {poly['open_count']} not a mark-to-market",
        f"Polymarket PAPER unknown finishes {poly['unknown_count']}",
        f"Kalshi DEMO record {kalshi['record']} fee-aware pnl {kalshi['pnl']} settled {kalshi['settled']}",
        f"Kalshi DEMO open risk cost {kalshi['open_cost']} count {kalshi['open_count']} not a mark-to-market",
        f"Kalshi DEMO unknown finishes {kalshi['unknown_count']}",
        "starting baseline UNKNOWN. 10000 is a configured risk bankroll, not a baseline. 2500 is not a verified Kalshi baseline.",
    ])
    try:
        con.execute("BEGIN IMMEDIATE")
        prior = con.execute("SELECT body FROM summary_slot WHERE ct_date=?", (slot,)).fetchone()
        if prior:
            con.commit()
            return {"slot": slot, "created": False, "body": prior[0]}
        con.execute("INSERT INTO summary_slot(ct_date, rendered_at, body) VALUES (?,?,?)", (slot, generated, body))
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback()
        body = con.execute("SELECT body FROM summary_slot WHERE ct_date=?", (slot,)).fetchone()[0]
        return {"slot": slot, "created": False, "body": body}
    return {"slot": slot, "created": True, "body": body}


RECEIPT = """
CREATE TABLE IF NOT EXISTS summary_receipt (
  ct_date TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  receipt TEXT
)
"""


def publish_summary(con: sqlite3.Connection, *, now, poly_rows: list[dict], kalshi_rows: list[dict], transport) -> dict:
    """One local claim per Central date. A lost Telegram response is not a receipt and is not retried."""
    from .delivery import Receipt
    rendered = render(con, now=now, poly_rows=poly_rows, kalshi_rows=kalshi_rows)
    if rendered is None:
        return {"status": "not_due", "posted": False, "delivery_proven": False, "exactly_once_telegram": False}
    con.execute(RECEIPT)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT state, receipt FROM summary_receipt WHERE ct_date=?",
            (rendered["slot"],),
        ).fetchone()
        if row is not None:
            con.commit()
            proven = row[0] == "delivered" and row[1] == "http_200"
            return {"status": row[0], "posted": False, "delivery_proven": proven,
                    "exactly_once_telegram": False, "body": rendered["body"]}
        con.execute(
            "INSERT INTO summary_receipt(ct_date, state, receipt) VALUES (?,?,NULL)",
            (rendered["slot"], "ambiguous"),
        )
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback()
        return {"status": "ambiguous", "posted": False, "delivery_proven": False, "exactly_once_telegram": False}
    try:
        receipt = transport(rendered["body"])
    except Exception:
        return {"status": "ambiguous", "posted": True, "delivery_proven": False, "exactly_once_telegram": False,
                "receipt": None}
    if not isinstance(receipt, Receipt) or receipt.code == "unknown":
        return {"status": "ambiguous", "posted": True, "delivery_proven": False, "exactly_once_telegram": False,
                "receipt": None}
    if receipt.delivered:
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE summary_receipt SET state='delivered', receipt=? WHERE ct_date=? AND state='ambiguous'",
                (receipt.code, rendered["slot"]),
            )
            con.commit()
        except sqlite3.Error:
            con.rollback()
            return {"status": "ambiguous", "posted": True, "delivery_proven": False, "exactly_once_telegram": False,
                    "receipt": None}
        return {"status": "delivered", "posted": True, "delivery_proven": True, "exactly_once_telegram": False,
                "body": rendered["body"], "receipt": receipt.code}
    con.execute(
        "UPDATE summary_receipt SET state='failed', receipt=? WHERE ct_date=?",
        (receipt.code, rendered["slot"]),
    )
    con.commit()
    return {"status": "failed", "posted": True, "delivery_proven": False, "exactly_once_telegram": False,
            "receipt": receipt.code}


def load_policy(path) -> dict:
    return json.loads(path.read_text())
