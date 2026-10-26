#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import sqlite3
from datetime import datetime
from typing import Any

DB_PATH = os.getenv("PERF_DB_PATH", "performance_tracking.db")
CSV_PATH = os.getenv("PERF_CSV_PATH", "logs/performance_tracker.csv")
JSONL_PATH = os.getenv("PERF_JSONL_PATH", "logs/performance_tracker.jsonl")


def _ensure_parent(path: str) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tracked_bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_utc TEXT,
            game TEXT,
            market TEXT,
            selection TEXT,
            strength TEXT,
            bot_win_pct REAL,
            book_win_pct REAL,
            display_edge REAL,
            ev_percent REAL,
            confidence REAL,
            kelly_quarter REAL,
            recommended_bankroll_pct REAL,
            leader TEXT,
            leader_win_prob REAL,
            minutes_left REAL,
            seconds_left REAL,
            score_diff REAL,
            pace TEXT,
            pressure TEXT,
            suspicious INTEGER,
            tag TEXT,
            result TEXT DEFAULT 'pending',
            profit_units REAL DEFAULT 0.0,
            final_home_score REAL DEFAULT NULL,
            final_away_score REAL DEFAULT NULL,
            settled_at_utc TEXT DEFAULT NULL
        )
        """
    )
    conn.commit()
    return conn


def record_tracked_bet(signal: dict[str, Any], tag: str = "standard") -> None:
    record = {
        "timestamp_utc": datetime.utcnow().isoformat(),
        "game": signal.get("game", ""),
        "market": signal.get("market", ""),
        "selection": signal.get("selection", ""),
        "strength": signal.get("strength", ""),
        "bot_win_pct": round(_to_float(signal.get("bot_win_pct", 0.0)), 2),
        "book_win_pct": round(_to_float(signal.get("book_win_pct", 0.0)), 2),
        "display_edge": round(_to_float(signal.get("display_edge", 0.0)), 2),
        "ev_percent": round(_to_float(signal.get("ev_percent", 0.0)), 2),
        "confidence": round(_to_float(signal.get("confidence", 0.0)), 4),
        "kelly_quarter": round(_to_float(signal.get("kelly_quarter", 0.0)), 4),
        "recommended_bankroll_pct": round(_to_float(signal.get("recommended_bankroll_pct", 0.0)), 2),
        "leader": signal.get("leader", ""),
        "leader_win_prob": round(_to_float(signal.get("leader_win_prob", 0.0)), 4),
        "minutes_left": _to_float(signal.get("minutes_left", 0.0)),
        "seconds_left": _to_float(signal.get("seconds_left", 0.0)),
        "score_diff": _to_float(signal.get("score_diff", 0.0)),
        "pace": signal.get("pace", ""),
        "pressure": signal.get("pressure", ""),
        "suspicious": 1 if bool(signal.get("suspicious", False)) else 0,
        "tag": tag,
    }

    conn = _connect()
    conn.execute(
        """
        INSERT INTO tracked_bets (
            timestamp_utc, game, market, selection, strength,
            bot_win_pct, book_win_pct, display_edge, ev_percent,
            confidence, kelly_quarter, recommended_bankroll_pct,
            leader, leader_win_prob, minutes_left, seconds_left,
            score_diff, pace, pressure, suspicious, tag
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["timestamp_utc"], record["game"], record["market"], record["selection"], record["strength"],
            record["bot_win_pct"], record["book_win_pct"], record["display_edge"], record["ev_percent"],
            record["confidence"], record["kelly_quarter"], record["recommended_bankroll_pct"],
            record["leader"], record["leader_win_prob"], record["minutes_left"], record["seconds_left"],
            record["score_diff"], record["pace"], record["pressure"], record["suspicious"], record["tag"],
        ),
    )
    conn.commit()
    conn.close()

    _ensure_parent(JSONL_PATH)
    with open(JSONL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    _ensure_parent(CSV_PATH)
    file_exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(record.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(record)


def settle_bet(
    bet_id: int,
    result: str,
    profit_units: float,
    final_home_score: float | None = None,
    final_away_score: float | None = None,
) -> None:
    result = str(result).lower().strip()
    if result not in {"win", "loss", "push"}:
        raise ValueError("result must be win, loss, or push")

    conn = _connect()
    conn.execute(
        """
        UPDATE tracked_bets
        SET result = ?, profit_units = ?, final_home_score = ?, final_away_score = ?, settled_at_utc = ?
        WHERE id = ?
        """,
        (result, profit_units, final_home_score, final_away_score, datetime.utcnow().isoformat(), bet_id),
    )
    conn.commit()
    conn.close()


def summary() -> dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM tracked_bets")
    total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM tracked_bets WHERE result IN ('win','loss','push')")
    graded = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM tracked_bets WHERE result = 'win'")
    wins = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM tracked_bets WHERE result = 'loss'")
    losses = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM tracked_bets WHERE result = 'push'")
    pushes = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(profit_units), 0.0) FROM tracked_bets WHERE result IN ('win','loss','push')")
    profit_units = cur.fetchone()[0] or 0.0

    conn.close()

    decided = wins + losses
    win_rate = round((wins / decided) * 100.0, 2) if decided > 0 else 0.0

    return {
        "total_tracked": total,
        "graded": graded,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": win_rate,
        "profit_units": round(profit_units, 2),
    }


if __name__ == "__main__":
    print("PERFORMANCE TRACKER SUMMARY")
    print(summary())
