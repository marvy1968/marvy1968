"""SQLite order lifecycle log. Runs beside the JSON state file. No network sends."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


class Journal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_events (
                id INTEGER PRIMARY KEY,
                ts TEXT NOT NULL,
                client_order_id TEXT,
                order_id TEXT,
                symbol TEXT,
                mode TEXT,
                expert TEXT,
                event TEXT,
                status TEXT,
                qty REAL,
                price REAL,
                fill_price REAL,
                slippage REAL,
                detail TEXT,
                config_hash TEXT
            )
            """
        )
        self.conn.commit()

    def event(
        self,
        event: str,
        *,
        ts: datetime | None = None,
        client_order_id: str = "",
        order_id: str = "",
        symbol: str = "",
        mode: str = "",
        expert: str = "",
        status: str = "",
        qty: float | None = None,
        price: float | None = None,
        fill_price: float | None = None,
        slippage: float | None = None,
        detail: str = "",
        config_hash: str = "",
    ) -> None:
        stamp = (ts or datetime.now()).isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO order_events (
                ts, client_order_id, order_id, symbol, mode, expert, event, status,
                qty, price, fill_price, slippage, detail, config_hash
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                stamp,
                client_order_id,
                order_id,
                symbol,
                mode,
                expert,
                event,
                status,
                qty,
                price,
                fill_price,
                slippage,
                detail,
                config_hash,
            ),
        )
        self.conn.commit()

    def count(self, event: str | None = None) -> int:
        if event is None:
            row = self.conn.execute("SELECT COUNT(*) FROM order_events").fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM order_events WHERE event = ?", (event,)).fetchone()
        return int(row[0])

    def close(self) -> None:
        self.conn.close()


def slippage(side: str, expected: float | None, fill: float | None) -> float | None:
    """Positive slippage is adverse."""
    if expected is None or fill is None:
        return None
    if side in ("buy", "long"):
        return fill - expected
    return expected - fill
