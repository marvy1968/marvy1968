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


def render(con: sqlite3.Connection, *, now, poly_rows: list[dict], kalshi_rows: list[dict]) -> dict | None:
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
    con.execute("INSERT INTO summary_slot(ct_date, rendered_at, body) VALUES (?,?,?)", (slot, generated, body))
    con.commit()
    return {"slot": slot, "created": True, "body": body}


def load_policy(path) -> dict:
    return json.loads(path.read_text())
