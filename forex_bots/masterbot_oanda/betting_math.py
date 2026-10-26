#!/usr/bin/env python3
from typing import Dict, Any

def american_to_decimal(odds: float) -> float:
    """Convert American odds to decimal odds."""
    if odds == 0:
        raise ValueError("Odds cannot be zero")
    if odds > 0:
        return 1 + (odds / 100)
    return 1 + (100 / abs(odds))

def implied_probability(odds: float) -> float:
    """Implied probability from American odds (includes vig)."""
    if odds == 0:
        raise ValueError("Odds cannot be zero")
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)

def calculate_edge(model_prob: float, odds: float) -> float:
    if not 0 <= model_prob <= 1:
        raise ValueError("model_prob must be between 0 and 1")
    return model_prob - implied_probability(odds)

def calculate_ev(model_prob: float, odds: float) -> float:
    if not 0 <= model_prob <= 1:
        raise ValueError("model_prob must be between 0 and 1")
    decimal = american_to_decimal(odds)
    return (model_prob * (decimal - 1)) - (1 - model_prob)

def calculate_kelly(model_prob: float, odds: float, fraction: float = 0.25) -> float:
    if not 0 <= model_prob <= 1 or odds == 0:
        return 0.0
    b = american_to_decimal(odds) - 1
    if b <= 0:
        return 0.0
    kelly = (b * model_prob - (1 - model_prob)) / b
    if kelly <= 0:
        return 0.0
    return round(kelly * fraction, 4)

def enrich_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    prob = float(signal.get("model_prob", 0))
    odds = float(signal.get("odds", 0))
    edge = calculate_edge(prob, odds)
    ev = calculate_ev(prob, odds)
    kelly_quarter = calculate_kelly(prob, odds, 0.25)
    signal.update({
        "edge": round(edge * 100, 2),
        "ev_percent": round(ev * 100, 2),
        "kelly_quarter": round(kelly_quarter * 100, 2)
    })
    return signal
