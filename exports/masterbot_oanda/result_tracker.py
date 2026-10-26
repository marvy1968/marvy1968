#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

DB_FILE = "tracking.db"


def init_db() -> None:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game TEXT,
        market TEXT,
        pick TEXT,
        line REAL,
        odds REAL,
        confidence REAL,
        edge REAL,
        strength TEXT,
        timestamp TEXT,
        result TEXT,
        profit REAL,
        final_home_score INTEGER,
        final_away_score INTEGER
    )
    """)

    conn.commit()
    conn.close()


def _table_columns() -> set[str]:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(bets)")
    cols = {row[1] for row in cur.fetchall()}
    conn.close()
    return cols


def upgrade_schema() -> None:
    init_db()
    cols = _table_columns()

    wanted = {
        "confidence": "REAL",
        "edge": "REAL",
        "strength": "TEXT",
        "final_home_score": "INTEGER",
        "final_away_score": "INTEGER",
    }

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    for col, col_type in wanted.items():
        if col not in cols:
            cur.execute(f"ALTER TABLE bets ADD COLUMN {col} {col_type}")

    conn.commit()
    conn.close()


def record_bet(
    game: str,
    market: str,
    pick: str,
    line: float,
    odds: float,
    confidence: float = 0.0,
    edge: float = 0.0,
    strength: str = "D",
) -> None:
    upgrade_schema()

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO bets (
        game, market, pick, line, odds, confidence, edge, strength,
        timestamp, result, profit, final_home_score, final_away_score
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        game,
        market,
        pick,
        line,
        odds,
        confidence,
        edge,
        strength,
        datetime.utcnow().isoformat(),
        "pending",
        0.0,
        None,
        None,
    ))

    conn.commit()
    conn.close()


def grade_bet(
    bet_id: int,
    win: bool,
    final_home_score: Optional[int] = None,
    final_away_score: Optional[int] = None,
) -> None:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    result = "win" if win else "loss"
    profit = 1.0 if win else -1.0

    cur.execute("""
    UPDATE bets
    SET result=?, profit=?, final_home_score=?, final_away_score=?
    WHERE id=?
    """, (result, profit, final_home_score, final_away_score, bet_id))

    conn.commit()
    conn.close()


def stats() -> None:
    upgrade_schema()

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM bets WHERE result != 'pending'")
    total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM bets WHERE result='win'")
    wins = cur.fetchone()[0]

    cur.execute("SELECT SUM(profit) FROM bets WHERE result != 'pending'")
    units = cur.fetchone()[0] or 0.0

    print("=== RESULT TRACKER STATS ===")
    print("graded_bets:", total)
    print("wins:", wins)
    print("losses:", max(0, total - wins))
    print("win_rate:", round((wins / total) * 100.0, 2) if total else 0.0)
    print("net_units:", round(units, 2))

    print("\n--- by market ---")
    cur.execute("""
    SELECT market,
           COUNT(*) as n,
           SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins,
           SUM(CASE WHEN result!='pending' THEN profit ELSE 0 END) as units
    FROM bets
    WHERE result != 'pending'
    GROUP BY market
    ORDER BY n DESC
    """)
    rows = cur.fetchall()
    for market, n, mwins, munits in rows:
        wr = (mwins / n) * 100.0 if n else 0.0
        print(f"{market}: bets={n} wins={mwins} win_rate={round(wr,2)} net_units={round(munits or 0.0,2)}")

    conn.close()


if __name__ == "__main__":
    upgrade_schema()
    stats()

