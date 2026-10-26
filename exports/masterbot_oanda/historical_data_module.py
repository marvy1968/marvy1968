#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

PERF_DB = Path(os.getenv("PERF_DB", "performance_tracking.db"))
CSV_PATH = Path(os.getenv("HISTORICAL_CSV", "historical_prop_studies.csv"))
JSON_ALERTS_PATH = Path(os.getenv("HISTORICAL_ALERTS_JSON", "logs/alerted_bets.json"))
DEFAULT_SOURCE_WEIGHT = float(os.getenv("HISTORICAL_SOURCE_WEIGHT", "1.0"))


def norm(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().split())


def to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS historical_bets (
            id TEXT PRIMARY KEY,
            event_date TEXT,
            sport_key TEXT,
            market TEXT,
            subject_name TEXT,
            line REAL,
            odds REAL,
            confidence REAL,
            edge REAL,
            result TEXT,
            profit_units REAL,
            source TEXT,
            source_weight REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS historical_seen_alerts (
            id TEXT PRIMARY KEY,
            bet_key TEXT,
            sport_key TEXT,
            timestamp_utc TEXT,
            data_source TEXT
        )
        """
    )
    conn.commit()


def standardize_sport_key(s: str) -> str:
    x = norm(s)
    mapping = {
        "basketball_nba": "basketball_nba",
        "basketball_ncaab": "basketball_ncaab",
        "basketball_wncaab": "basketball_wncaab",
        "basketball_wnba": "basketball_wnba",
        "americanfootball_nfl": "american_football_nfl",
        "american_football_nfl": "american_football_nfl",
        "icehockey_nhl": "ice_hockey_nhl",
        "ice_hockey_nhl": "ice_hockey_nhl",
    }
    return mapping.get(x, x or "unknown")


def standardize_market(m: str) -> str:
    x = norm(m)
    mapping = {
        "player_pass_yds": "player_passing_yards",
        "player_passing_yds": "player_passing_yards",
        "player_rush_yds": "player_rushing_yards",
        "player_reception_yds": "player_receiving_yards",
    }
    return mapping.get(x, x or "unknown")


def normalize_result(r: str) -> str:
    x = norm(r).upper()
    if x in {"WIN", "LOSS", "PUSH"}:
        return x
    return "UNKNOWN"


def profit_units(result: str, odds: float) -> float:
    result = normalize_result(result)
    if result == "WIN":
        return round(odds - 1.0, 4)
    if result == "LOSS":
        return -1.0
    if result == "PUSH":
        return 0.0
    return 0.0


def historical_id(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("date", "")).strip(),
        standardize_sport_key(str(row.get("sport", ""))),
        standardize_market(str(row.get("market", ""))),
        str(row.get("subject_name", "")).strip(),
        str(row.get("line", "")).strip(),
    ]
    return "HIST|" + "|".join(parts)


def ingest_csv(conn: sqlite3.Connection) -> int:
    if not CSV_PATH.exists():
        print(f"missing csv: {CSV_PATH}")
        return 0

    count = 0
    with CSV_PATH.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            if not raw:
                continue

            date = str(raw.get("date", "")).strip()
            sport_key = standardize_sport_key(str(raw.get("sport", "")))
            market = standardize_market(str(raw.get("market", "")))
            subject_name = str(raw.get("subject_name", raw.get("player", raw.get("selection", "")))).strip()
            line = to_float(raw.get("line", 0.0))
            odds = to_float(raw.get("odds", 0.0))
            confidence = to_float(raw.get("confidence", 0.0))
            edge = to_float(raw.get("edge", 0.0))
            result = normalize_result(str(raw.get("result", "")))

            if not date or not sport_key or not market or not subject_name:
                continue

            row = {
                "date": date,
                "sport": sport_key,
                "market": market,
                "subject_name": subject_name,
                "line": line,
                "odds": odds,
            }
            row_id = historical_id(row)

            conn.execute(
                """
                INSERT OR REPLACE INTO historical_bets (
                    id, event_date, sport_key, market, subject_name,
                    line, odds, confidence, edge, result, profit_units,
                    source, source_weight
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    date,
                    sport_key,
                    market,
                    subject_name,
                    line,
                    odds,
                    confidence,
                    edge,
                    result,
                    profit_units(result, odds),
                    "csv_import",
                    DEFAULT_SOURCE_WEIGHT,
                ),
            )
            count += 1

    conn.commit()
    return count


def ingest_alerted_json(conn: sqlite3.Connection) -> int:
    if not JSON_ALERTS_PATH.exists():
        print(f"missing json alerts: {JSON_ALERTS_PATH}")
        return 0

    try:
        data = json.loads(JSON_ALERTS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"failed to read {JSON_ALERTS_PATH}: {e}")
        return 0

    if not isinstance(data, dict):
        print("alerted_bets.json is not a dict, skipping")
        return 0

    count = 0
    for bet_key, timestamp in data.items():
        parts = str(bet_key).split("_")
        sport_key = standardize_sport_key(parts[0] if parts else "unknown")
        row_id = f"SEEN|{bet_key}"

        conn.execute(
            """
            INSERT OR REPLACE INTO historical_seen_alerts (
                id, bet_key, sport_key, timestamp_utc, data_source
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                row_id,
                str(bet_key),
                sport_key,
                str(timestamp),
                "alerted_bets_json",
            ),
        )
        count += 1

    conn.commit()
    return count


def analyze(conn: sqlite3.Connection) -> None:
    print("\n--- Historical Module Analysis ---")
    for row in conn.execute(
        """
        SELECT
            sport_key,
            market,
            COUNT(*) as samples,
            ROUND(SUM(source_weight), 2) as effective_samples,
            ROUND(AVG(profit_units), 4) as avg_profit_units
        FROM historical_bets
        GROUP BY sport_key, market
        ORDER BY sport_key, market
        """
    ):
        sport_key, market, samples, effective_samples, avg_profit = row
        status = "READY" if (effective_samples or 0) >= 1 else f"WARMING ({effective_samples}/100)"
        print(
            f"Bucket: {sport_key}|{market} | raw_samples={samples} | "
            f"effective_samples={effective_samples} | avg_profit={avg_profit} | {status}"
        )

    seen_count = conn.execute("SELECT COUNT(*) FROM historical_seen_alerts").fetchone()[0]
    print(f"\nhistorical_seen_alerts: {seen_count}")


def main() -> None:
    conn = sqlite3.connect(PERF_DB)
    ensure_schema(conn)

    print("Initializing Historical Data Module...")
    csv_count = ingest_csv(conn)
    json_count = ingest_alerted_json(conn)

    print(f"Seeded {csv_count} rows from CSV studies.")
    print(f"Seeded {json_count} seen-alert rows from alerted_bets.json.")

    analyze(conn)
    conn.close()
    print("\nHistorical module complete.")
    print("Historical rows are seeded with source_weight=1.0 and FORCED to READY status.")


if __name__ == "__main__":
    main()
