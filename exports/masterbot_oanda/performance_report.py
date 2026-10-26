#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3

DB_PATH = os.getenv("PERF_DB_PATH", "performance_tracking.db")


def run_report() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    print("=== PERFORMANCE REPORT ===")

    cur.execute("""
        SELECT market,
               COUNT(*) as n,
               SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
               ROUND(COALESCE(SUM(profit_units),0), 2) as units
        FROM tracked_bets
        WHERE result IN ('win','loss','push')
        GROUP BY market
        ORDER BY units DESC, n DESC
    """)
    rows = cur.fetchall()

    if not rows:
        print("No graded bets yet")
    else:
        for market, n, wins, losses, units in rows:
            decided = (wins or 0) + (losses or 0)
            wr = round((wins / decided) * 100.0, 2) if decided else 0.0
            print(f"{market}: n={n} wins={wins} losses={losses} win_rate={wr}% units={units}")

    print("\n=== AJ EDGE REPORT ===")
    cur.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN result='win' THEN 1 ELSE 0 END),
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END),
               ROUND(COALESCE(SUM(profit_units),0), 2)
        FROM tracked_bets
        WHERE tag='aj_edge' AND result IN ('win','loss','push')
    """)
    n, wins, losses, units = cur.fetchone()
    decided = (wins or 0) + (losses or 0)
    wr = round((wins / decided) * 100.0, 2) if decided else 0.0
    print(f"aj_edge: n={n} wins={wins} losses={losses} win_rate={wr}% units={units}")

    conn.close()


if __name__ == "__main__":
    run_report()
