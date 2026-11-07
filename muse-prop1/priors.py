"""Muse Prop1 — pregame priors.
v1 rule: the prior mean is the FIRST-SEEN Bovada line for that player/market,
frozen in state/first_lines.json at the first poll (before/at kickoff).
That is a real market prior, not a guess, and it avoids needing nflverse /
CollegeFootballData / Torvik wired up before the first run.

SD and per-play / per-minute rates come from the tables below (season-typical),
overridable per player in priors.yaml (optional file, same folder):
  priors:
    "Josh Allen":
      player_pass_yds: {mean: 245.0, sd: 55.0, per_play: 3.8}
If priors.yaml is absent, the tables below are used for every player.
SD is the honest part — if calibration fails in backtest, SD is the first
thing to re-fit, not the edge threshold.
"""
from __future__ import annotations
import json
from pathlib import Path

# (sd, per_play_or_per_min, pregame_minutes_or_None)
FOOTBALL_SD = {
    "player_pass_yds": (55.0, 3.8, None),
    "player_pass_tds": (0.9, 0.03, None),
    "player_rush_yds": (28.0, 1.1, None),
    "player_reception_yds": (30.0, 1.0, None),
    "player_receptions": (2.2, 0.08, None),
    "player_anytime_td": (0.45, 0.012, None),
}
BASKETBALL_SD = {
    "player_points": (5.0, 0.62, 30.0),
    "player_rebounds": (3.0, 0.28, 30.0),
    "player_assists": (2.0, 0.18, 30.0),
    "player_threes": (1.2, 0.08, 30.0),
    "player_points_rebounds": (6.5, 0.90, 30.0),
    "player_points_assists": (6.0, 0.80, 30.0),
    "player_rebounds_assists": (3.8, 0.46, 30.0),
    "player_points_rebounds_assists": (7.5, 1.08, 30.0),
    "player_steals": (1.0, 0.05, 30.0),
    "player_blocks": (1.0, 0.05, 30.0),
}

def load_priors_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml
        return (yaml.safe_load(path.read_text()) or {}).get("priors", {}) or {}
    except Exception:
        return {}

def get_prior(sport: str, player: str, market: str, first_line: float | None,
              priors_yaml: dict | None = None) -> dict:
    """Return {mean, sd, per_play/per_min, pregame_minutes}.
    Mean = priors.yaml override, else first-seen Bovada line, else None (skip)."""
    table = FOOTBALL_SD if sport in ("nfl", "ncaaf") else BASKETBALL_SD
    sd, rate, mins = table.get(market, (5.0, 0.5, 30.0))
    mean = None
    if priors_yaml and player in priors_yaml and market in priors_yaml[player]:
        entry = priors_yaml[player][market] or {}
        mean = entry.get("mean", first_line)
        sd = entry.get("sd", sd)
        rate = entry.get("per_play", entry.get("per_min", rate))
        mins = entry.get("minutes", mins)
    else:
        mean = first_line
    return {"mean": mean, "sd": sd, "rate": rate, "pregame_minutes": mins}

def load_first_lines(state_dir: Path) -> dict:
    p = state_dir / "first_lines.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}

def freeze_first_line(state_dir: Path, sport: str, game_id: str,
                      player: str, market: str, line: float) -> float:
    """Store the first-seen line for (sport, game, player, market), return it.
    Never overwrites — the prior is frozen at first sight."""
    state_dir.mkdir(parents=True, exist_ok=True)
    p = state_dir / "first_lines.json"
    data = load_first_lines(state_dir)
    key = f"{sport}|{game_id}|{player}|{market}"
    if key not in data:
        data[key] = line
        p.write_text(json.dumps(data))
    return data[key]
