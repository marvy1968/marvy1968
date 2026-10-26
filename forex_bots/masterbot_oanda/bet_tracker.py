#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path("marchedge_tracking.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_tracking_db() -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tracked_bets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_placed TEXT,
        game_id TEXT,
        game TEXT,
        sport_key TEXT,
        engine TEXT,
        market_type TEXT,
        selection TEXT,
        side TEXT,
        line REAL,
        price REAL,
        proj REAL,
        edge REAL,
        confidence REAL,
        kelly REAL,
        tier TEXT,
        quality_score REAL,
        calibration_version TEXT,
        filter_version TEXT,
        bet_strength TEXT,
        status TEXT DEFAULT 'open',
        result TEXT,
        pnl_units REAL DEFAULT 0.0,
        meta_json TEXT
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_tracked_bets_open
    ON tracked_bets(status, sport_key, game_id)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_tracked_bets_game
    ON tracked_bets(game_id, market_type, selection)
    """)

    conn.commit()
    conn.close()


def _infer_side(selection: str) -> str:
    s = (selection or "").lower()
    if " under " in f" {s} " or s.startswith("under "):
        return "under"
    if " over " in f" {s} " or s.startswith("over "):
        return "over"
    return selection or ""


def _extract_line(signal: dict[str, Any]) -> float | None:
    for key in ("market_line", "line", "point"):
        val = signal.get(key)
        if val is not None:
            try:
                return float(val)
            except Exception:
                return None

    selection = str(signal.get("selection", ""))
    parts = selection.replace("|", " ").split()
    for tok in reversed(parts):
        try:
            return float(tok)
        except Exception:
            continue
    return None


def track_bets(signals: list[dict[str, Any]], sport_key: str) -> int:
    if not signals:
        return 0

    conn = get_conn()
    cur = conn.cursor()
    inserted = 0

    for s in signals:
        ts_placed = str(s.get("ts_placed") or s.get("timestamp") or "")
        game_id = str(s.get("game_id", ""))
        game = str(s.get("game") or s.get("matchup") or game_id)
        engine = str(s.get("engine", "main"))
        market_type = str(s.get("market_type", ""))
        selection = str(s.get("selection", ""))
        side = str(s.get("side") or _infer_side(selection))
        line = _extract_line(s)
        price = s.get("price")
        proj = s.get("proj", s.get("model_line"))
        edge = s.get("edge")
        confidence = s.get("confidence")
        kelly = s.get("kelly")
        tier = s.get("tier")
        quality_score = s.get("quality_score")
        calibration_version = s.get("calibration_version")
        filter_version = s.get("filter_version")
        bet_strength = s.get("bet_strength")

        # avoid duplicate open tickets for same exact market snapshot
        cur.execute("""
            SELECT id FROM tracked_bets
            WHERE status = 'open'
              AND game_id = ?
              AND market_type = ?
              AND selection = ?
              AND COALESCE(line, -9999) = COALESCE(?, -9999)
            LIMIT 1
        """, (game_id, market_type, selection, line))
        exists = cur.fetchone()
        if exists:
            continue

        cur.execute("""
            INSERT INTO tracked_bets (
                ts_placed, game_id, game, sport_key, engine, market_type,
                selection, side, line, price, proj, edge, confidence, kelly,
                tier, quality_score, calibration_version, filter_version,
                bet_strength, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ts_placed, game_id, game, sport_key, engine, market_type,
            selection, side, line, price, proj, edge, confidence, kelly,
            tier, quality_score, calibration_version, filter_version,
            bet_strength, json.dumps(s, ensure_ascii=False)
        ))
        inserted += 1

    conn.commit()
    conn.close()
    return inserted


def fetch_open_bets(sport_key: str | None = None) -> list[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()

    if sport_key:
        cur.execute("""
            SELECT * FROM tracked_bets
            WHERE status = 'open' AND sport_key = ?
            ORDER BY id ASC
        """, (sport_key,))
    else:
        cur.execute("""
            SELECT * FROM tracked_bets
            WHERE status = 'open'
            ORDER BY id ASC
        """)

    rows = cur.fetchall()
    conn.close()
    return rows


def settle_bet(
    bet_id: int,
    result: str,
    pnl_units: float,
) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE tracked_bets
        SET status = 'settled',
            result = ?,
            pnl_units = ?
        WHERE id = ?
    """, (result, pnl_units, bet_id))
    conn.commit()
    conn.close()


def summary_report() -> dict[str, Any]:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN result = 'push' THEN 1 ELSE 0 END) AS pushes,
            ROUND(SUM(pnl_units), 3) AS pnl_units
        FROM tracked_bets
        WHERE status = 'settled'
    """)
    overall = dict(cur.fetchone())

    cur.execute("""
        SELECT
            engine,
            COUNT(*) AS total,
            SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN result = 'push' THEN 1 ELSE 0 END) AS pushes,
            ROUND(SUM(pnl_units), 3) AS pnl_units
        FROM tracked_bets
        WHERE status = 'settled'
        GROUP BY engine
        ORDER BY total DESC
    """)
    by_engine = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT
            sport_key,
            COUNT(*) AS total,
            SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN result = 'push' THEN 1 ELSE 0 END) AS pushes,
            ROUND(SUM(pnl_units), 3) AS pnl_units
        FROM tracked_bets
        WHERE status = 'settled'
        GROUP BY sport_key
        ORDER BY total DESC
    """)
    by_sport = [dict(r) for r in cur.fetchall()]

    conn.close()

    total = overall.get("total") or 0
    wins = overall.get("wins") or 0
    losses = overall.get("losses") or 0
    pushes = overall.get("pushes") or 0
    graded = wins + losses
    win_rate = round((wins / graded) * 100.0, 2) if graded else 0.0

    overall["win_rate"] = win_rate
    overall["graded"] = graded
    overall["pushes"] = pushes

    return {
        "overall": overall,
        "by_engine": by_engine,
        "by_sport": by_sport,
    }


if __name__ == "__main__":
    init_tracking_db()
    print("tracking db ready:", DB_PATH)
