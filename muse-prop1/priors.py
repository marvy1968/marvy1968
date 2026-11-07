"""Muse Prop1 — pregame anchors and player production rates.

The prior mean is a REAL observed pregame quote, frozen only when the quote
timestamp is at or before scheduled start. A live line is never stored under
a pregame label. A bare number left by the old first-seen writer is ignored.

Production rate is player-specific:
  1. priors.yaml per_play / per_min for that player and market, or
  2. that player's own observed pregame line divided by that player's
     opportunity baseline (expected plays, or that player's minutes).
The old season-typical rate table is not a production rate. Market SDs below
are an uncalibrated dispersion assumption, not a fitted calibration and not
a substitute for a player's rate.
"""
from __future__ import annotations
import json
from pathlib import Path

from identity import game_key, normalize_player, parse_time

# Uncalibrated market dispersion only. Not a per-play or per-minute rate.
MARKET_SD = {
    "player_pass_yds": 55.0,
    "player_pass_tds": 0.9,
    "player_rush_yds": 28.0,
    "player_reception_yds": 30.0,
    "player_receptions": 2.2,
    "player_anytime_td": 0.45,
    "player_points": 5.0,
    "player_rebounds": 3.0,
    "player_assists": 2.0,
    "player_threes": 1.2,
    "player_points_rebounds": 6.5,
    "player_points_assists": 6.0,
    "player_rebounds_assists": 3.8,
    "player_points_rebounds_assists": 7.5,
    "player_steals": 1.0,
    "player_blocks": 1.0,
}

# Kept so older imports do not crash. Rates in these tuples are unused.
FOOTBALL_SD = {k: (v, None, None) for k, v in MARKET_SD.items() if k.startswith("player_pass") or k.startswith("player_rush") or k.startswith("player_reception") or k == "player_anytime_td"}
BASKETBALL_SD = {k: (v, None, None) for k, v in MARKET_SD.items() if k not in FOOTBALL_SD}

def load_priors_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml
        return (yaml.safe_load(path.read_text()) or {}).get("priors", {}) or {}
    except Exception:
        return {}

def _player_entry(priors_yaml: dict | None, player: str, market: str) -> dict:
    if not priors_yaml:
        return {}
    block = priors_yaml.get(player)
    if not isinstance(block, dict):
        # Also accept a normalized-name key.
        want = normalize_player(player)
        for name, body in priors_yaml.items():
            if normalize_player(name) == want and isinstance(body, dict):
                block = body
                break
    if not isinstance(block, dict):
        return {}
    entry = block.get(market) or {}
    return entry if isinstance(entry, dict) else {}


def get_prior(sport: str, player: str, market: str, observed_pregame_line: float | None,
              priors_yaml: dict | None = None, opportunities: float | None = None,
              player_minutes: float | None = None) -> dict:
    """Return the player prior, or mean/rate None when it cannot be observed.

    `observed_pregame_line` must already be a real pregame quote. This function
    does not accept a live line and does not fill a generic production rate.
    """
    entry = _player_entry(priors_yaml, player, market)
    sd = MARKET_SD.get(market, 5.0)
    sd_source = "uncalibrated_market_dispersion"
    if entry.get("sd") is not None:
        sd = float(entry["sd"])
        sd_source = "player_prior_file"
    mean = entry.get("mean", observed_pregame_line)
    mean_source = "player_prior_file" if entry.get("mean") is not None else (
        "observed_pregame" if observed_pregame_line is not None else None)
    if mean is not None:
        mean = float(mean)
    minutes = entry.get("minutes", player_minutes)
    if minutes is not None:
        minutes = float(minutes)
    rate = entry.get("per_play", entry.get("per_min"))
    if rate is not None:
        rate = float(rate)
        rate_source = "player_prior_file"
    elif mean is not None and sport in ("ncaab", "ncaaw"):
        if minutes and minutes > 0:
            rate = mean / minutes
            rate_source = "player_pregame_line_over_player_minutes"
        else:
            rate = None
            rate_source = None
    elif mean is not None and opportunities and opportunities > 0:
        rate = mean / float(opportunities)
        rate_source = "player_pregame_line_over_opportunities"
    else:
        rate = None
        rate_source = None
    reason = None
    if mean is None:
        reason = "missing_pregame_anchor"
    elif rate is None:
        reason = "missing_player_rate" if sport in ("nfl", "ncaaf") else "missing_player_minutes"
    return {
        "mean": mean, "sd": sd, "rate": rate, "pregame_minutes": minutes,
        "mean_source": mean_source, "rate_source": rate_source, "sd_source": sd_source,
        "reason": reason, "sport": sport, "market": market,
    }

def anchor_key(sport: str, home: str, away: str, scheduled_start, player: str, market: str) -> str | None:
    gkey = game_key(sport, home, away, scheduled_start)
    who = normalize_player(player)
    if not gkey or not who or not market:
        return None
    return f"{gkey}|{who}|{market}"


def load_first_lines(state_dir: Path) -> dict:
    p = state_dir / "first_lines.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _trusted_record(record) -> dict | None:
    """Accept only an explicit observed-pregame record. Bare floats are legacy."""
    if not isinstance(record, dict):
        return None
    if record.get("source") != "observed_pregame":
        return None
    if record.get("line") is None:
        return None
    observed = parse_time(record.get("observed_at"))
    start = parse_time(record.get("scheduled_start"))
    if observed is None or start is None:
        return None
    # Two minutes of clock skew. Later than that is a live line with a pregame label.
    if (observed - start).total_seconds() > 120:
        return None
    return record


def read_observed_pregame(state_dir: Path, sport: str, home: str, away: str,
                           scheduled_start, player: str, market: str) -> dict | None:
    key = anchor_key(sport, home, away, scheduled_start, player, market)
    if not key:
        return None
    return _trusted_record(load_first_lines(state_dir).get(key))


def freeze_observed_pregame(state_dir: Path, sport: str, home: str, away: str,
                            scheduled_start, player: str, market: str, line: float,
                            observed_at, source_ids: dict | None = None) -> dict | None:
    """Store the first real pregame quote. Never overwrites. Refuses live timestamps.

    Returns the stored record, or None when this observation is not a pregame quote.
    """
    from identity import format_time
    key = anchor_key(sport, home, away, scheduled_start, player, market)
    if not key:
        return None
    observed = parse_time(observed_at)
    start = parse_time(scheduled_start)
    candidate = {
        "line": float(line),
        "source": "observed_pregame",
        "observed_at": format_time(observed),
        "scheduled_start": format_time(start),
        "sport": sport, "home": home, "away": away,
        "player": player, "market": market,
        "source_ids": source_ids or {},
    }
    if _trusted_record(candidate) is None:
        return None
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "first_lines.json"
    data = load_first_lines(state_dir)
    current = _trusted_record(data.get(key))
    if current is None:
        data[key] = candidate
        path.write_text(json.dumps(data, sort_keys=True))
        return candidate
    return current


def freeze_first_line(state_dir: Path, sport: str, game_id: str,
                      player: str, market: str, line: float) -> float | None:
    """Legacy entry point. It no longer treats a live line as a pregame anchor.

    Callers that only have a provider game id cannot prove the quote is pregame.
    Returns None so the runner abstains instead of inventing a label.
    """
    return None
