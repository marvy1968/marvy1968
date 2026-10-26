#!/usr/bin/env python3
from __future__ import annotations

from typing import Any


def american_to_implied_prob(odds: Any) -> float:
    try:
        x = float(odds)
    except Exception:
        return 0.0

    if x == 0:
        return 0.0
    if x > 0:
        return 100.0 / (x + 100.0)
    return abs(x) / (abs(x) + 100.0)


def _first_present(d: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for k in keys:
        if k in d and d.get(k) not in (None, ""):
            return d.get(k)
    return default


def extract_market_context(payload: dict[str, Any]) -> dict[str, float]:
    home_ml = _first_present(
        payload,
        ["home_ml", "home_moneyline", "home_ml_odds", "home_h2h", "home_price"],
        0.0,
    )
    away_ml = _first_present(
        payload,
        ["away_ml", "away_moneyline", "away_ml_odds", "away_h2h", "away_price"],
        0.0,
    )

    open_total = _first_present(payload, ["open_total", "opening_total"], 0.0)
    open_spread = _first_present(payload, ["open_spread", "opening_spread", "open_home_spread"], 0.0)
    current_total = _first_present(payload, ["market_total", "total"], 0.0)
    current_spread = _first_present(payload, ["home_spread_line", "market_spread", "spread"], 0.0)

    home_ml = float(home_ml or 0.0)
    away_ml = float(away_ml or 0.0)
    open_total = float(open_total or 0.0)
    open_spread = float(open_spread or 0.0)
    current_total = float(current_total or 0.0)
    current_spread = float(current_spread or 0.0)

    return {
        "home_ml_odds": home_ml,
        "away_ml_odds": away_ml,
        "home_ml_implied": round(american_to_implied_prob(home_ml), 4),
        "away_ml_implied": round(american_to_implied_prob(away_ml), 4),
        "open_total": open_total,
        "open_spread": open_spread,
        "current_total": current_total,
        "current_spread": current_spread,
        "total_drift": round(current_total - open_total, 2) if open_total else 0.0,
        "spread_drift": round(current_spread - open_spread, 2) if open_spread else 0.0,
    }


if __name__ == "__main__":
    sample = {
        "home_ml": -145,
        "away_ml": 125,
        "market_total": 145.5,
        "home_spread_line": -4.5,
        "open_total": 143.5,
        "open_spread": -3.5,
    }
    print(extract_market_context(sample))
