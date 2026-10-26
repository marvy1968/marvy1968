#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import Any, Dict

AJ_EDGE_CSV = "logs/aj_edge_tracker.csv"
AJ_EDGE_JSONL = "logs/aj_edge_tracker.jsonl"


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


def build_aj_edge_record(signal: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "timestamp_utc": datetime.utcnow().isoformat(),
        "game": signal.get("game", ""),
        "market": signal.get("market", ""),
        "selection": signal.get("selection", ""),
        "bot_win_pct": round(_to_float(signal.get("bot_win_pct", 0.0)), 2),
        "book_win_pct": round(_to_float(signal.get("book_win_pct", 0.0)), 2),
        "display_edge": round(_to_float(signal.get("display_edge", 0.0)), 2),
        "ev_percent": round(_to_float(signal.get("ev_percent", 0.0)), 2),
        "confidence": round(_to_float(signal.get("confidence", 0.0)), 4),
        "kelly_quarter": round(_to_float(signal.get("kelly_quarter", 0.0)), 4),
        "leader": signal.get("leader", ""),
        "leader_win_prob": round(_to_float(signal.get("leader_win_prob", 0.0)), 4),
        "minutes_left": _to_float(signal.get("minutes_left", 0.0)),
        "seconds_left": _to_float(signal.get("seconds_left", 0.0)),
        "score_diff": _to_float(signal.get("score_diff", 0.0)),
        "pace": signal.get("pace", ""),
        "pressure": signal.get("pressure", ""),
        "suspicious": bool(signal.get("suspicious", False)),
        "recommended_bankroll_pct": 2.0,
    }


def track_aj_edge(signal: Dict[str, Any]) -> None:
    record = build_aj_edge_record(signal)

    _ensure_parent(AJ_EDGE_JSONL)
    with open(AJ_EDGE_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    _ensure_parent(AJ_EDGE_CSV)
    file_exists = os.path.exists(AJ_EDGE_CSV)
    with open(AJ_EDGE_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(record.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(record)


if __name__ == "__main__":
    sample = {
        "game": "NC State Wolfpack @ Michigan Wolverines",
        "market": "moneyline",
        "selection": "Michigan Wolverines",
        "bot_win_pct": 88.5,
        "book_win_pct": 61.2,
        "display_edge": 27.3,
        "ev_percent": 36.4,
        "confidence": 0.885,
        "kelly_quarter": 0.06,
        "leader": "Michigan Wolverines",
        "leader_win_prob": 0.885,
        "minutes_left": 4,
        "seconds_left": 20,
        "score_diff": 14,
        "pace": "normal",
        "pressure": "normal",
        "suspicious": False,
    }

    track_aj_edge(sample)
    print("AJ EDGE TRACKER TEST OK")
