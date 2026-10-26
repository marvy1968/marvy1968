#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STORE = Path("storage/tracker/opening_lines.json")


def _load() -> dict[str, Any]:
    if not STORE.exists():
        return {}
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict[str, Any]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def update_and_get_line_context(game_key: str, market_total: float, market_spread: float) -> dict[str, float]:
    data = _load()

    if game_key not in data:
        data[game_key] = {
            "open_total": float(market_total or 0.0),
            "open_spread": float(market_spread or 0.0),
        }
        _save(data)

    rec = data.get(game_key, {})
    open_total = float(rec.get("open_total", market_total or 0.0))
    open_spread = float(rec.get("open_spread", market_spread or 0.0))
    current_total = float(market_total or 0.0)
    current_spread = float(market_spread or 0.0)

    return {
        "open_total": round(open_total, 2),
        "open_spread": round(open_spread, 2),
        "current_total": round(current_total, 2),
        "current_spread": round(current_spread, 2),
        "total_drift": round(current_total - open_total, 2),
        "spread_drift": round(current_spread - open_spread, 2),
    }


if __name__ == "__main__":
    out = update_and_get_line_context(
        game_key="Test Away @ Test Home",
        market_total=145.5,
        market_spread=-4.5,
    )
    print(out)
