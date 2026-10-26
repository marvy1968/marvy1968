#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TRACK_FILE = Path("storage/tracker/alerts.jsonl")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float | None = None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _extract_odds_fields(alert: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """
    Preserve any odds fields already attached to the alert.
    Fall back to payload-level fields when present.
    """
    out: dict[str, Any] = {}

    candidate_fields = [
        "odds",
        "market_odds",
        "price",
        "american_odds",
        "book_odds",
        "bookmaker",
        "market_line",
        "system_value",
        "market_value",
    ]

    for key in candidate_fields:
        if key in alert and alert.get(key) not in (None, ""):
            out[key] = alert.get(key)
        elif key in payload and payload.get(key) not in (None, ""):
            out[key] = payload.get(key)

    # keep common market context if available
    for key in [
        "home_spread_line",
        "market_total",
        "home_ml",
        "away_ml",
    ]:
        if key in payload and payload.get(key) not in (None, ""):
            out[key] = payload.get(key)

    return out


def record_alert(payload: dict[str, Any], alert: dict[str, Any], phase: str = "pregame") -> None:
    TRACK_FILE.parent.mkdir(parents=True, exist_ok=True)

    row: dict[str, Any] = {
        "ts_utc": _utc_now(),
        "phase": phase,
        "sport_key": alert.get("sport_key") or payload.get("sport_key"),
        "game": alert.get("game") or payload.get("game") or f"{payload.get('away_team', '')} @ {payload.get('home_team', '')}".strip(),
        "market": alert.get("market"),
        "selection": alert.get("selection"),
        "edge": _safe_float(alert.get("edge"), 0.0),
        "confidence": _safe_float(alert.get("confidence"), 0.0),
        "tier": alert.get("tier", ""),
        "source": alert.get("source", ""),
        "recommended_units": alert.get("recommended_units"),
        "capped_units": alert.get("capped_units"),
        "bet_size": alert.get("bet_size"),
        "kelly_quarter": alert.get("kelly_quarter"),
        "kelly": alert.get("kelly"),
        "aj_edge": bool(alert.get("aj_edge", False)),
        "aj_tier": alert.get("aj_tier", ""),
        "aj_units": _safe_float(alert.get("aj_units"), None),
        "aj_time_remaining": _safe_float(alert.get("aj_time_remaining"), None),
        "aj_score_diff": _safe_float(alert.get("aj_score_diff"), None),
        "result": "pending",
        "profit_units": 0.0,
    }

    # store teams when we can
    if payload.get("home_team"):
        row["home_team"] = payload.get("home_team")
    if payload.get("away_team"):
        row["away_team"] = payload.get("away_team")

    # merge odds and market fields
    row.update(_extract_odds_fields(alert, payload))

    with TRACK_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    sample_payload = {
        "sport_key": "basketball_nba",
        "away_team": "Chicago Bulls",
        "home_team": "Philadelphia 76ers",
        "market_total": 238.5,
        "home_spread_line": -14.5,
        "home_ml": -900,
        "away_ml": 600,
    }
    sample_alert = {
        "sport_key": "basketball_nba",
        "game": "Chicago Bulls @ Philadelphia 76ers",
        "market": "total",
        "selection": "under",
        "edge": 7.4,
        "confidence": 82.5,
        "tier": "B",
        "odds": -108,
        "bookmaker": "draftkings",
        "market_value": 238.5,
        "system_value": 220.5,
    }
    record_alert(sample_payload, sample_alert, phase="live")
    print("sample alert recorded")
