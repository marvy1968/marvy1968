#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import time
from typing import Any

TEAM_PROPS_LOG_JSONL = os.getenv("TEAM_PROPS_LOG_JSONL", "logs/team_props_bets.jsonl")
TEAM_PROPS_LOG_CSV = os.getenv("TEAM_PROPS_LOG_CSV", "logs/team_props_bets.csv")


def ensure_parent(path: str) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def append_jsonl(path: str, row: dict[str, Any]) -> None:
    ensure_parent(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def append_csv(path: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    ensure_parent(path)
    exists = os.path.exists(path)

    fieldnames = [
        "timestamp",
        "game",
        "home_team",
        "away_team",
        "market",
        "team",
        "side",
        "line",
        "odds",
        "projected_final_raw",
        "projected_final_calibrated",
        "model_prob",
        "implied_prob",
        "edge",
        "confidence",
        "kelly_quarter",
        "strength",
        "suspicious",
        "phase",
        "pace_bucket",
        "one_possession_game",
        "blowout_flag",
        "summary",
    ]

    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        if not exists:
            writer.writeheader()

        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def log_team_props(team_props_output: dict[str, Any]) -> dict[str, Any]:
    home_team = team_props_output.get("home_team")
    away_team = team_props_output.get("away_team")
    game = f"{away_team} @ {home_team}"
    timestamp = int(time.time())

    logged_rows: list[dict[str, Any]] = []

    for prop in team_props_output.get("best_team_props", []):
        row = {
            "timestamp": timestamp,
            "game": game,
            "home_team": home_team,
            "away_team": away_team,
            "market": prop.get("market", "team_total"),
            "team": prop.get("team"),
            "side": prop.get("side"),
            "line": prop.get("line"),
            "odds": prop.get("odds"),
            "projected_final_raw": prop.get("projected_final_raw"),
            "projected_final_calibrated": prop.get("projected_final_calibrated"),
            "model_prob": prop.get("model_prob"),
            "implied_prob": prop.get("implied_prob"),
            "edge": prop.get("edge"),
            "confidence": prop.get("confidence"),
            "kelly_quarter": prop.get("kelly_quarter"),
            "strength": prop.get("strength"),
            "suspicious": prop.get("suspicious", False),
            "phase": prop.get("phase"),
            "pace_bucket": prop.get("pace_bucket"),
            "one_possession_game": prop.get("one_possession_game"),
            "blowout_flag": prop.get("blowout_flag"),
            "summary": prop.get("summary"),
        }

        append_jsonl(TEAM_PROPS_LOG_JSONL, row)
        logged_rows.append(row)

    append_csv(TEAM_PROPS_LOG_CSV, logged_rows)

    return {
        "rows_logged": len(logged_rows),
        "jsonl_path": TEAM_PROPS_LOG_JSONL,
        "csv_path": TEAM_PROPS_LOG_CSV,
    }


if __name__ == "__main__":
    sample = {
        "home_team": "Duke",
        "away_team": "Auburn",
        "best_team_props": [
            {
                "market": "team_total",
                "team": "Auburn",
                "side": "over",
                "line": 53.5,
                "odds": -110,
                "projected_final_raw": 55.57,
                "projected_final_calibrated": 55.57,
                "model_prob": 0.7654,
                "implied_prob": 0.5238,
                "edge": 0.2416,
                "confidence": 90.0,
                "kelly_quarter": 0.07,
                "strength": "A",
                "suspicious": False,
                "phase": "2h",
                "pace_bucket": "normal",
                "one_possession_game": False,
                "blowout_flag": False,
                "summary": "Auburn team total over 53.5 | proj=55.57 | edge=0.2416 | conf=90.0% | suspicious=False | strength=A",
            },
            {
                "market": "team_total",
                "team": "Duke",
                "side": "over",
                "line": 59.5,
                "odds": -110,
                "projected_final_raw": 60.98,
                "projected_final_calibrated": 60.98,
                "model_prob": 0.6897,
                "implied_prob": 0.5238,
                "edge": 0.1659,
                "confidence": 86.6,
                "kelly_quarter": 0.07,
                "strength": "A",
                "suspicious": False,
                "phase": "2h",
                "pace_bucket": "normal",
                "one_possession_game": False,
                "blowout_flag": False,
                "summary": "Duke team total over 59.5 | proj=60.98 | edge=0.1659 | conf=86.6% | suspicious=False | strength=A",
            },
        ],
    }

    result = log_team_props(sample)

    print("TEAM PROPS LOGGER")
    print("rows_logged:", result["rows_logged"])
    print("jsonl_path:", result["jsonl_path"])
    print("csv_path:", result["csv_path"])
